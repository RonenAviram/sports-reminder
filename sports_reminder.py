#!/usr/bin/env python3
"""
Sports Reminder - Daily email for today's matches
Usage:
  python3 sports_reminder.py             # dry-run: show matches, no email
  python3 sports_reminder.py --send      # send email if there are matches
  python3 sports_reminder.py --test      # send a test email regardless
"""

import sys
import json
import datetime
import unicodedata
import urllib.request
import urllib.parse
import os
import xml.etree.ElementTree as ET

from email_sender import send_raw_email, GMAIL_SENDER, GMAIL_APP_PASSWORD
from player_stats import send_player_stats_emails

# DST-aware timezone support (zoneinfo is stdlib since Python 3.9)
try:
    from zoneinfo import ZoneInfo
    _ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
    _BERLIN_TZ  = ZoneInfo("Europe/Berlin")   # EuroLeague uses CET/CEST
    _HAS_ZONEINFO = True
except Exception:
    _HAS_ZONEINFO = False

import calendar as _calendar

def _last_weekday(year: int, month: int, weekday: int) -> int:
    """Return the day-of-month of the last occurrence of weekday (0=Mon..6=Sun) in month."""
    last = _calendar.monthrange(year, month)[1]
    return max(d for d in range(last, last - 7, -1)
               if datetime.date(year, month, d).weekday() == weekday)

def _israel_utc_offset_h(at_utc: datetime.datetime) -> int:
    """Israel's UTC offset at a given UTC moment: +3 (IDT, summer) or +2 (IST, winter).
    DST rule: starts last Friday of March 02:00 IL (= 00:00 UTC), ends last Sunday of Oct 01:00 UTC."""
    if _HAS_ZONEINFO:
        aware = at_utc.replace(tzinfo=datetime.timezone.utc).astimezone(_ISRAEL_TZ)
        return int(aware.utcoffset().total_seconds() // 3600)
    y = at_utc.year
    dst_start = datetime.datetime(y, 3, _last_weekday(y, 3, 4), 0, 0)   # Fri→00:00 UTC
    dst_end   = datetime.datetime(y, 10, _last_weekday(y, 10, 6), 1, 0)  # Sun→01:00 UTC
    return 3 if dst_start <= at_utc < dst_end else 2

def _berlin_utc_offset_h(at_utc: datetime.datetime) -> int:
    """Europe/Berlin UTC offset at a given UTC moment: +2 (CEST, summer) or +1 (CET, winter).
    CEST starts last Sunday of March 01:00 UTC, ends last Sunday of Oct 01:00 UTC."""
    if _HAS_ZONEINFO:
        aware = at_utc.replace(tzinfo=datetime.timezone.utc).astimezone(_BERLIN_TZ)
        return int(aware.utcoffset().total_seconds() // 3600)
    y = at_utc.year
    cest_start = datetime.datetime(y, 3, _last_weekday(y, 3, 6), 1, 0)   # Sun→01:00 UTC
    cest_end   = datetime.datetime(y, 10, _last_weekday(y, 10, 6), 1, 0)  # Sun→01:00 UTC
    return 2 if cest_start <= at_utc < cest_end else 1

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edit these before first run
# ─────────────────────────────────────────────────────────────────────────────
FIREBASE_PROJECT   = "sports-reminder-55578"
FIREBASE_API_KEY   = "AIzaSyCd3C1_XN69r8lWUBYPndoGFxmDjnsjX1E"
USERS_COLLECTION   = "users"          # multi-user: users/{uid}
GLOBAL_CONFIG_PATH = "config/global"  # global flags (world_cup_mode etc.)

TIMEZONE_OFFSET    = 3    # Israel (UTC+3)

# ─────────────────────────────────────────────────────────────────────────────
# PLAYER WATCH — stats for specific players, shown in the morning email
# Each entry: display_name, espn_id, team_id (ESPN), team_name, league_id
# ─────────────────────────────────────────────────────────────────────────────
PLAYER_WATCH = [
    {
        "display_name": "Deni Avdija",
        "espn_id":      "4683021",
        "team_id":      "22",           # Portland Trail Blazers
        "team_name":    "Portland Trail Blazers",
        "league_id":    "nba",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# ESPN ENDPOINTS  (league_id → URL)
# ─────────────────────────────────────────────────────────────────────────────
ESPN_ENDPOINTS = {
    "premier_league":       "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "la_liga":              "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "bundesliga":           "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard",
    "serie_a":              "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "ligue_1":              "https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/scoreboard",
    "champions_league":     "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard",
    "europa_league":        "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/scoreboard",
    "israeli_pl_soccer":    "https://site.api.espn.com/apis/site/v2/sports/soccer/isr.1/scoreboard",
    "mls":                  "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
    "fifa_world_cup":       "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
    "nba":                  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "euroleague":            None,    # uses EuroLeague official API (see below)
    "eurocup":               None,    # uses EuroCup official API (see below)
    "israeli_pl_basketball": None,    # uses TheSportsDB (ESPN returns empty for isr.1 basketball)
}

# ─────────────────────────────────────────────────────────────────────────────
# THESPORTSDB — Israeli leagues (ESPN isr.1 returns only partial team list)
# Free key "3" covers eventsday + eventsseason.
# Basketball ID=4474, Soccer ID=4644 (Israeli Premier League / Ligat HaAl)
# ─────────────────────────────────────────────────────────────────────────────
TSDB_LEAGUES = {
    "israeli_pl_basketball": "Israeli Basketball Premier League",
    "israeli_pl_soccer":     "Israeli Premier League",
}
TSDB_LEAGUE_IDS = {
    "israeli_pl_basketball": "4474",
    "israeli_pl_soccer":     "4644",
}
TSDB_SEASON = "2025-2026"
TSDB_FREE_KEY = "3"

# ─────────────────────────────────────────────────────────────────────────────
# EUROLEAGUE / EUROCUP OFFICIAL API
# ESPN dropped these — use api-live.euroleague.net instead
# Competition codes: E = EuroLeague, U = EuroCup
# Season codes: E2025 = 2025-26 EuroLeague, U2025 = 2025-26 EuroCup
# ─────────────────────────────────────────────────────────────────────────────
EUROLEAGUE_COMPETITION_CODES = {
    "euroleague": ("E", "E2025"),
    "eurocup":    ("U", "U2025"),
}

# ─────────────────────────────────────────────────────────────────────────────
# TEAM NAME MATCHING
# Three-layer approach:
#   1. NOISE_TOKENS  — strip known sponsor words before comparing
#   2. Word-coverage — all words of user's name appear in API name (multi-word)
#   3. ALIASES       — last resort for abbreviations that can't be solved algorithmically
# ─────────────────────────────────────────────────────────────────────────────

# Sponsor / filler words that APIs inject into team names.
# These are NEVER part of a team's actual identity — safe to ignore.
NOISE_TOKENS = {
    # EuroLeague / EuroCup jersey sponsors (updated each season as needed)
    "rapyd",        # Maccabi Rapyd Tel Aviv
    "ibi",          # Hapoel IBI Tel Aviv
    "beko",         # Fenerbahce Beko
    "aktor",        # Panathinaikos Aktor
    "mozzart",      # Partizan Mozzart Bet
    "bet",          # Partizan Mozzart Bet  (also covers "1xbet", etc.)
    "ea7",          # EA7 Emporio Armani Milan
    "emporio",      # EA7 Emporio Armani Milan
    "armani",       # EA7 Emporio Armani Milan
    "ldlc",         # LDLC ASVEL Villeurbanne
    "segafredo",    # Virtus Segafredo Bologna
    "meridianbet",  # Crvena Zvezda Meridianbet
    "betano",       # various soccer clubs
    "ewc",          # Efes branding variants
    "baxi",         # Baxi Manresa (EuroCup)
    "cosea",        # Cosea JL Bourg-en-Bresse (EuroCup)
    "midtown",      # Hapoel Midtown Jerusalem (EuroCup)
    "gain",         # Besiktas Gain Istanbul (EuroCup)
    "betsson",      # Aris Thessaloniki Betsson (EuroCup)
    "cosmorama",    # Panionios Cosmorama (EuroCup)
    "voli",         # Buducnost Voli (EuroCup)
    # Soccer jersey sponsors occasionally appended to team names
    "365",          # bet365 variations
    "ticketportal",
}

# Hard-coded aliases: only for cases the algorithm can't solve
# (abbreviations, historic name changes, etc.)
TEAM_ALIASES = {
    # ESPN soccer abbreviations
    "Man City":                    "Manchester City",
    "Man United":                  "Manchester United",
    "Spurs":                       "Tottenham Hotspur",
    "Nottm Forest":                "Nottingham Forest",
    "Inter":                       "Inter Milan",
    # ESPN drops/changes prefixes or uses short name
    "Barcelona":                   "FC Barcelona",
    "Porto":                       "FC Porto",
    "Bologna":                     "Bologna FC",
    "Milan":                       "AC Milan",
    "Lazio":                       "SS Lazio",
    "Atletico Madrid":             "Atletico Madrid",
    "Atlético Madrid":             "Atletico Madrid",
    "Hoffenheim":                  "TSG Hoffenheim",
    "RB Leipzig":                  "RB Leipzig",
    "Köln":                        "FC Koln",
    "Koln":                        "FC Koln",
    "Valencia":                    "Valencia CF",
    "Sevilla":                     "Sevilla FC",
    "Espanol":                     "RCD Espanyol",
    "Espanyol":                    "RCD Espanyol",
    "Osasuna":                     "CA Osasuna",
    "Alaves":                      "Deportivo Alaves",
    "Alavés":                      "Deportivo Alaves",
    "Paris Saint-Germain":         "Paris Saint-Germain",  # identity, resolves accent issues
    "PSG":                         "Paris Saint-Germain",
    "Lens":                        "Lens",                 # identity
    "Rennes":                      "Rennes",
    "Brentford":                   "Brentford",
    "Bournemouth":                 "Bournemouth",
    # Israeli teams — ESPN uses hyphens / apostrophes
    "Maccabi Tel-Aviv":            "Maccabi Tel Aviv",
    "Hapoel Tel-Aviv":             "Hapoel Tel Aviv",
    "Hapoel Be'er":                "Hapoel Beer Sheva",    # ESPN truncates
    "Hapoel Be'er Sheva":          "Hapoel Beer Sheva",
    "Hapoel Beer-Sheva":           "Hapoel Beer Sheva",
    "Bnei Yehuda Tel-Aviv":        "Bnei Yehuda",
    "Ironi Kiryat-Shmona":         "Ironi Kiryat Shmona",
    "Hapoel Jerusalem":            "Hapoel Jerusalem",
    "Bnei Sakhnin":                "Bnei Sakhnin",
    "Hapoel Hadera":               "Hapoel Hadera",
    "Maccabi Petah-Tikva":         "Maccabi Petah Tikva",
    "Hapoel Raanana":              "Hapoel Raanana",
    "Maccabi Bnei Raina":          "Maccabi Bnei Raina",
    # EuroLeague / EuroCup
    "Zalgiris Kaunas":             "Zalgiris",
    "Crvena zvezda":               "Crvena Zvezda",
    "AS Monaco":                   "Monaco Basket",        # EuroLeague basketball
    "AS MONACO":                   "Monaco Basket",        # uppercase variant from API
    "EA7 Emporio Armani Milan":    "Olimpia Milano",       # full sponsor name → common name
    "EA7 EMPORIO ARMANI MILAN":    "Olimpia Milano",       # uppercase variant from API
    "Armani Milan":                "Olimpia Milano",
    "Olimpia Milano":              "Olimpia Milano",       # identity
    "Baskonia Vitoria-Gasteiz":    "Baskonia",
    "LDLC ASVEL VILLEURBANNE":     "ASVEL",                # EuroCup — ASVEL is 5 chars, below threshold
    "LDLC ASVEL Villeurbanne":     "ASVEL",
    # ESPN uses Italian name for Inter
    "Internazionale":              "Inter Milan",
    "FC Internazionale":           "Inter Milan",
    "FC Internazionale Milano":    "Inter Milan",
    # Rennes — ESPN uses full French name
    "Stade Rennais":               "Rennes",
    "Stade Rennais FC":            "Rennes",
    # MLS abbreviation
    "LAFC":                        "Los Angeles FC",
    # Red Bull Salzburg — ESPN sometimes uses RB abbreviation
    "RB Salzburg":                 "Red Bull Salzburg",
    "FC Red Bull Salzburg":        "Red Bull Salzburg",
    # Champions League / Europa League — ESPN sometimes uses shorter names
    "Real Madrid CF":              "Real Madrid",
    "Inter Milan":                 "Inter Milan",
    "Borussia Dortmund":           "Borussia Dortmund",
    "Sporting CP":                 "Sporting CP",
    "Slavia Prague":               "Slavia Prague",
    "FC Copenhagen":               "FC Copenhagen",
    "Bodo/Glimt":                  "Bodo/Glimt",
    "Union Saint-Gilloise":        "Union Saint-Gilloise",
    "Pafos FC":                    "Pafos FC",
    "Qarabag":                     "Qarabag",
    "Kairat Almaty":               "Kairat Almaty",
    "Villarreal CF":               "Villarreal CF",
    "Red Bull Salzburg":           "Red Bull Salzburg",
    "Eintracht Frankfurt":         "Eintracht Frankfurt",
    "Fenerbahce":                  "Fenerbahce",           # Europa League (no Beko)
    # NBA — "LA" abbreviation for Los Angeles teams
    "Los Angeles Lakers":          "LA Lakers",
    "Los Angeles Clippers":        "LA Clippers",
    # MLS
    "Los Angeles FC":              "Los Angeles FC",
    "Nashville SC":                "Nashville SC",
}

# ─────────────────────────────────────────────────────────────────────────────
# FIFA WORLD CUP 2026 — Emoji flags for national teams
# ESPN abbreviation → ISO 3166-1 alpha-2 (used to build emoji flag)
# ─────────────────────────────────────────────────────────────────────────────
_ESPN_ABBR_TO_ISO2 = {
    "MEX": "MX", "RSA": "ZA", "KOR": "KR", "CZE": "CZ", "CAN": "CA",
    "BIH": "BA", "USA": "US", "PAR": "PY", "QAT": "QA", "SUI": "CH",
    "BRA": "BR", "MAR": "MA", "HAI": "HT", "SCO": "GB",
    "AUS": "AU", "TUR": "TR", "GER": "DE", "CUW": "CW", "NED": "NL",
    "JPN": "JP", "CIV": "CI", "ECU": "EC", "SWE": "SE", "TUN": "TN",
    "ESP": "ES", "CPV": "CV", "BEL": "BE", "EGY": "EG", "KSA": "SA",
    "URU": "UY", "IRN": "IR", "NZL": "NZ", "FRA": "FR", "SEN": "SN",
    "IRQ": "IQ", "NOR": "NO", "ARG": "AR", "ALG": "DZ", "AUT": "AT",
    "JOR": "JO", "POR": "PT", "COD": "CD", "ENG": "GB",
    "CRO": "HR", "GHA": "GH", "PAN": "PA", "UZB": "UZ", "COL": "CO",
}

_SPECIAL_FLAGS = {
    "ENG": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "SCO": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
}

def _country_flag_emoji(espn_abbr: str) -> str:
    """Convert ESPN team abbreviation to emoji flag."""
    if espn_abbr in _SPECIAL_FLAGS:
        return _SPECIAL_FLAGS[espn_abbr]
    iso2 = _ESPN_ABBR_TO_ISO2.get(espn_abbr, "")
    if len(iso2) == 2:
        return chr(0x1F1E6 + ord(iso2[0]) - ord('A')) + chr(0x1F1E6 + ord(iso2[1]) - ord('A'))
    return ""

def _team_display_with_flag(team_name: str, espn_abbr: str) -> str:
    """Return 'flag name' or just 'name' if no flag found."""
    flag = _country_flag_emoji(espn_abbr)
    return f"{flag} {team_name}" if flag else team_name


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def strip_accents(s: str) -> str:
    """Remove accents: Atlético → Atletico"""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def normalize_name(name: str) -> str:
    """Lowercase, strip accents, replace hyphens, remove common prefixes."""
    name = strip_accents(name).lower().strip()
    name = name.replace("-", " ").replace("'", "")
    for prefix in ["fc ", "afc ", "as ", "rc ", "ac ", "sc ", "vfb ", "vfl ", "fsv "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    for suffix in [" fc", " sc", " ac", " bc", " b.c."]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()

def strip_noise(name: str) -> str:
    """Remove sponsor/noise tokens from a normalized name."""
    return " ".join(
        w for w in normalize_name(name).split()
        if w not in NOISE_TOKENS
    )

def names_match(api_name: str, our_name: str) -> bool:
    """
    Match an API team name against the user's stored name.
    Layers (first match wins):
      1. Alias table  — handles abbreviations (Man City → Manchester City)
      2. Exact norm   — handles accents, FC/AS prefixes
      3. Word-subset  — handles sponsor insertions (Maccabi Rapyd Tel Aviv → Maccabi Tel Aviv)
      4. Noise-strip + word-subset  — handles sponsor at start/end for short names
      5. Noise-strip + single-word  — "Panathinaikos" matches "Panathinaikos Aktor Athens"
    """
    # 1. Alias table (case-insensitive key lookup)
    resolved = TEAM_ALIASES.get(api_name) or TEAM_ALIASES.get(api_name.title()) or api_name
    if resolved.lower() == our_name.lower():
        return True
    # Also try: user stored name might itself be an alias key mapping to the canonical
    resolved2 = TEAM_ALIASES.get(our_name) or TEAM_ALIASES.get(our_name.title())
    if resolved2 and resolved2.lower() == api_name.lower():
        return True

    norm_api = normalize_name(api_name)
    norm_our = normalize_name(our_name)

    # 2. Exact normalized match
    if norm_api == norm_our:
        return True

    # 3. Word-subset: all words of user's name are present in API name
    our_words = set(norm_our.split())
    api_words = set(norm_api.split())
    if len(our_words) >= 2 and our_words.issubset(api_words):
        return True

    # 4. Noise-stripped word-subset: strip sponsors, then re-check
    clean_api = strip_noise(api_name)
    clean_our = strip_noise(our_name)
    clean_our_words = set(clean_our.split())
    clean_api_words = set(clean_api.split())

    if clean_api == clean_our:
        return True
    if len(clean_our_words) >= 2 and clean_our_words.issubset(clean_api_words):
        return True

    # 5. Single significant word after noise stripping:
    #    user saves "Panathinaikos", API says "Panathinaikos Aktor Athens"
    #    → after stripping "aktor": "panathinaikos athens"
    #    → "panathinaikos" is the FIRST word → match
    #    Require ≥6 chars to avoid false positives on city names like "Milan"
    clean_api_list = clean_api.split()
    if (len(clean_our_words) == 1
            and clean_api_list
            and len(list(clean_our_words)[0]) >= 7
            and list(clean_our_words)[0] == clean_api_list[0]):
        return True

    return False

def fetch_json(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
        "Referer": "https://www.espn.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def today_israel() -> str:
    """Return today's date in Israel as YYYY-MM-DD."""
    utc_now = datetime.datetime.utcnow()
    israel_now = utc_now + datetime.timedelta(hours=_israel_utc_offset_h(utc_now))
    return israel_now.strftime("%Y-%m-%d")

def now_israel_time() -> str:
    """Return current time in Israel as HH:MM."""
    utc_now = datetime.datetime.utcnow()
    israel_now = utc_now + datetime.timedelta(hours=_israel_utc_offset_h(utc_now))
    return israel_now.strftime("%H:%M")


def _compute_display_date(il_date: str, time_str: str) -> str:
    """Games between 00:00-04:59 Israel time belong to the previous evening.
    Returns il_date - 1 day for those games, otherwise il_date unchanged.
    This ensures e.g. a 00:00 IL game on June 23 displays under June 22."""
    if time_str == "TBD":
        return il_date
    try:
        h = int(time_str.split(":")[0])
        if 0 <= h < 5:
            dt = datetime.datetime.strptime(il_date, "%Y-%m-%d")
            return (dt - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return il_date

def _format_series_summary(series_summary: str, il_date: str = "") -> str:
    """Reformat dates in NBA series_summary from M/D to 'Month Dth' using Israel date.

    ESPN returns e.g. 'Series starts 6/3' in US timezone.  A game at 8:30 PM ET
    is 3:30 AM Israel the *next* day, so we use the game's il_date (already
    adjusted) when available.  Otherwise we just reformat M/D to month-name.
    """
    if not series_summary:
        return series_summary

    import re
    _MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"]

    def _ordinal(n: int) -> str:
        if 11 <= n % 100 <= 13:
            return f"{n}th"
        return f"{n}{['th','st','nd','rd'][min(n % 10, 4)] if n % 10 < 4 else 'th'}"

    # If we have il_date and the summary mentions "starts", use il_date
    if il_date and "starts" in series_summary.lower():
        try:
            dt = datetime.datetime.strptime(il_date, "%Y-%m-%d")
            nice = f"{_MONTH_NAMES[dt.month - 1]} {_ordinal(dt.day)}"
            return re.sub(r'\d{1,2}/\d{1,2}', nice, series_summary)
        except Exception:
            pass

    # Fallback: just reformat M/D (US format) to month name
    def _replace(match):
        m, d = int(match.group(1)), int(match.group(2))
        try:
            return f"{_MONTH_NAMES[m - 1]} {_ordinal(d)}"
        except Exception:
            return match.group(0)

    return re.sub(r'(\d{1,2})/(\d{1,2})', _replace, series_summary)

# ─────────────────────────────────────────────────────────────────────────────
# FIREBASE  — multi-user support
# ─────────────────────────────────────────────────────────────────────────────

def _firestore_get_doc(collection: str, doc_id: str) -> dict:
    """Fetch a single Firestore document. Returns raw fields dict or {}."""
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/{collection}/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = fetch_json(url)
        return data.get("fields", {})
    except Exception as e:
        print(f"⚠️  Could not read Firestore {collection}/{doc_id}: {e}")
        return {}

def _firestore_bool(fields: dict, key: str, default: bool = False) -> bool:
    """Extract a boolean field from Firestore fields dict."""
    field = fields.get(key, {})
    if "booleanValue" in field:
        return bool(field["booleanValue"])
    return default

def _firestore_string(fields: dict, key: str, default: str = "") -> str:
    """Extract a string field from Firestore fields dict."""
    return fields.get(key, {}).get("stringValue", default)


def load_global_config() -> dict:
    """Load global config (config/global). Returns dict with world_cup_mode etc."""
    fields = _firestore_get_doc("config", "global")
    return {
        "world_cup_mode": _firestore_bool(fields, "world_cup_mode", False),
        "world_cup_end_date": _firestore_string(fields, "world_cup_end_date", ""),
    }


def load_user_doc(doc_id: str) -> dict:
    """Load a full user document from users/{doc_id}. Single read."""
    fields = _firestore_get_doc(USERS_COLLECTION, doc_id)
    if not fields:
        return {}

    teams_field = fields.get("teams", {}).get("arrayValue", {}).get("values", [])
    teams = []
    for t in teams_field:
        m = t.get("mapValue", {}).get("fields", {})
        enabled_field = m.get("enabled", {})
        enabled = bool(enabled_field["booleanValue"]) if "booleanValue" in enabled_field else True
        if not enabled:
            continue
        teams.append({
            "name":     m.get("name",     {}).get("stringValue", ""),
            "sport":    m.get("sport",    {}).get("stringValue", ""),
            "leagueId": m.get("leagueId", {}).get("stringValue", ""),
            "league":   m.get("league",   {}).get("stringValue", ""),
        })

    return {
        "doc_id":               doc_id,
        "email":                _firestore_string(fields, "reminder_email") or _firestore_string(fields, "email"),
        "display_name":         _firestore_string(fields, "display_name", doc_id),
        "status":               _firestore_string(fields, "status", "active"),
        "teams":                teams,
        "weekly_digest":        _firestore_bool(fields, "weekly_digest", False),
        "avdija_stats":         _firestore_bool(fields, "avdija_stats", True),
        "avdija_dedicated_email": _firestore_bool(fields, "avdija_dedicated_email", False),
        "israeli_players_email":  _firestore_bool(fields, "israeli_players_email", False),
        "player_stats_email":     _firestore_bool(fields, "player_stats_email", False),
        "emails_paused": _firestore_bool(fields, "emails_paused", False),
        "synthetic": _firestore_bool(fields, "synthetic", False),
    }


def load_all_users() -> list[dict]:
    """Load all active users from users/ collection."""
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/{USERS_COLLECTION}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = fetch_json(url)
    except Exception as e:
        print(f"⚠️  Could not list users: {e}")
        return []

    users = []
    for doc in data.get("documents", []):
        doc_path = doc.get("name", "")
        doc_id = doc_path.rsplit("/", 1)[-1] if "/" in doc_path else ""
        if not doc_id:
            continue
        fields = doc.get("fields", {})
        status = _firestore_string(fields, "status", "active")
        if status != "active":
            print(f"   ⏭️  Skipping user {doc_id} (status={status})")
            continue
        user = load_user_doc(doc_id)
        if user:
            users.append(user)
    return users


# ── Legacy wrappers (backward compat for player_stats.py) ──────────────────

def load_tracked_teams(doc_id: str, enabled_only: bool = True) -> list[dict]:
    """Legacy wrapper — reads from users/ collection."""
    user = load_user_doc(doc_id)
    return user.get("teams", [])

def load_avdija_stats_flag(doc_id: str) -> bool:
    """Legacy wrapper."""
    user = load_user_doc(doc_id)
    return user.get("avdija_stats", True)

def load_weekly_digest_flag(doc_id: str) -> bool:
    """Legacy wrapper."""
    user = load_user_doc(doc_id)
    return user.get("weekly_digest", False)

def load_world_cup_mode_flag(doc_id: str) -> bool:
    """Legacy wrapper — now reads from global config."""
    gc = load_global_config()
    return gc.get("world_cup_mode", False)


# ─────────────────────────────────────────────────────────────────────────────
# ESPN ENDPOINTS  (league_id → URL)
# ─────────────────────────────────────────────────────────────────────────────
ESPN_ENDPOINTS = {
    "premier_league":       "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "la_liga":              "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "bundesliga":           "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard",
    "serie_a":              "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "ligue_1":              "https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/scoreboard",
    "champions_league":     "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard",
    "europa_league":        "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/scoreboard",
    "israeli_pl_soccer":    "https://site.api.espn.com/apis/site/v2/sports/soccer/isr.1/scoreboard",
    "mls":                  "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
    "fifa_world_cup":       "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
    "nba":                  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "euroleague":            None,    # uses EuroLeague official API (see below)
    "eurocup":               None,    # uses EuroCup official API (see below)
    "israeli_pl_basketball": None,    # uses TheSportsDB (ESPN returns empty for isr.1 basketball)
}

# ─────────────────────────────────────────────────────────────────────────────
# THESPORTSDB — Israeli leagues (ESPN isr.1 returns only partial team list)
# Free key "3" covers eventsday + eventsseason.
# Basketball ID=4474, Soccer ID=4644 (Israeli Premier League / Ligat HaAl)
# ─────────────────────────────────────────────────────────────────────────────
TSDB_LEAGUES = {
    "israeli_pl_basketball": "Israeli Basketball Premier League",
    "israeli_pl_soccer":     "Israeli Premier League",
}
TSDB_LEAGUE_IDS = {
    "israeli_pl_basketball": "4474",
    "israeli_pl_soccer":     "4644",
}
TSDB_SEASON = "2025-2026"
TSDB_FREE_KEY = "3"

# ─────────────────────────────────────────────────────────────────────────────
# EUROLEAGUE / EUROCUP OFFICIAL API
# ESPN dropped these — use api-live.euroleague.net instead
# Competition codes: E = EuroLeague, U = EuroCup
# Season codes: E2025 = 2025-26 EuroLeague, U2025 = 2025-26 EuroCup
# ─────────────────────────────────────────────────────────────────────────────
EUROLEAGUE_COMPETITION_CODES = {
    "euroleague": ("E", "E2025"),
    "eurocup":    ("U", "U2025"),
}

# ─────────────────────────────────────────────────────────────────────────────
# TEAM NAME MATCHING
# Three-layer approach:
#   1. NOISE_TOKENS  — strip known sponsor words before comparing
#   2. Word-coverage — all words of user's name appear in API name (multi-word)
#   3. ALIASES       — last resort for abbreviations that can't be solved algorithmically
# ─────────────────────────────────────────────────────────────────────────────

# Sponsor / filler words that APIs inject into team names.
# These are NEVER part of a team's actual identity — safe to ignore.
NOISE_TOKENS = {
    # EuroLeague / EuroCup jersey sponsors (updated each season as needed)
    "rapyd",        # Maccabi Rapyd Tel Aviv
    "ibi",          # Hapoel IBI Tel Aviv
    "beko",         # Fenerbahce Beko
    "aktor",        # Panathinaikos Aktor
    "mozzart",      # Partizan Mozzart Bet
    "bet",          # Partizan Mozzart Bet  (also covers "1xbet", etc.)
    "ea7",          # EA7 Emporio Armani Milan
    "emporio",      # EA7 Emporio Armani Milan
    "armani",       # EA7 Emporio Armani Milan
    "ldlc",         # LDLC ASVEL Villeurbanne
    "segafredo",    # Virtus Segafredo Bologna
    "meridianbet",  # Crvena Zvezda Meridianbet
    "betano",       # various soccer clubs
    "ewc",          # Efes branding variants
    "baxi",         # Baxi Manresa (EuroCup)
    "cosea",        # Cosea JL Bourg-en-Bresse (EuroCup)
    "midtown",      # Hapoel Midtown Jerusalem (EuroCup)
    "gain",         # Besiktas Gain Istanbul (EuroCup)
    "betsson",      # Aris Thessaloniki Betsson (EuroCup)
    "cosmorama",    # Panionios Cosmorama (EuroCup)
    "voli",         # Buducnost Voli (EuroCup)
    # Soccer jersey sponsors occasionally appended to team names
    "365",          # bet365 variations
    "ticketportal",
}

# Hard-coded aliases: only for cases the algorithm can't solve
# (abbreviations, historic name changes, etc.)
TEAM_ALIASES = {
    # ESPN soccer abbreviations
    "Man City":                    "Manchester City",
    "Man United":                  "Manchester United",
    "Spurs":                       "Tottenham Hotspur",
    "Nottm Forest":                "Nottingham Forest",
    "Inter":                       "Inter Milan",
    # ESPN drops/changes prefixes or uses short name
    "Barcelona":                   "FC Barcelona",
    "Porto":                       "FC Porto",
    "Bologna":                     "Bologna FC",
    "Milan":                       "AC Milan",
    "Lazio":                       "SS Lazio",
    "Atletico Madrid":             "Atletico Madrid",
    "Atlético Madrid":             "Atletico Madrid",
    "Hoffenheim":                  "TSG Hoffenheim",
    "RB Leipzig":                  "RB Leipzig",
    "Köln":                        "FC Koln",
    "Koln":                        "FC Koln",
    "Valencia":                    "Valencia CF",
    "Sevilla":                     "Sevilla FC",
    "Espanol":                     "RCD Espanyol",
    "Espanyol":                    "RCD Espanyol",
    "Osasuna":                     "CA Osasuna",
    "Alaves":                      "Deportivo Alaves",
    "Alavés":                      "Deportivo Alaves",
    "Paris Saint-Germain":         "Paris Saint-Germain",  # identity, resolves accent issues
    "PSG":                         "Paris Saint-Germain",
    "Lens":                        "Lens",                 # identity
    "Rennes":                      "Rennes",
    "Brentford":                   "Brentford",
    "Bournemouth":                 "Bournemouth",
    # Israeli teams — ESPN uses hyphens / apostrophes
    "Maccabi Tel-Aviv":            "Maccabi Tel Aviv",
    "Hapoel Tel-Aviv":             "Hapoel Tel Aviv",
    "Hapoel Be'er":                "Hapoel Beer Sheva",    # ESPN truncates
    "Hapoel Be'er Sheva":          "Hapoel Beer Sheva",
    "Hapoel Beer-Sheva":           "Hapoel Beer Sheva",
    "Bnei Yehuda Tel-Aviv":        "Bnei Yehuda",
    "Ironi Kiryat-Shmona":         "Ironi Kiryat Shmona",
    "Hapoel Jerusalem":            "Hapoel Jerusalem",
    "Bnei Sakhnin":                "Bnei Sakhnin",
    "Hapoel Hadera":               "Hapoel Hadera",
    "Maccabi Petah-Tikva":         "Maccabi Petah Tikva",
    "Hapoel Raanana":              "Hapoel Raanana",
    "Maccabi Bnei Raina":          "Maccabi Bnei Raina",
    # EuroLeague / EuroCup
    "Zalgiris Kaunas":             "Zalgiris",
    "Crvena zvezda":               "Crvena Zvezda",
    "AS Monaco":                   "Monaco Basket",        # EuroLeague basketball
    "AS MONACO":                   "Monaco Basket",        # uppercase variant from API
    "EA7 Emporio Armani Milan":    "Olimpia Milano",       # full sponsor name → common name
    "EA7 EMPORIO ARMANI MILAN":    "Olimpia Milano",       # uppercase variant from API
    "Armani Milan":                "Olimpia Milano",
    "Olimpia Milano":              "Olimpia Milano",       # identity
    "Baskonia Vitoria-Gasteiz":    "Baskonia",
    "LDLC ASVEL VILLEURBANNE":     "ASVEL",                # EuroCup — ASVEL is 5 chars, below threshold
    "LDLC ASVEL Villeurbanne":     "ASVEL",
    # ESPN uses Italian name for Inter
    "Internazionale":              "Inter Milan",
    "FC Internazionale":           "Inter Milan",
    "FC Internazionale Milano":    "Inter Milan",
    # Rennes — ESPN uses full French name
    "Stade Rennais":               "Rennes",
    "Stade Rennais FC":            "Rennes",
    # MLS abbreviation
    "LAFC":                        "Los Angeles FC",
    # Red Bull Salzburg — ESPN sometimes uses RB abbreviation
    "RB Salzburg":                 "Red Bull Salzburg",
    "FC Red Bull Salzburg":        "Red Bull Salzburg",
    # Champions League / Europa League — ESPN sometimes uses shorter names
    "Real Madrid CF":              "Real Madrid",
    "Inter Milan":                 "Inter Milan",
    "Borussia Dortmund":           "Borussia Dortmund",
    "Sporting CP":                 "Sporting CP",
    "Slavia Prague":               "Slavia Prague",
    "FC Copenhagen":               "FC Copenhagen",
    "Bodo/Glimt":                  "Bodo/Glimt",
    "Union Saint-Gilloise":        "Union Saint-Gilloise",
    "Pafos FC":                    "Pafos FC",
    "Qarabag":                     "Qarabag",
    "Kairat Almaty":               "Kairat Almaty",
    "Villarreal CF":               "Villarreal CF",
    "Red Bull Salzburg":           "Red Bull Salzburg",
    "Eintracht Frankfurt":         "Eintracht Frankfurt",
    "Fenerbahce":                  "Fenerbahce",           # Europa League (no Beko)
    # NBA — "LA" abbreviation for Los Angeles teams
    "Los Angeles Lakers":          "LA Lakers",
    "Los Angeles Clippers":        "LA Clippers",
    # MLS
    "Los Angeles FC":              "Los Angeles FC",
    "Nashville SC":                "Nashville SC",
}

# ─────────────────────────────────────────────────────────────────────────────
# FIFA WORLD CUP 2026 — Emoji flags for national teams
# ESPN abbreviation → ISO 3166-1 alpha-2 (used to build emoji flag)
# ─────────────────────────────────────────────────────────────────────────────
_ESPN_ABBR_TO_ISO2 = {
    "MEX": "MX", "RSA": "ZA", "KOR": "KR", "CZE": "CZ", "CAN": "CA",
    "BIH": "BA", "USA": "US", "PAR": "PY", "QAT": "QA", "SUI": "CH",
    "BRA": "BR", "MAR": "MA", "HAI": "HT", "SCO": "GB",
    "AUS": "AU", "TUR": "TR", "GER": "DE", "CUW": "CW", "NED": "NL",
    "JPN": "JP", "CIV": "CI", "ECU": "EC", "SWE": "SE", "TUN": "TN",
    "ESP": "ES", "CPV": "CV", "BEL": "BE", "EGY": "EG", "KSA": "SA",
    "URU": "UY", "IRN": "IR", "NZL": "NZ", "FRA": "FR", "SEN": "SN",
    "IRQ": "IQ", "NOR": "NO", "ARG": "AR", "ALG": "DZ", "AUT": "AT",
    "JOR": "JO", "POR": "PT", "COD": "CD", "ENG": "GB",
    "CRO": "HR", "GHA": "GH", "PAN": "PA", "UZB": "UZ", "COL": "CO",
}

_SPECIAL_FLAGS = {
    "ENG": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "SCO": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
}

def _country_flag_emoji(espn_abbr: str) -> str:
    """Convert ESPN team abbreviation to emoji flag."""
    if espn_abbr in _SPECIAL_FLAGS:
        return _SPECIAL_FLAGS[espn_abbr]
    iso2 = _ESPN_ABBR_TO_ISO2.get(espn_abbr, "")
    if len(iso2) == 2:
        return chr(0x1F1E6 + ord(iso2[0]) - ord('A')) + chr(0x1F1E6 + ord(iso2[1]) - ord('A'))
    return ""

def _team_display_with_flag(team_name: str, espn_abbr: str) -> str:
    """Return 'flag name' or just 'name' if no flag found."""
    flag = _country_flag_emoji(espn_abbr)
    return f"{flag} {team_name}" if flag else team_name


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def strip_accents(s: str) -> str:
    """Remove accents: Atlético → Atletico"""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def normalize_name(name: str) -> str:
    """Lowercase, strip accents, replace hyphens, remove common prefixes."""
    name = strip_accents(name).lower().strip()
    name = name.replace("-", " ").replace("'", "")
    for prefix in ["fc ", "afc ", "as ", "rc ", "ac ", "sc ", "vfb ", "vfl ", "fsv "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    for suffix in [" fc", " sc", " ac", " bc", " b.c."]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()

def strip_noise(name: str) -> str:
    """Remove sponsor/noise tokens from a normalized name."""
    return " ".join(
        w for w in normalize_name(name).split()
        if w not in NOISE_TOKENS
    )

def names_match(api_name: str, our_name: str) -> bool:
    """
    Match an API team name against the user's stored name.
    Layers (first match wins):
      1. Alias table  — handles abbreviations (Man City → Manchester City)
      2. Exact norm   — handles accents, FC/AS prefixes
      3. Word-subset  — handles sponsor insertions (Maccabi Rapyd Tel Aviv → Maccabi Tel Aviv)
      4. Noise-strip + word-subset  — handles sponsor at start/end for short names
      5. Noise-strip + single-word  — "Panathinaikos" matches "Panathinaikos Aktor Athens"
    """
    # 1. Alias table (case-insensitive key lookup)
    resolved = TEAM_ALIASES.get(api_name) or TEAM_ALIASES.get(api_name.title()) or api_name
    if resolved.lower() == our_name.lower():
        return True
    # Also try: user stored name might itself be an alias key mapping to the canonical
    resolved2 = TEAM_ALIASES.get(our_name) or TEAM_ALIASES.get(our_name.title())
    if resolved2 and resolved2.lower() == api_name.lower():
        return True

    norm_api = normalize_name(api_name)
    norm_our = normalize_name(our_name)

    # 2. Exact normalized match
    if norm_api == norm_our:
        return True

    # 3. Word-subset: all words of user's name are present in API name
    our_words = set(norm_our.split())
    api_words = set(norm_api.split())
    if len(our_words) >= 2 and our_words.issubset(api_words):
        return True

    # 4. Noise-stripped word-subset: strip sponsors, then re-check
    clean_api = strip_noise(api_name)
    clean_our = strip_noise(our_name)
    clean_our_words = set(clean_our.split())
    clean_api_words = set(clean_api.split())

    if clean_api == clean_our:
        return True
    if len(clean_our_words) >= 2 and clean_our_words.issubset(clean_api_words):
        return True

    # 5. Single significant word after noise stripping:
    #    user saves "Panathinaikos", API says "Panathinaikos Aktor Athens"
    #    → after stripping "aktor": "panathinaikos athens"
    #    → "panathinaikos" is the FIRST word → match
    #    Require ≥6 chars to avoid false positives on city names like "Milan"
    clean_api_list = clean_api.split()
    if (len(clean_our_words) == 1
            and clean_api_list
            and len(list(clean_our_words)[0]) >= 7
            and list(clean_our_words)[0] == clean_api_list[0]):
        return True

    return False

def fetch_json(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
        "Referer": "https://www.espn.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def today_israel() -> str:
    """Return today's date in Israel as YYYY-MM-DD."""
    utc_now = datetime.datetime.utcnow()
    israel_now = utc_now + datetime.timedelta(hours=_israel_utc_offset_h(utc_now))
    return israel_now.strftime("%Y-%m-%d")

def _format_series_summary(series_summary: str, il_date: str = "") -> str:
    """Reformat dates in NBA series_summary from M/D to 'Month Dth' using Israel date.

    ESPN returns e.g. 'Series starts 6/3' in US timezone.  A game at 8:30 PM ET
    is 3:30 AM Israel the *next* day, so we use the game's il_date (already
    adjusted) when available.  Otherwise we just reformat M/D to month-name.
    """
    if not series_summary:
        return series_summary

    import re
    _MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"]

    def _ordinal(n: int) -> str:
        if 11 <= n % 100 <= 13:
            return f"{n}th"
        return f"{n}{['th','st','nd','rd'][min(n % 10, 4)] if n % 10 < 4 else 'th'}"

    # If we have il_date and the summary mentions "starts", use il_date
    if il_date and "starts" in series_summary.lower():
        try:
            dt = datetime.datetime.strptime(il_date, "%Y-%m-%d")
            nice = f"{_MONTH_NAMES[dt.month - 1]} {_ordinal(dt.day)}"
            return re.sub(r'\d{1,2}/\d{1,2}', nice, series_summary)
        except Exception:
            pass

    # Fallback: just reformat M/D (US format) to month name
    def _replace(match):
        m, d = int(match.group(1)), int(match.group(2))
        try:
            return f"{_MONTH_NAMES[m - 1]} {_ordinal(d)}"
        except Exception:
            return match.group(0)

    return re.sub(r'(\d{1,2})/(\d{1,2})', _replace, series_summary)

# ─────────────────────────────────────────────────────────────────────────────
# FIREBASE  — read user's tracked teams
# ─────────────────────────────────────────────────────────────────────────────
def load_tracked_teams(doc_id: str, enabled_only: bool = True) -> list[dict]:
    """
    Returns list of dicts: [{name, sport, leagueId, league, enabled}, ...]
    Uses Firebase REST API — no SDK needed.

    enabled_only=True  → skip teams where enabled=false (for dry-run / real send)
    enabled_only=False → return ALL teams regardless of enabled flag (for validation)
    If a team has no "enabled" field it is treated as enabled=True.
    """
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/configs/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = fetch_json(url)
    except Exception as e:
        print(f"⚠️  Could not read Firestore: {e}")
        return []

    fields = data.get("fields", {})
    teams_field = fields.get("teams", {}).get("arrayValue", {}).get("values", [])
    teams = []
    for t in teams_field:
        m = t.get("mapValue", {}).get("fields", {})
        # Support optional "enabled" boolean field stored by the React UI
        enabled_field = m.get("enabled", {})
        if "booleanValue" in enabled_field:
            enabled = bool(enabled_field["booleanValue"])
        else:
            enabled = True  # absent = enabled
        if enabled_only and not enabled:
            continue
        teams.append({
            "name":     m.get("name",     {}).get("stringValue", ""),
            "sport":    m.get("sport",    {}).get("stringValue", ""),
            "leagueId": m.get("leagueId", {}).get("stringValue", ""),
            "league":   m.get("league",   {}).get("stringValue", ""),
            "enabled":  enabled,
        })
    return teams


def load_avdija_stats_flag(doc_id: str) -> bool:
    """Returns True if Avdija stats email is enabled (default: True if field absent)."""
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/configs/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = fetch_json(url)
    except Exception:
        return True  # default to enabled on error
    avdija_field = data.get("fields", {}).get("avdija_stats", {})
    if "booleanValue" in avdija_field:
        return bool(avdija_field["booleanValue"])
    return True  # absent = enabled


def load_weekly_digest_flag(doc_id: str) -> bool:
    """Returns True if weekly digest email is enabled (default: False — opt-in feature)."""
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/configs/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = fetch_json(url)
    except Exception:
        return False
    field = data.get("fields", {}).get("weekly_digest", {})
    if "booleanValue" in field:
        return bool(field["booleanValue"])
    return False  # absent = disabled


def load_world_cup_mode_flag(doc_id: str) -> bool:
    """Returns True if World Cup all-games mode is enabled (default: False — opt-in)."""
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/configs/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = fetch_json(url)
    except Exception:
        return False
    field = data.get("fields", {}).get("world_cup_mode", {})
    if "booleanValue" in field:
        return bool(field["booleanValue"])
    return False  # absent = disabled


# ─────────────────────────────────────────────────────────────────────────────
# ESPN  — fetch today's games per league
# ─────────────────────────────────────────────────────────────────────────────
def fetch_todays_games(league_id: str, today: str, weekly_mode: bool = False) -> list[dict]:
    """Returns list of game dicts for today.
    weekly_mode=True skips the NBA 24-hour filter and adds il_date to each game."""
    # Route EuroLeague / EuroCup to the official API
    if league_id in EUROLEAGUE_COMPETITION_CODES:
        return fetch_euroleague_games(league_id, today)
    # Route Israeli Basketball to TheSportsDB
    if league_id in TSDB_LEAGUES:
        return fetch_tsdb_games(league_id, today)

    url = ESPN_ENDPOINTS.get(league_id)
    if not url:
        return []

    # For NBA, MLS, and FIFA World Cup: query both today and tomorrow (UTC)
    # to catch overnight games that cross midnight UTC (e.g. 02:00Z = 05:00 IL)
    if league_id in ("nba", "mls", "fifa_world_cup"):
        today_fmt    = today.replace("-", "")
        tomorrow_utc = (datetime.datetime.strptime(today, "%Y-%m-%d")
                        + datetime.timedelta(days=1)).strftime("%Y%m%d")
        all_events: list = []
        for dated_url in [f"{url}?dates={today_fmt}", f"{url}?dates={tomorrow_utc}"]:
            try:
                all_events.extend(fetch_json(dated_url).get("events", []))
            except Exception as e:
                print(f"  ⚠️  ESPN fetch failed for {league_id}: {e}")
        data = {"events": all_events}
    else:
        try:
            data = fetch_json(f"{url}?dates={today.replace('-', '')}")
        except Exception as e:
            print(f"  ⚠️  ESPN fetch failed for {league_id}: {e}")
            return []

    # tomorrow_utc string for date filtering (NBA only)
    tomorrow_utc_str = (datetime.datetime.strptime(today, "%Y-%m-%d")
                        + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    games = []
    for event in data.get("events", []):
        game_date = event.get("date", "")[:10]
        # NBA, MLS, and FIFA WC: accept today + tomorrow UTC dates
        # (tomorrow UTC games are filtered below to only include pre-08:00 IL)
        if league_id in ("nba", "mls", "fifa_world_cup"):
            if game_date != today and game_date != tomorrow_utc_str:
                continue
        # In weekly_mode: also accept tomorrow's date (cross-midnight games)
        elif weekly_mode:
            if game_date != today and game_date != tomorrow_utc_str:
                continue
        else:
            if game_date != today:
                continue

        # Cross-midnight filter: games from tomorrow_utc only appear in today's
        # daily email if they start before 08:00 Israel time.
        # This prevents e.g. a 22:00 IL game on the next day from showing up.
        # Skip this filter in weekly_mode (weekly digest shows all games).
        if game_date == tomorrow_utc_str and not weekly_mode:
            try:
                _utc_dt = datetime.datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ")
                _il_off = _israel_utc_offset_h(_utc_dt)
                _il_hour = (_utc_dt + datetime.timedelta(hours=_il_off)).hour
                if _il_hour >= 8:
                    continue
            except Exception:
                pass

        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        # Try to get game time in Israel timezone (DST-aware)
        game_utc_dt = None
        game_local  = None
        # ESPN puts timeValid on competition OR event — check both.
        # We trust ESPN's timeValid field; no extra placeholder heuristic needed.
        time_valid  = comp.get("timeValid", event.get("timeValid", True))
        try:
            game_utc_dt = datetime.datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ")
            il_offset   = _israel_utc_offset_h(game_utc_dt)
            game_local  = game_utc_dt + datetime.timedelta(hours=il_offset)

            time_str    = "TBD" if not time_valid else game_local.strftime("%H:%M")
        except Exception:
            time_str = "TBD"

        # NBA/MLS: only show games within the next 24 hours (skip this filter in weekly_mode)
        if league_id in ("nba", "mls") and game_utc_dt is not None and not weekly_mode:
            now_utc = datetime.datetime.utcnow()
            if game_utc_dt < now_utc or game_utc_dt > now_utc + datetime.timedelta(hours=24):
                continue

        # Israel date of this game (used by weekly digest for correct bucketing)
        il_date = game_local.strftime("%Y-%m-%d") if game_local else today
        # Display date: games 00:00-04:59 IL shown under previous day
        display_date = _compute_display_date(il_date, time_str)

        # Playoff series info (NBA)
        series_summary = ""
        playoff_note   = ""
        if league_id == "nba":
            series_obj = comp.get("series", {})
            if series_obj:
                series_summary = series_obj.get("summary", "")  # e.g. "LAL lead series 2-0"
            for note in comp.get("notes", []):
                headline = note.get("headline", "")
                if headline:
                    playoff_note = headline  # e.g. "West 1st Round - Game 5 If Necessary"

        # Tournament round/group info (World Cup, Champions League, etc.)
        tournament_note = ""
        if league_id == "fifa_world_cup":
            for note in comp.get("notes", []):
                headline = note.get("headline", "")
                if headline:
                    tournament_note = headline  # e.g. "Group A", "Round of 16"

        games.append({
            "home":      home["team"]["displayName"],
            "away":      away["team"]["displayName"],
            "home_abbr": home["team"].get("abbreviation", ""),
            "away_abbr": away["team"].get("abbreviation", ""),
            "time":      time_str,
            "il_date":   il_date,
            "display_date": display_date,
            "status":    comp.get("status", {}).get("type", {}).get("description", ""),
            "league_id": league_id,
            "series_summary": series_summary,
            "playoff_note":   playoff_note,
            "tournament_note": tournament_note,
        })
    return games

# ─────────────────────────────────────────────────────────────────────────────
# EUROLEAGUE OFFICIAL API — fetch today's games
# ─────────────────────────────────────────────────────────────────────────────
def fetch_euroleague_games(league_id: str, today: str) -> list[dict]:
    """
    Fetch today's games from the official EuroLeague/EuroCup API.
    Returns XML with all season results; we filter to today's date.
    Date format in XML: "Mar 24, 2026"  →  we compare with YYYY-MM-DD today.
    """
    _, season_code = EUROLEAGUE_COMPETITION_CODES[league_id]
    # Use /schedules (not /results) — results only has played games; schedules has everything
    url = f"https://api-live.euroleague.net/v1/schedules?seasonCode={season_code}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/xml,text/xml,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.euroleague.net",
            "Referer": "https://www.euroleague.net/",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            xml_data = r.read()
    except Exception as e:
        print(f"  ⚠️  EuroLeague API fetch failed for {league_id}: {e}")
        return []

    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        print(f"  ⚠️  EuroLeague XML parse error for {league_id}: {e}")
        return []

    # Parse today's date for comparison
    try:
        today_dt = datetime.datetime.strptime(today, "%Y-%m-%d").date()
    except Exception:
        return []

    games = []
    # schedules API uses <item> elements; results API uses <game>
    for game in root.findall("item"):
        date_str = (game.findtext("date") or "").strip()   # e.g. "Mar 24, 2026"
        if not date_str:
            continue
        try:
            game_dt = datetime.datetime.strptime(date_str, "%b %d, %Y").date()
        except Exception:
            continue
        if game_dt != today_dt:
            continue

        home = (game.findtext("hometeam") or "").strip().title()
        away = (game.findtext("awayteam") or "").strip().title()
        # schedules uses <startime>; results used <time>
        time_raw = (game.findtext("startime") or game.findtext("time") or "").strip()

        # Convert CET/CEST (Berlin) → Israel time (EuroLeague API returns startime in CET)
        try:
            t = datetime.datetime.strptime(time_raw, "%H:%M")
            game_berlin = datetime.datetime.combine(game_dt, t.time())
            berlin_offset = _berlin_utc_offset_h(game_berlin)
            game_utc = game_berlin - datetime.timedelta(hours=berlin_offset)
            il_offset = _israel_utc_offset_h(game_utc)
            game_israel = game_utc + datetime.timedelta(hours=il_offset)
            time_str = game_israel.strftime("%H:%M")
        except Exception:
            time_str = time_raw or "TBD"

        games.append({
            "home":      home,
            "away":      away,
            "time":      time_str,
            "status":    "Scheduled",
            "league_id": league_id,
            "display_date": _compute_display_date(today, time_str),
        })
    return games

# ─────────────────────────────────────────────────────────────────────────────
# THESPORTSDB — Israeli Basketball Premier League
# ─────────────────────────────────────────────────────────────────────────────
def fetch_tsdb_games(league_id: str, today: str) -> list[dict]:
    """Fetch today's games from TheSportsDB for leagues in TSDB_LEAGUES."""
    league_name = TSDB_LEAGUES.get(league_id)
    if not league_name:
        return []
    url = (f"https://www.thesportsdb.com/api/v1/json/{TSDB_FREE_KEY}"
           f"/eventsday.php?d={today}&l={urllib.parse.quote(league_name)}")
    try:
        data = fetch_json(url)
    except Exception as e:
        print(f"  ⚠️  TheSportsDB fetch failed for {league_id}: {e}")
        return []
    events = data.get("events") or []
    games = []
    for ev in events:
        if ev.get("strStatus") in ("FT", "AOT", "AET"):
            continue  # skip finished games
        home = ev.get("strHomeTeam", "")
        away = ev.get("strAwayTeam", "")
        # Always use strTime (UTC) + DST-aware offset.
        # strTimeLocal is unreliable — TheSportsDB returns UTC+2 (IST) even during IDT (UTC+3),
        # causing a 1-hour error during Israeli summer time (DST).
        time_utc = (ev.get("strTime") or "").strip()
        if time_utc:
            try:
                t_utc = datetime.datetime.strptime(time_utc[:5], "%H:%M")
                game_utc_full = datetime.datetime.combine(
                    datetime.datetime.strptime(today, "%Y-%m-%d").date(),
                    t_utc.time()
                )
                il_offset = _israel_utc_offset_h(game_utc_full)
                t_il = t_utc + datetime.timedelta(hours=il_offset)
                time_str = t_il.strftime("%H:%M")
            except Exception:
                time_str = "TBD"
        else:
            time_str = "TBD"
        games.append({
            "home":      home,
            "away":      away,
            "time":      time_str,
            "status":    ev.get("strStatus", "Scheduled"),
            "league_id": league_id,
            "display_date": _compute_display_date(today, time_str),
        })
    return games

def _all_teams_from_tsdb(league_id: str) -> list[str]:
    """Fetch all team names from TheSportsDB season schedule (for validation)."""
    lid = TSDB_LEAGUE_IDS.get(league_id)
    if not lid:
        return []
    url = (f"https://www.thesportsdb.com/api/v1/json/{TSDB_FREE_KEY}"
           f"/eventsseason.php?id={lid}&s={urllib.parse.quote(TSDB_SEASON)}")
    try:
        data = fetch_json(url)
    except Exception as e:
        return [f"__ERROR__{e}"]
    events = data.get("events") or []
    seen = set()
    for ev in events:
        for field in ("strHomeTeam", "strAwayTeam"):
            name = (ev.get(field) or "").strip()
            if name:
                seen.add(name)
    return sorted(seen)

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION — check every tracked team can be found in its league's API
# ─────────────────────────────────────────────────────────────────────────────
def _all_teams_from_euroleague(league_id: str) -> list[str]:
    """Fetch every team name from the full season schedule."""
    _, season_code = EUROLEAGUE_COMPETITION_CODES[league_id]
    url = f"https://api-live.euroleague.net/v1/schedules?seasonCode={season_code}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/xml,text/xml,*/*",
            "Origin": "https://www.euroleague.net",
            "Referer": "https://www.euroleague.net/",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            xml_data = r.read()
    except Exception as e:
        return [f"__ERROR__{e}"]
    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        return [f"__ERROR__{e}"]
    seen = set()
    for item in root.findall("item"):
        for field in ("hometeam", "awayteam"):
            name = (item.findtext(field) or "").strip()
            if name:
                seen.add(name)
    return sorted(seen)

def _all_teams_from_espn(league_id: str) -> list[str]:
    """Fetch ALL team names from ESPN /teams endpoint (full league roster, not just today)."""
    scoreboard_url = ESPN_ENDPOINTS.get(league_id)
    if not scoreboard_url:
        return []
    # Replace /scoreboard with /teams to get the full team list regardless of today's schedule
    teams_url = scoreboard_url.replace("/scoreboard", "/teams")
    try:
        data = fetch_json(teams_url)
    except Exception as e:
        return [f"__ERROR__{e}"]
    seen = set()
    # ESPN /teams response: {"sports":[{"leagues":[{"teams":[{"team":{"displayName":...}}]}]}]}
    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for entry in league.get("teams", []):
                name = entry.get("team", {}).get("displayName", "")
                if name:
                    seen.add(name)
    return sorted(seen)

def validate_teams(tracked: list[dict]) -> list[dict]:
    """
    For each tracked team, check whether it can be found in its league's API.
    Returns list of dicts: {name, league, status, matched_as, games_found}
    """
    # Cache API team lists per league_id
    api_teams_cache: dict[str, list[str]] = {}

    def get_api_teams(league_id: str) -> list[str]:
        if league_id not in api_teams_cache:
            if league_id in EUROLEAGUE_COMPETITION_CODES:
                api_teams_cache[league_id] = _all_teams_from_euroleague(league_id)
            elif league_id in TSDB_LEAGUES:
                api_teams_cache[league_id] = _all_teams_from_tsdb(league_id)
            elif ESPN_ENDPOINTS.get(league_id):
                api_teams_cache[league_id] = _all_teams_from_espn(league_id)
            else:
                api_teams_cache[league_id] = []
        return api_teams_cache[league_id]

    results = []
    for team in tracked:
        league_id = team["leagueId"]
        api_teams = get_api_teams(league_id)

        # Check for fetch error
        errors = [t for t in api_teams if t.startswith("__ERROR__")]
        if errors:
            results.append({
                "name": team["name"], "league": team.get("league", league_id),
                "leagueId": league_id,
                "status": "error", "matched_as": errors[0].replace("__ERROR__", ""),
                "games_found": 0,
            })
            continue

        if not api_teams:
            results.append({
                "name": team["name"], "league": team.get("league", league_id),
                "leagueId": league_id,
                "status": "unsupported", "matched_as": "League not supported yet",
                "games_found": 0,
            })
            continue

        # Try to find a match in API team list
        matched = [t for t in api_teams if names_match(t, team["name"])]
        if matched:
            results.append({
                "name": team["name"], "league": team.get("league", league_id),
                "leagueId": league_id,
                "status": "ok",
                "matched_as": matched[0] if len(matched) == 1 else f"{matched[0]} (+{len(matched)-1} more)",
                "games_found": len(matched),
            })
        else:
            # Show the closest API names to help the user fix it
            hint = ", ".join(api_teams[:5]) + ("..." if len(api_teams) > 5 else "")
            results.append({
                "name": team["name"], "league": team.get("league", league_id),
                "leagueId": league_id,
                "status": "no_match",
                "matched_as": f"Not found. API has: {hint}",
                "games_found": 0,
            })
    return results

# ─────────────────────────────────────────────────────────────────────────────
# FIRESTORE WRITE — disable teams that fail validation
# ─────────────────────────────────────────────────────────────────────────────
def disable_failing_teams(doc_id: str) -> dict:
    """
    Re-enable ALL teams, then run fresh validation and disable only those
    not found in any league API (status='no_match').
    This corrects previous false-positive disables (e.g. due to incomplete ESPN data).
    Returns {"disabled": [...], "reenabled": int, "total": int, "error": str|None}
    """
    import urllib.request as _ur
    base_url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/configs/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    # --- 1. Fetch raw Firestore doc ---
    try:
        raw = fetch_json(base_url)
    except Exception as e:
        return {"disabled": [], "reenabled": 0, "total": 0, "error": str(e)}

    raw_values = (raw.get("fields", {})
                     .get("teams", {})
                     .get("arrayValue", {})
                     .get("values", []))

    # --- 2. Re-enable ALL disabled teams so we get a fresh slate ---
    reenabled = 0
    for entry in raw_values:
        fields = entry.get("mapValue", {}).get("fields", {})
        enabled_field = fields.get("enabled", {})
        if enabled_field.get("booleanValue") is False:
            fields["enabled"] = {"booleanValue": True}
            reenabled += 1

    # --- 3. Run validation on ALL teams (all now enabled) ---
    tracked = load_tracked_teams(doc_id, enabled_only=False)
    if not tracked:
        return {"disabled": [], "reenabled": 0, "total": 0, "error": "No teams found in Firestore"}

    results = validate_teams(tracked)
    failing = {(r["name"], r["leagueId"])
               for r in results if r["status"] == "no_match"}

    # --- 4. Disable only the truly failing teams ---
    disabled_names = []
    for entry in raw_values:
        fields = entry.get("mapValue", {}).get("fields", {})
        name      = fields.get("name",     {}).get("stringValue", "")
        league_id = fields.get("leagueId", {}).get("stringValue", "")
        if (name, league_id) in failing:
            fields["enabled"] = {"booleanValue": False}
            disabled_names.append(f"{name} [{league_id}]")

    # --- 4. PATCH only the teams field back to Firestore ---
    patch_url = base_url + "&updateMask.fieldPaths=teams"
    body = json.dumps({
        "fields": {
            "teams": {"arrayValue": {"values": raw_values}}
        }
    }).encode()
    req = _ur.Request(
        patch_url, data=body, method="PATCH",
        headers={"Content-Type": "application/json"}
    )
    try:
        with _ur.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        return {"disabled": [], "reenabled": reenabled, "total": 0, "error": f"Firestore write failed: {e}"}

    return {"disabled": disabled_names, "reenabled": reenabled, "total": len(disabled_names), "error": None}


# ─────────────────────────────────────────────────────────────────────────────
# MATCHING — find which of your teams play today
# ─────────────────────────────────────────────────────────────────────────────
def fetch_league_games(leagues: set, today: str) -> dict:
    """Fetch today's games for a set of league IDs. Returns {league_id: [games]}.
    Called ONCE for all users — the expensive ESPN/API step."""
    games_by_league: dict[str, list] = {}
    for league_id in leagues:
        if league_id in ESPN_ENDPOINTS or league_id in EUROLEAGUE_COMPETITION_CODES:
            games_by_league[league_id] = fetch_todays_games(league_id, today)
    return games_by_league


def filter_matches_for_user(tracked: list[dict], games_by_league: dict, today: str) -> list[dict]:
    """Filter pre-fetched games by a user's tracked teams."""
    matches = []
    seen = set()

    for tracked_team in tracked:
        league_id = tracked_team["leagueId"]
        games = games_by_league.get(league_id, [])

        for game in games:
            game_key = f"{game['home']}_{game['away']}_{league_id}"
            if game_key in seen:
                continue

            if names_match(game["home"], tracked_team["name"]) or \
               names_match(game["away"], tracked_team["name"]):
                matches.append({
                    **game,
                    "tracked_team": tracked_team["name"],
                    "league_name":  tracked_team.get("league") or league_id,
                    "sport":        tracked_team["sport"],
                })
                seen.add(game_key)

    matches.sort(key=lambda m: (m.get("display_date", m.get("il_date", today)), m.get("il_date", today), m["time"]))
    return matches


def find_my_matches(tracked: list[dict], today: str) -> list[dict]:
    """Legacy wrapper — fetch + filter in one call."""
    leagues_needed = set(t["leagueId"] for t in tracked)
    games_by_league = fetch_league_games(leagues_needed, today)
    return filter_matches_for_user(tracked, games_by_league, today)


def fetch_all_world_cup_games(today: str, tracked_names: set[str] | None = None) -> list[dict]:
    """Fetch ALL World Cup games for today (world_cup_mode).
    Returns match dicts ready for the email, with tracked_team/star markers.
    tracked_names: set of tracked team names (used for WC filtering)."""
    games = fetch_todays_games("fifa_world_cup", today)
    if not games:
        return []

    tracked_names = tracked_names or set()
    matches = []
    for game in games:
        # Check if either team is tracked → mark with star
        tracked_team = ""
        for tname in tracked_names:
            if names_match(game["home"], tname) or names_match(game["away"], tname):
                tracked_team = tname
                break
        matches.append({
            **game,
            "tracked_team": tracked_team,
            "league_name":  "FIFA World Cup",
            "sport":        "soccer",
            "is_world_cup": True,
        })
    return matches


def find_week_matches(tracked: list[dict], start_date: str, world_cup_mode: bool = False, now_il_time: str = None) -> dict:
    """Fetch matches for 7 days starting from start_date (serial).
    Games are bucketed by their *display date* — for games 00:00-04:59 IL this is
    il_date minus 1 day (they belong to the previous evening), others use il_date.
    If world_cup_mode=True, also fetches ALL World Cup games for the week.
    Returns dict: date_str -> list[match], sorted by date, only days with matches."""
    import time as _time

    start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end_date  = (start_dt + datetime.timedelta(days=6)).strftime("%Y-%m-%d")

    # Query ESPN dates from (start_date - 1) through (start_date + 6).
    # The extra day-before catches NBA late-night US games whose Israel date = start_date.
    espn_dates = [
        (start_dt + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(-1, 7)   # 8 ESPN dates total
    ]

    leagues_needed = set(t["leagueId"] for t in tracked)

    def fetch_for_espn_date(date_str: str) -> list[dict]:
        """Fetch all tracked-team matches for one ESPN date in weekly mode (serial)."""
        print(f"  📅 Fetching {date_str}...")
        games_by_league: dict[str, list] = {}
        for i, lid in enumerate(leagues_needed):
            if lid in ESPN_ENDPOINTS or lid in EUROLEAGUE_COMPETITION_CODES or lid in TSDB_LEAGUES:
                games_by_league[lid] = fetch_todays_games(lid, date_str, weekly_mode=True)
                if i < len(leagues_needed) - 1:
                    _time.sleep(0.3)  # avoid ESPN rate limiting within a single date

        matches = []
        seen_local: set = set()
        for tracked_team in tracked:
            lid   = tracked_team["leagueId"]
            games = games_by_league.get(lid, [])
            for game in games:
                # EuroLeague / TSDB games don't carry il_date — use the query date
                if "il_date" not in game:
                    game["il_date"] = date_str
                if "display_date" not in game:
                    game["display_date"] = _compute_display_date(game["il_date"], game.get("time", "TBD"))
                game_key = f"{game['home']}_{game['away']}_{lid}"
                if game_key in seen_local:
                    continue
                if names_match(game["home"], tracked_team["name"]) or \
                   names_match(game["away"], tracked_team["name"]):
                    matches.append({
                        **game,
                        "tracked_team": tracked_team["name"],
                        "league_name":  tracked_team.get("league") or lid,
                        "sport":        tracked_team["sport"],
                    })
                    seen_local.add(game_key)
        print(f"    → {len(matches)} match(es)")
        return matches

    # Fetch serially (one date at a time) with a pause between dates.
    # Parallelism caused ESPN rate-limiting → all leagues returning [] silently.
    all_matches: list[dict] = []
    for i, d in enumerate(espn_dates):
        try:
            all_matches.extend(fetch_for_espn_date(d))
        except Exception as e:
            print(f"  ⚠️  Week fetch failed for {d}: {e}")
        if i < len(espn_dates) - 1:
            _time.sleep(1.0)  # 1s between dates to avoid ESPN rate limiting

    # World Cup mode: fetch all WC games for each ESPN date and merge
    if world_cup_mode:
        tracked_names = {t["name"] for t in tracked}
        print(f"  🏆 World Cup mode — fetching all WC games for the week...")
        for i, d in enumerate(espn_dates):
            try:
                wc_games = fetch_todays_games("fifa_world_cup", d, weekly_mode=True)
                for game in wc_games:
                    if "il_date" not in game:
                        game["il_date"] = d
                    if "display_date" not in game:
                        game["display_date"] = _compute_display_date(game["il_date"], game.get("time", "TBD"))
                    # Check if tracked
                    t_team = ""
                    for tname in tracked_names:
                        if names_match(game["home"], tname) or names_match(game["away"], tname):
                            t_team = tname
                            break
                    if not t_team:
                        continue
                    all_matches.append({
                        **game,
                        "tracked_team": t_team,
                        "league_name":  "FIFA World Cup",
                        "sport":        "soccer",
                        "is_world_cup": True,
                    })
            except Exception as e:
                print(f"  ⚠️  WC week fetch failed for {d}: {e}")
            if i < len(espn_dates) - 1:
                _time.sleep(0.5)

    # Re-bucket by display_date (games 00:00-04:59 IL shown under previous day); deduplicate globally; keep only [start_date, end_date]
    results: dict[str, list] = {}
    seen_global: set = set()
    for match in all_matches:
        dd = match.get("display_date", match.get("il_date", start_date))
        if dd < start_date or dd > end_date:
            continue
        # Skip matches that already happened on the send day
        if now_il_time and il_date == start_date:
            match_time = match.get("time", "")
            if match_time and match_time < now_il_time:
                continue
        game_key = f"{match['home']}_{match['away']}_{match['league_id']}"
        if game_key in seen_global:
            continue
        seen_global.add(game_key)
        results.setdefault(dd, []).append(match)

    # Sort matches within each day by time
    for day_matches in results.values():
        day_matches.sort(key=lambda m: (m.get("il_date", ""), m["time"]))

    return dict(sorted(results.items()))


# ─────────────────────────────────────────────────────────────────────────────
# FULL TOURNAMENT — fetch all FIFA World Cup games (group stage + knockout)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_full_tournament_games(tracked_names: set) -> dict:
    """Fetch ALL FIFA World Cup 2026 games from June 11 to July 19.
    Returns dict: date_str -> list[match], sorted by date.
    tracked_names is used to filter tracked teams."""
    import time as _time

    start_dt = datetime.datetime(2026, 6, 11)
    end_dt   = datetime.datetime(2026, 7, 19)

    # Build list of ESPN query dates (UTC)
    espn_dates = []
    d = start_dt
    while d <= end_dt:
        espn_dates.append(d.strftime("%Y-%m-%d"))
        d += datetime.timedelta(days=1)

    print(f"  🏆 Fetching {len(espn_dates)} days of World Cup games...")
    all_matches: list[dict] = []
    seen: set = set()

    for i, date_str in enumerate(espn_dates):
        try:
            games = fetch_todays_games("fifa_world_cup", date_str, weekly_mode=True)
            for game in games:
                if "il_date" not in game:
                    game["il_date"] = date_str
                if "display_date" not in game:
                    game["display_date"] = _compute_display_date(game["il_date"], game.get("time", "TBD"))
                game_key = f"{game['home']}_{game['away']}"
                if game_key in seen:
                    continue
                seen.add(game_key)
                # Check if tracked
                t_team = ""
                for tname in tracked_names:
                    if names_match(game["home"], tname) or names_match(game["away"], tname):
                        t_team = tname
                        break
                all_matches.append({
                    **game,
                    "tracked_team": t_team,
                    "league_name":  "FIFA World Cup",
                    "sport":        "soccer",
                    "is_world_cup": True,
                })
            print(f"    📅 {date_str}: {len(games)} game(s)")
        except Exception as e:
            print(f"    ⚠️  {date_str}: {e}")
        if i < len(espn_dates) - 1:
            _time.sleep(0.5)

    # Bucket by Israel date
    results: dict[str, list] = {}
    for match in all_matches:
        il_date = match.get("il_date", "")
        results.setdefault(il_date, []).append(match)

    # Sort matches within each day by time
    for day_matches in results.values():
        day_matches.sort(key=lambda m: (m.get("il_date", ""), m["time"]))

    print(f"  ✅ Total: {len(all_matches)} games across {len(results)} days")
    return dict(sorted(results.items()))


def build_tournament_email_html(matches_by_day: dict) -> str:
    """Build HTML email for the full World Cup tournament schedule."""
    total = sum(len(v) for v in matches_by_day.values())
    days_html = ""
    for date_str, matches in matches_by_day.items():
        dt        = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        day_label = dt.strftime("%A, %b ") + str(dt.day)
        rows      = ""
        for m in matches:
            gcal     = _gcal_url(m, date_str)
            gcal_html = (
                f'<div style="margin-top:4px;">'
                f'<a href="{gcal}" style="font-size:11px; color:#1a56db; text-decoration:none;">📅 Add to Calendar</a>'
                f'</div>'
            ) if gcal and m["time"] != "TBD" else ""
            # Tournament round info
            tournament_html = ""
            t_note = m.get("tournament_note", "")
            if t_note:
                tournament_html = f'<div style="font-size:11px; color:#b45309; margin-top:2px; font-style:italic;">{t_note}</div>'
            # Time
            if m["time"] == "TBD":
                time_html = '<span style="font-weight:600; color:#9ca3af;">TBD</span>'
            else:
                time_html = f'<span style="font-weight:600; color:#1a56db;">{m["time"]}</span>'
            # Matchup with flags
            home_flag = _country_flag_emoji(m.get("home_abbr", ""))
            away_flag = _country_flag_emoji(m.get("away_abbr", ""))
            h_disp = f"{home_flag} {m['home']}" if home_flag else m["home"]
            a_disp = f"{away_flag} {m['away']}" if away_flag else m["away"]
            tracked_t = m.get("tracked_team", "")
            if tracked_t:
                if names_match(m["home"], tracked_t):
                    h_disp = h_disp
                elif names_match(m["away"], tracked_t):
                    a_disp = a_disp
            matchup_str = (f'{h_disp}<br>'
                           f'<span style="font-size:12px; color:#888;">Vs</span><br>'
                           f'{a_disp}')
            rows += f"""
                <tr>
                  <td style="padding:10px 12px; font-size:15px; border-bottom:1px solid #f0f0f0; width:32px; vertical-align:top;">🏆</td>
                  <td style="padding:10px 12px; border-bottom:1px solid #f0f0f0;">
                    <div style="font-weight:600; color:#111;">{matchup_str}</div>
                    <div style="font-size:12px; color:#666; margin-top:2px;">{m['league_name']}</div>
                    {tournament_html}
                    {gcal_html}
                  </td>
                  <td style="padding:10px 12px; border-bottom:1px solid #f0f0f0; text-align:right; white-space:nowrap;">
                    {time_html}
                  </td>
                </tr>"""
        days_html += f"""
            <div>
              <div style="padding:8px 16px; font-size:11px; font-weight:700; color:#6b7280;
                          text-transform:uppercase; letter-spacing:0.06em;
                          background:#f8fafc; border-top:1px solid #e5e7eb;">{day_label}</div>
              <table style="width:100%; border-collapse:collapse;">{rows}</table>
            </div>"""

    return f"""
    <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                       background:#f8fafc; margin:0; padding:20px;">
      <div style="max-width:520px; margin:0 auto; background:white; border-radius:16px;
                  overflow:hidden; box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <div style="background:#0f172a; padding:20px 24px;">
          <div style="font-size:40px; margin-bottom:4px; line-height:1;">🏆</div>
          <h1 style="color:white; margin:0; font-size:18px; font-weight:700;">FIFA World Cup 2026</h1>
          <p style="color:#94a3b8; margin:4px 0 0; font-size:13px;">Full Schedule — {total} matches · Israel time</p>
        </div>
        <div>{days_html}</div>
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb;">
          <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=tournament"
             style="font-size:12px; color:#6b7280; text-decoration:none;">
            ✏️ Edit your teams at sports-reminder-ui.vercel.app
          </a>
          <div style="margin-top:12px;font-size:12px;color:#999;">
            <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
          <div style="margin-top:8px;text-align:center;"><a href="https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT" target="_blank" style="color:#25D366;text-decoration:none;font-size:12px;">📱 Get updates on WhatsApp</a></div>
          </div>
        </div>
      </div>
    </body></html>
    """


def send_tournament_email(to: str, matches_by_day: dict):
    """Send the full tournament schedule email."""
    total = sum(len(v) for v in matches_by_day.values())
    subject = f"🏆 FIFA World Cup 2026 — Full Schedule — {total} matches"


    plain = f"FIFA World Cup 2026 — Full Schedule ({total} matches, Israel time)\n\n"
    for date_str, matches in matches_by_day.items():
        dt     = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        plain += f"{dt.strftime('%A, %b')} {dt.day}\n"
        for m in matches:
            plain += f"  ⚽  {m['home']} Vs {m['away']}  —  {m['time']}\n"
            t_note = m.get("tournament_note", "")
            if t_note:
                plain += f"      {t_note}\n"
        plain += "\n"

    html = build_tournament_email_html(matches_by_day)
    return send_raw_email(to, subject, html, plain, email_type="tournament")


# ─────────────────────────────────────────────────────────────────────────────
# PLAYER STATS — fetch last completed game stats for a watched player
# ─────────────────────────────────────────────────────────────────────────────
def fetch_player_last_game_stats(player: dict) -> dict | None:
    """
    Find the most recent completed NBA game for the player's team (checking
    yesterday + today in UTC, to cover Israeli overnight games).
    Returns a dict with game result + key stats, or None if not found.
    """
    now_utc = datetime.datetime.utcnow()
    dates_to_check = [
        (now_utc - datetime.timedelta(days=1)).strftime("%Y%m%d"),
        now_utc.strftime("%Y%m%d"),
    ]

    for date_str in dates_to_check:
        try:
            url = (f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
                   f"/scoreboard?dates={date_str}")
            data = fetch_json(url)
        except Exception:
            continue

        for event in data.get("events", []):
            comp        = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            # Only completed games featuring our team
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            our_team = next(
                (c for c in competitors if c.get("team", {}).get("id") == player["team_id"]),
                None
            )
            if not our_team:
                continue

            # Fetch full box score for this game
            game_id = event["id"]
            try:
                summary = fetch_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
                    f"/summary?event={game_id}"
                )
            except Exception:
                continue

            # Find the player row in the boxscore
            for team_data in summary.get("boxscore", {}).get("players", []):
                for cat in team_data.get("statistics", []):
                    athlete = next(
                        (a for a in cat.get("athletes", [])
                         if a.get("athlete", {}).get("id") == player["espn_id"]),
                        None
                    )
                    if not athlete:
                        continue

                    labels   = cat.get("labels", [])
                    stats    = athlete.get("stats", [])
                    stat_map = {labels[i]: stats[i]
                                for i in range(min(len(labels), len(stats)))}

                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

                    try:
                        game_utc_dt = datetime.datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ")
                        il_offset   = _israel_utc_offset_h(game_utc_dt)
                        game_il     = game_utc_dt + datetime.timedelta(hours=il_offset)
                        game_date_il = game_il.strftime("%d/%m")
                    except Exception:
                        game_date_il = date_str

                    return {
                        "player_name":  player["display_name"],
                        "home":         home["team"]["displayName"],
                        "away":         away["team"]["displayName"],
                        "home_score":   home.get("score", ""),
                        "away_score":   away.get("score", ""),
                        "won":          our_team.get("winner", False),
                        "game_date_il": game_date_il,
                        "pts":          stat_map.get("PTS", "?"),
                        "reb":          stat_map.get("REB", "?"),
                        "ast":          stat_map.get("AST", "?"),
                        "stl":          stat_map.get("STL", "?"),
                        "blk":          stat_map.get("BLK", "?"),
                        "fg":           stat_map.get("FG", "?"),
                        "three_pt":     stat_map.get("3PT", "?"),
                        "ft":           stat_map.get("FT", "?"),
                        "to":           stat_map.get("TO", "?"),
                        "pf":           stat_map.get("PF", "?"),
                        "plus_minus":   stat_map.get("+/-", "?"),
                        "min":          stat_map.get("MIN", "?"),
                        "dnp":          athlete.get("didNotPlay", False),
                    }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────────────────
def _gcal_url(match: dict, today: str) -> str | None:
    """Build a Google Calendar 'add event' URL from a match dict + today's date string."""
    if match.get("time") in (None, "TBD", ""):
        return None
    try:
        h, mi = map(int, match["time"].split(":"))
        # Use the game's Israel date (il_date) for correct calendar event date
        game_date = match.get("il_date", today)
        y, mo, d = map(int, game_date.split("-"))
        il_dt = datetime.datetime(y, mo, d, h, mi)
        # Estimate UTC: use il_dt minus 3h as rough UTC to determine DST offset
        rough_utc = il_dt - datetime.timedelta(hours=3)
        il_offset = _israel_utc_offset_h(rough_utc)
        utc_start = il_dt - datetime.timedelta(hours=il_offset)
        utc_end   = utc_start + datetime.timedelta(hours=2)
        start_s = utc_start.strftime("%Y%m%dT%H%M%SZ")
        end_s   = utc_end.strftime("%Y%m%dT%H%M%SZ")
        is_wc = match.get("is_world_cup") or match.get("league_id") == "fifa_world_cup"
        if is_wc:
            s_emoji = "🏆"
        else:
            sport_emoji_map = {"soccer": "⚽", "basketball": "🏀"}
            s_emoji = sport_emoji_map.get(match.get("sport", ""), "🏟️")
        title   = urllib.parse.quote(f"{s_emoji} {match['away']} Vs {match['home']}")
        details = urllib.parse.quote(match.get("league_name", ""))
        return (
            f"https://calendar.google.com/calendar/render"
            f"?action=TEMPLATE&text={title}&dates={start_s}/{end_s}&details={details}"
        )
    except Exception:
        return None


def build_email_html(matches: list[dict], today: str, player_stats: list[dict] | None = None) -> str:
    sport_emoji = {"soccer": "⚽", "basketball": "🏀"}
    rows = ""
    for m in matches:
        is_wc = m.get("is_world_cup") or m.get("league_id") == "fifa_world_cup"
        emoji = "🏆" if is_wc else sport_emoji.get(m["sport"], "🏟️")
        gcal = _gcal_url(m, today)
        gcal_html = (
            f'<div style="margin-top:5px;">'
            f'<a href="{gcal}" style="font-size:11px; color:#1a56db; text-decoration:none;">📅 Add to Calendar</a>'
            f'</div>'
        ) if gcal and m["time"] != "TBD" else ""
        # Playoff series info line (NBA) — daily email shows only playoff_note
        # (e.g. "East Finals - Game 1"), not series_summary ("Series starts X/X")
        playoff_html = ""
        p_note  = m.get("playoff_note", "")
        if p_note:
            playoff_html = f'<div style="font-size:11px; color:#9333ea; margin-top:2px; font-style:italic;">{p_note}</div>'
        # Tournament round info (World Cup)
        tournament_html = ""
        t_note = m.get("tournament_note", "")
        if t_note:
            tournament_html = f'<div style="font-size:11px; color:#b45309; margin-top:2px; font-style:italic;">{t_note}</div>'
        # Time display — TBD gets a muted style; "If Necessary" gets extra note
        is_if_necessary = "if necessary" in p_note.lower()
        # Show Israel date next to time when game falls on a different display date
        game_display_date = m.get("display_date", m.get("il_date", today))
        game_il_date = m.get("il_date", today)
        if m["time"] == "00:00" and game_display_date != game_il_date:
            _dd_dt = datetime.datetime.strptime(game_display_date, "%Y-%m-%d")
            _il_dt = datetime.datetime.strptime(game_il_date, "%Y-%m-%d")
            date_prefix = f'<div style="font-size:11px; color:#6b7280; margin-bottom:1px;">{_dd_dt.strftime("%a")}-{_il_dt.strftime("%a")} night</div>'
        elif game_display_date != today and m["time"] != "TBD":
            _g_dt = datetime.datetime.strptime(game_display_date, "%Y-%m-%d")
            _g_day_name = _g_dt.strftime("%a")  # e.g. "Tue"
            _g_date_str = f"{_g_day_name} {_g_dt.day}/{_g_dt.month}"
            date_prefix = f'<div style="font-size:11px; color:#6b7280; margin-bottom:1px;">{_g_date_str}</div>'
        else:
            date_prefix = ""
        if m["time"] == "TBD":
            time_html = '<span style="font-weight:600; color:#9ca3af;">TBD</span>'
            time_sub  = ('<div style="font-size:10px; color:#d97706;">if necessary</div>'
                         if is_if_necessary else "")
        else:
            time_html = f'{date_prefix}<span style="font-weight:600; color:#1a56db;">{m["time"]}</span>'
            time_sub  = '<div style="font-size:12px; color:#999;">Israel time</div>'

        # Build team names — World Cup uses "Vs" with flags; others use "@"
        if is_wc:
            home_flag = _country_flag_emoji(m.get("home_abbr", ""))
            away_flag = _country_flag_emoji(m.get("away_abbr", ""))
            home_display = f"{home_flag} {m['home']}" if home_flag else m["home"]
            away_display = f"{away_flag} {m['away']}" if away_flag else m["away"]
            # Star marker for tracked teams
            tracked = m.get("tracked_team", "")
            if tracked:
                if names_match(m["home"], tracked):
                    home_display = home_display
                elif names_match(m["away"], tracked):
                    away_display = away_display
            matchup_html = (f'{home_display}<br>'
                           f'<span style="font-size:13px; color:#888;">Vs</span><br>'
                           f'{away_display}')
        else:
            matchup_html = f'{m["away"]} @ {m["home"]}'
        rows += f"""
        <tr>
          <td style="padding:12px 16px; font-size:16px; border-bottom:1px solid #f0f0f0; vertical-align:top;">
            {emoji}
          </td>
          <td style="padding:12px 16px; border-bottom:1px solid #f0f0f0;">
            <div style="font-weight:600; color:#111;">{matchup_html}</div>
            <div style="font-size:13px; color:#666; margin-top:2px;">{m['league_name']}</div>
            {playoff_html}
            {tournament_html}
            {gcal_html}
          </td>
          <td style="padding:12px 16px; border-bottom:1px solid #f0f0f0; text-align:right;">
            {time_html}
            {time_sub}
          </td>
        </tr>"""

    # Build player stats HTML block
    player_stats_html = ""
    for ps in (player_stats or []):
        if ps.get("dnp"):
            player_stats_html += f"""
        <div style="margin:16px 0 0; padding:12px 16px; background:#f8fafc;
                    border-radius:8px; border-left:3px solid #94a3b8;">
          <div style="font-size:13px; font-weight:600; color:#64748b;">
            🏀 {ps['player_name']} | {ps['away']} @ {ps['home']} ({ps['game_date_il']})
          </div>
          <div style="font-size:14px; color:#64748b; margin-top:4px;">Did Not Play (DNP)</div>
        </div>"""
        else:
            result_color = "#16a34a" if ps["won"] else "#dc2626"
            result_text  = "Win" if ps["won"] else "Loss"
            pm_val       = ps.get("plus_minus", "?")
            try:
                pm_int = int(pm_val)
                pm_color = "#16a34a" if pm_int > 0 else ("#dc2626" if pm_int < 0 else "#64748b")
                pm_display = f"+{pm_int}" if pm_int > 0 else str(pm_int)
            except (ValueError, TypeError):
                pm_color   = "#64748b"
                pm_display = pm_val
            player_stats_html += f"""
        <div style="margin:16px 0 0; padding:12px 16px; background:#eff6ff;
                    border-radius:8px; border-left:3px solid #1a56db;">
          <div style="font-size:13px; font-weight:600; color:#1a56db; margin-bottom:8px;">
            🏀 {ps['player_name']} | {ps['away']} {ps['away_score']}–{ps['home_score']} {ps['home']}
            &nbsp;<span style="color:{result_color}; font-weight:700;">{result_text}</span>
            <span style="font-weight:400; color:#64748b;"> ({ps['game_date_il']})</span>
          </div>
          <table style="width:100%; border-collapse:collapse; margin-bottom:8px;">
            <tr>
              <td style="text-align:center; padding:4px 6px; border-right:1px solid #bfdbfe;">
                <div style="font-size:19px; font-weight:700; color:#64748b;">{ps['min']}</div>
                <div style="font-size:10px; color:#94a3b8; text-transform:uppercase; margin-top:2px;">MIN</div>
              </td>
              <td style="text-align:center; padding:4px 6px; border-right:1px solid #bfdbfe;">
                <div style="font-size:19px; font-weight:700; color:#1a56db;">{ps['pts']}</div>
                <div style="font-size:10px; color:#94a3b8; text-transform:uppercase; margin-top:2px;">PTS</div>
              </td>
              <td style="text-align:center; padding:4px 6px; border-right:1px solid #bfdbfe;">
                <div style="font-size:19px; font-weight:700; color:#111;">{ps['reb']}</div>
                <div style="font-size:10px; color:#94a3b8; text-transform:uppercase; margin-top:2px;">REB</div>
              </td>
              <td style="text-align:center; padding:4px 6px; border-right:1px solid #bfdbfe;">
                <div style="font-size:19px; font-weight:700; color:#111;">{ps['ast']}</div>
                <div style="font-size:10px; color:#94a3b8; text-transform:uppercase; margin-top:2px;">AST</div>
              </td>
              <td style="text-align:center; padding:4px 6px;">
                <div style="font-size:19px; font-weight:700; color:{pm_color};">{pm_display}</div>
                <div style="font-size:10px; color:#94a3b8; text-transform:uppercase; margin-top:2px;">+/-</div>
              </td>
            </tr>
          </table>
          <div style="font-size:12px; color:#64748b; border-top:1px solid #bfdbfe; padding-top:6px;">
            FG {ps['fg'].replace('-','/')} &nbsp;·&nbsp; 3PT {ps['three_pt'].replace('-','/')} &nbsp;·&nbsp; FT {ps['ft'].replace('-','/')}
            &nbsp;·&nbsp; {ps['stl']} STL &nbsp;·&nbsp; {ps['blk']} BLK
            &nbsp;·&nbsp; {ps['to']} TO &nbsp;·&nbsp; {ps['pf']} PF
          </div>
        </div>"""

    _dt = datetime.datetime.strptime(today, "%Y-%m-%d")
    date_formatted = _dt.strftime("%A, %B ") + str(_dt.day)
    return f"""
    <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                       background:#f8fafc; margin:0; padding:20px;">
      <div style="max-width:520px; margin:0 auto; background:white; border-radius:16px;
                  overflow:hidden; box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <div style="background:#0f172a; padding:20px 24px;">
          <div style="font-size:40px; margin-bottom:4px; line-height:1;">🏟️</div>
          <h1 style="color:white; margin:0; font-size:18px; font-weight:700;">
            Sports Reminder
          </h1>
          <p style="color:#94a3b8; margin:4px 0 0; font-size:13px;">{date_formatted}</p>
        </div>
        <div style="padding:16px 24px 8px;">
          {''.join([
            f'<p style="color:#374151; margin:0 0 16px; font-size:14px;">You have <strong>{len(matches)} {"match" if len(matches)==1 else "matches"}</strong> ahead:</p>',
            f'<table style="width:100%; border-collapse:collapse;">{rows}</table>'
          ]) if matches else ''}
          {player_stats_html}
        </div>
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb;">
          <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=daily"
             style="font-size:12px; color:#6b7280; text-decoration:none;">
            ✏️ Edit your teams at sports-reminder-ui.vercel.app
          </a>
          <div style="margin-top:12px;font-size:12px;color:#999;">
            <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
          <div style="margin-top:8px;text-align:center;"><a href="https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT" target="_blank" style="color:#25D366;text-decoration:none;font-size:12px;">📱 Get updates on WhatsApp</a></div>
          </div>
        </div>
      </div>
    </body></html>
    """

def send_email(to: str, matches: list[dict], today: str, player_stats: list[dict] | None = None):
    _dt2 = datetime.datetime.strptime(today, "%Y-%m-%d")
    date_str = _dt2.strftime("%b ") + str(_dt2.day)
    if not matches and player_stats:
        ps = player_stats[0]
        if ps.get("dnp"):
            subject = f"🏀 {ps['player_name']} — DNP — {ps['game_date_il']}"
        else:
            result = "W" if ps["won"] else "L"
            subject = f"🏀 {ps['player_name']} — {ps['pts']} pts / {ps['reb']} reb / {ps['ast']} ast ({result}) — {ps['game_date_il']}"
    else:
        wc_count    = sum(1 for m in matches if m.get("is_world_cup") or m.get("league_id") == "fifa_world_cup")
        other_count = len(matches) - wc_count
        if wc_count and not other_count:
            subject = f"🏆 World Cup — {wc_count} match{'es' if wc_count!=1 else ''} — {date_str}"
        elif wc_count and other_count:
            subject = f"🏆 {wc_count} WC + {other_count} other — {date_str}"
        else:
            subject = f"🏟️ {len(matches)} match{'es' if len(matches)!=1 else ''} ahead — {date_str}"


    # Plain text fallback
    plain = f"Your matches for {date_str}:\n\n"
    for m in matches:
        is_wc = m.get("is_world_cup") or m.get("league_id") == "fifa_world_cup"
        sep = " Vs " if is_wc else " @ "
        _pt_mn = ""
        if m["time"] == "00:00" and m.get("display_date") and m.get("il_date") and m["display_date"] != m["il_date"]:
            _pt_mn = f" ({datetime.datetime.strptime(m['display_date'],'%Y-%m-%d').strftime('%a')}-{datetime.datetime.strptime(m['il_date'],'%Y-%m-%d').strftime('%a')} night)"
        plain += f"  {m['away']}{sep}{m['home']}  —  {m['league_name']}  —  {m['time']}{_pt_mn} (IL)\n"
    if player_stats:
        plain += "\n---\n"
        for ps in player_stats:
            if ps.get("dnp"):
                plain += f"\n🏀 {ps['player_name']} Did Not Play ({ps['game_date_il']})\n"
            else:
                result = "ניצחון" if ps["won"] else "הפסד"
                pm_str = ps.get("plus_minus", "?")
                try:
                    pm_str = f"+{pm_str}" if int(pm_str) > 0 else str(pm_str)
                except (ValueError, TypeError):
                    pass
                plain += (f"\n🏀 {ps['player_name']} | {ps['away']} {ps['away_score']}–{ps['home_score']} {ps['home']}"
                          f" ({result}, {ps['game_date_il']})\n"
                          f"   {ps['min']} min · {ps['pts']} pts · {ps['reb']} reb · {ps['ast']} ast · {pm_str}\n"
                          f"   FG {ps['fg'].replace('-','/')} · 3PT {ps['three_pt'].replace('-','/')} · FT {ps['ft'].replace('-','/')}"
                          f" · {ps['stl']} stl · {ps['blk']} blk · {ps['to']} to · {ps['pf']} pf\n")
    plain += f"\nEdit your teams: https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=daily"

    html = build_email_html(matches, today, player_stats)
    return send_raw_email(to, subject, html, plain, email_type="morning")

# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY DIGEST — helper, HTML builder, sender
# ─────────────────────────────────────────────────────────────────────────────

def _week_label(start_date: str) -> str:
    """Returns e.g. 'Apr 12–18' or 'Apr 28 – May 4'."""
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end   = start + datetime.timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}"
    return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"


def build_weekly_email_html(matches_by_day: dict, start_date: str) -> str:
    week_lbl    = _week_label(start_date)
    sport_emoji = {"soccer": "⚽", "basketball": "🏀"}

    if not matches_by_day:
        body_html = """
        <div style="padding:32px 24px; text-align:center; color:#6b7280; font-size:14px;">
          No matches this week for your teams. Enjoy the break! ⚽🏀
        </div>"""
    else:
        days_html = ""
        for date_str, matches in matches_by_day.items():
            dt        = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            day_label = dt.strftime("%A, %b ") + str(dt.day)
            rows      = ""
            for m in matches:
                is_wc    = m.get("is_world_cup") or m.get("league_id") == "fifa_world_cup"
                emoji    = "🏆" if is_wc else sport_emoji.get(m["sport"], "🏟️")
                gcal     = _gcal_url(m, date_str)
                gcal_html = (
                    f'<div style="margin-top:4px;">'
                    f'<a href="{gcal}" style="font-size:11px; color:#1a56db; text-decoration:none;">📅 Add to Calendar</a>'
                    f'</div>'
                ) if gcal and m["time"] != "TBD" else ""
                # Playoff series info line (NBA)
                playoff_html = ""
                p_note  = m.get("playoff_note", "")
                p_series = _format_series_summary(m.get("series_summary", ""), m.get("il_date", ""))
                if p_note or p_series:
                    parts = []
                    if p_note:
                        parts.append(p_note)
                    if p_series:
                        parts.append(p_series)
                    _joined = " · ".join(parts)
                    playoff_html = f'<div style="font-size:11px; color:#9333ea; margin-top:2px; font-style:italic;">{_joined}</div>'
                # Tournament round info (World Cup)
                tournament_html = ""
                t_note = m.get("tournament_note", "")
                if t_note:
                    tournament_html = f'<div style="font-size:11px; color:#b45309; margin-top:2px; font-style:italic;">{t_note}</div>'
                # Time display — TBD gets a muted style; "If Necessary" gets extra note
                is_if_necessary = "if necessary" in p_note.lower()
                if m["time"] == "TBD":
                    tbd_sub = ('<div style="font-size:10px; color:#d97706;">if nec.</div>'
                               if is_if_necessary else "")
                    time_html = f'<span style="font-weight:600; color:#9ca3af;">TBD</span>{tbd_sub}'
                else:
                    _w_midnight = ""
                    if m["time"] == "00:00" and m.get("display_date") and m.get("il_date") and m["display_date"] != m["il_date"]:
                        _w_dd_dt = datetime.datetime.strptime(m["display_date"], "%Y-%m-%d")
                        _w_il_dt = datetime.datetime.strptime(m["il_date"], "%Y-%m-%d")
                        _w_midnight = f'<div style="font-size:11px; color:#6b7280; margin-bottom:1px;">{_w_dd_dt.strftime("%a")}-{_w_il_dt.strftime("%a")} night</div>'
                    time_html = f'{_w_midnight}<span style="font-weight:600; color:#1a56db;">{m["time"]}</span>'
                # Build matchup text — World Cup uses "Vs" with flags
                if is_wc:
                    home_flag = _country_flag_emoji(m.get("home_abbr", ""))
                    away_flag = _country_flag_emoji(m.get("away_abbr", ""))
                    h_disp = f"{home_flag} {m['home']}" if home_flag else m["home"]
                    a_disp = f"{away_flag} {m['away']}" if away_flag else m["away"]
                    tracked_t = m.get("tracked_team", "")
                    if tracked_t:
                        if names_match(m["home"], tracked_t):
                            h_disp = h_disp
                        elif names_match(m["away"], tracked_t):
                            a_disp = a_disp
                    matchup_str = (f'{h_disp}<br>'
                                   f'<span style="font-size:12px; color:#888;">Vs</span><br>'
                                   f'{a_disp}')
                else:
                    matchup_str = f'{m["away"]} @ {m["home"]}'
                rows += f"""
                <tr>
                  <td style="padding:10px 12px; font-size:15px; border-bottom:1px solid #f0f0f0; width:32px; vertical-align:top;">{emoji}</td>
                  <td style="padding:10px 12px; border-bottom:1px solid #f0f0f0;">
                    <div style="font-weight:600; color:#111;">{matchup_str}</div>
                    <div style="font-size:12px; color:#666; margin-top:2px;">{m['league_name']}</div>
                    {playoff_html}
                    {tournament_html}
                    {gcal_html}
                  </td>
                  <td style="padding:10px 12px; border-bottom:1px solid #f0f0f0; text-align:right; white-space:nowrap;">
                    {time_html}
                  </td>
                </tr>"""
            days_html += f"""
            <div>
              <div style="padding:8px 16px; font-size:11px; font-weight:700; color:#6b7280;
                          text-transform:uppercase; letter-spacing:0.06em;
                          background:#f8fafc; border-top:1px solid #e5e7eb;">{day_label}</div>
              <table style="width:100%; border-collapse:collapse;">{rows}</table>
            </div>"""
        body_html = f'<div>{days_html}</div>'

    return f"""
    <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                       background:#f8fafc; margin:0; padding:20px;">
      <div style="max-width:520px; margin:0 auto; background:white; border-radius:16px;
                  overflow:hidden; box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <div style="background:#0f172a; padding:20px 24px;">
          <div style="font-size:40px; margin-bottom:4px; line-height:1;">🗓️</div>
          <h1 style="color:white; margin:0; font-size:18px; font-weight:700;">Upcoming Matches</h1>
          <p style="color:#94a3b8; margin:4px 0 0; font-size:13px;">{week_lbl} · Israel time</p>
        </div>
        {body_html}
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb;">
          <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=weekly"
             style="font-size:12px; color:#6b7280; text-decoration:none;">
            ✏️ Edit your teams at sports-reminder-ui.vercel.app
          </a>
          <div style="margin-top:12px;font-size:12px;color:#999;">
            <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
          <div style="margin-top:8px;text-align:center;"><a href="https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT" target="_blank" style="color:#25D366;text-decoration:none;font-size:12px;">📱 Get updates on WhatsApp</a></div>
          </div>
        </div>
      </div>
    </body></html>
    """


def send_weekly_email(to: str, matches_by_day: dict, start_date: str):
    week_lbl = _week_label(start_date)
    total    = sum(len(v) for v in matches_by_day.values())
    subject  = f"🗓️ No upcoming matches — {week_lbl}" if total == 0 \
               else f"🗓️ Upcoming matches — {week_lbl}"


    if total == 0:
        plain = f"No matches this week for your teams. Enjoy the break! ⚽🏀\n\nEdit your teams: https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=weekly"
    else:
        plain = f"Upcoming matches — {week_lbl} (Israel time)\n\n"
        for date_str, matches in matches_by_day.items():
            dt     = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            plain += f"{dt.strftime('%A, %b')} {dt.day}\n"
            for m in matches:
                icon = "🏀" if m["sport"] == "basketball" else "⚽"
                _wpt_mn = ""
                if m["time"] == "00:00" and m.get("display_date") and m.get("il_date") and m["display_date"] != m["il_date"]:
                    _wpt_mn = f" ({datetime.datetime.strptime(m['display_date'],'%Y-%m-%d').strftime('%a')}-{datetime.datetime.strptime(m['il_date'],'%Y-%m-%d').strftime('%a')} night)"
                plain += f"  {icon}  {m['away']} @ {m['home']}  —  {m['league_name']}  —  {m['time']}{_wpt_mn}\n"
                p_note  = m.get("playoff_note", "")
                p_series = _format_series_summary(m.get("series_summary", ""), m.get("il_date", ""))
                if p_note or p_series:
                    parts = [p for p in [p_note, p_series] if p]
                    _joined = " · ".join(parts)
                    plain += f"      {_joined}\n"
            plain += "\n"
        plain += f"Edit your teams: https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=weekly"

    html = build_weekly_email_html(matches_by_day, start_date)
    return send_raw_email(to, subject, html, plain, email_type="weekly")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
MOCK_TEAMS = [
    {"name": "Hapoel Tel Aviv",  "sport": "basketball", "leagueId": "euroleague",        "league": "EuroLeague"},
    {"name": "Maccabi Tel Aviv", "sport": "basketball", "leagueId": "euroleague",        "league": "EuroLeague"},
    {"name": "FC Barcelona",     "sport": "soccer",     "leagueId": "champions_league",  "league": "Champions League"},
    {"name": "Hapoel Tel Aviv",  "sport": "soccer",     "leagueId": "israeli_pl_soccer", "league": "Israeli Premier League"},
]

MOCK_MATCHES = [
    {"home": "Real Madrid",      "away": "Hapoel Tel Aviv", "time": "21:00",
     "tracked_team": "Hapoel Tel Aviv",  "league_name": "EuroLeague",        "sport": "basketball"},
    {"home": "Maccabi Tel-Aviv", "away": "Panathinaikos",   "time": "19:30",
     "tracked_team": "Maccabi Tel Aviv", "league_name": "EuroLeague",        "sport": "basketball"},
    {"home": "FC Barcelona",     "away": "Bayern Munich",   "time": "22:00",
     "tracked_team": "FC Barcelona",     "league_name": "Champions League",  "sport": "soccer"},
]


# ── Synthetic user health check ──────────────────────────────────────────────────────────────

SYNTHETIC_ALERT_EMAIL = "ronen6213@gmail.com"

def _send_synthetic_alert(mode, today, failures):
    """Send alert email to admin when synthetic user test fails."""
    if not failures:
        return
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    report_lines = [
        "=" * 60,
        "SYNTHETIC TEST FAILURE REPORT",
        "=" * 60,
        f"Mode: {mode}",
        f"Date: {today}",
        f"Timestamp: {timestamp}",
        f"Failures: {len(failures)}",
        "",
    ]
    for i, f in enumerate(failures, 1):
        report_lines.append(f"--- Failure #{i} ---")
        report_lines.append(f"Type: {f.get('type', 'unknown')}")
        report_lines.append(f"Error: {f.get('error', 'N/A')}")
        if f.get('traceback'):
            report_lines.append(f"Traceback:\n{f['traceback']}")
        if f.get('details'):
            for k, v in f['details'].items():
                report_lines.append(f"{k}: {v}")
        report_lines.append("")
    report_lines.extend([
        "--- DIAGNOSTIC INFO ---",
        "User doc_id: synthetic_health_check",
        "User email: ronen6213+synthetic@gmail.com",
        "Script: sports_reminder.py",
        f"Mode flag: {'--weekly' if mode == 'weekly' else '--player-stats' if mode == 'stats' else '--no-stats'}",
        "",
        "ACTION: Paste this entire email body to Claude to start debugging.",
        "=" * 60,
    ])
    plain = "\n".join(report_lines)
    html_esc = plain.replace("\n", "<br>\n")
    html = f"""<html><body style="font-family:monospace;font-size:13px;background:#1a1a2e;color:#e0e0e0;padding:20px;">
<div style="max-width:700px;margin:0 auto;">
<div style="background:#d32f2f;color:white;padding:12px 20px;border-radius:8px 8px 0 0;">
<h2 style="margin:0;">U0001f6a8 Synthetic Test FAILED — {mode}</h2>
</div>
<div style="background:#2d2d44;padding:20px;border-radius:0 0 8px 8px;">
<pre style="white-space:pre-wrap;word-break:break-word;color:#e0e0e0;">{html_esc}</pre>
</div></div></body></html>"""
    subject = f"U0001f6a8 SportsReminder Synthetic FAILED — {mode} — {today}"
    try:
        send_raw_email(SYNTHETIC_ALERT_EMAIL, subject, html, plain, email_type="synthetic_alert")
        print(f"   U0001f6a8 Synthetic alert sent to {SYNTHETIC_ALERT_EMAIL}")
    except Exception as e:
        print(f"   ❌ Failed to send synthetic alert: {e}")


def main():
    args           = sys.argv[1:]
    send_mode      = "--send"        in args
    test_mode      = "--test"        in args
    mock_mode      = "--mock"        in args
    player_stats_m = "--player-stats" in args or "--stats-only" in args  # 07:00 IL
    no_stats       = "--no-stats"    in args   # 09:00 IL — morning games only
    weekly_mode    = "--weekly"      in args   # Saturday 22:00 IL — weekly digest
    tournament_mode = "--full-tournament" in args  # One-off: full WC schedule
    # --simulate-date YYYY-MM-DD: override today's date for testing
    sim_date = None
    for i, a in enumerate(args):
        if a == "--simulate-date" and i + 1 < len(args):
            sim_date = args[i + 1]
    today = sim_date if sim_date else today_israel()
    # --test-user EMAIL: send only to this user (for QA without spamming all users)
    test_user_email = None
    for i, a in enumerate(args):
        if a == "--test-user" and i + 1 < len(args):
            test_user_email = args[i + 1]

    print(f"\n🗓️  Sports Reminder — {today}")
    print("=" * 50)
    if test_user_email:
        print(f"\U0001f9ea TEST USER MODE: only sending to {test_user_email}")

    if mock_mode:
        print("\n🧪 MOCK MODE — using fake teams & games (no network calls)\n")
        tracked = MOCK_TEAMS
        matches = MOCK_MATCHES
        print(f"   Tracked teams ({len(tracked)}):")
        for t in tracked:
            print(f"   • {t['name']}  [{t['league']} / {t['sport']}]")
        print(f"\n🎯 {len(matches)} mock match(es) today:\n")
        for m in matches:
            emoji = "⚽" if m["sport"] == "soccer" else "🏀"
            print(f"  {emoji}  {m['away']} @ {m['home']}")
            print(f"      {m['league_name']}  —  {m['time']} (Israel time)\n")
        if send_mode:
            print(f"📧 Sending mock email to {GMAIL_SENDER}...")
            send_email(GMAIL_SENDER, matches, today)
        else:
            # Show the HTML that would be sent
            html = build_email_html(matches, today)
            out_path = "/tmp/sports_reminder_preview.html"
            with open(out_path, "w") as f:
                f.write(html)
            print(f"📄 Email HTML preview saved to: {out_path}")
            print("   Open it in a browser to see how the email looks.")
            print("\n   Run with --mock --send to actually send it.")
        return

    # ── Full tournament mode (one-off WC schedule) ──────────────────────────
    if tournament_mode:
        print("\n🏆 Full Tournament mode — fetching all FIFA World Cup 2026 games...")
        all_u = load_all_users()
        if test_user_email:
            all_u = [u for u in all_u if u.get("email","").lower() == test_user_email.lower()]
        tracked = all_u[0]["teams"] if all_u else []
        tracked_names = {t["name"] for t in tracked} if tracked else set()
        print(f"   {len(tracked_names)} tracked team(s) found")
        matches_by_day = fetch_full_tournament_games(tracked_names)
        total = sum(len(v) for v in matches_by_day.values())
        print(f"\n🏆 {total} match(es) found across {len(matches_by_day)} day(s)")
        for date_str, day_matches in matches_by_day.items():
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            print(f"\n  {dt.strftime('%A, %b')} {dt.day}: {len(day_matches)} game(s)")
            for m in day_matches:
                print(f"    ⚽  {m['home']} Vs {m['away']}  —  {m['time']}")
        if send_mode:
            print(f"\n📧 Sending tournament email to {GMAIL_SENDER}...")
            send_tournament_email(GMAIL_SENDER, matches_by_day)
        else:
            print("\nℹ️  Dry-run. Add --send to send the tournament email.")
        return

    # ── Load all users ──────────────────────────────────────────────────────
    print(f"\n📥 Loading users...")
    users = load_all_users()
    if test_user_email:
        users = [u for u in users if u.get("email","").lower() == test_user_email.lower()]
        print(f"   \U0001f9ea TEST MODE: filtered to {len(users)} user(s) matching {test_user_email}")
    if not users:
        print("   No active users found. Exiting.")
        return
    print(f"   Found {len(users)} active user(s): {', '.join(u['display_name'] for u in users)}")

    # ── Load global config ────────────────────────────────────────────────
    global_config = load_global_config()
    wc_mode = global_config.get("world_cup_mode", False)
    if wc_mode:
        print(f"   🏆 World Cup mode ON (global)")

    # ── Weekly digest mode (Saturday night, 20:00 IL) ─────────────────────
    if weekly_mode:
        print(f"\n📅 Weekly digest mode — {today}")
        synthetic_failures = []
        for user in users:
            if user.get("emails_paused") and not test_mode:
                print(f"\n  \u23f8\ufe0f {user['display_name']}: emails paused \u2014 skipping")
                continue
            if not user.get("weekly_digest") and not test_mode:
                print(f"\n   ⏭️  {user['display_name']}: weekly digest disabled → skipping")
                continue
            try:
                tracked = user["teams"]
                if not tracked:
                    print(f"\n   ⏭️  {user['display_name']}: no tracked teams → skipping")
                    continue
                print(f"\n   👤 {user['display_name']} ({len(tracked)} teams)...")
                matches_by_day = find_week_matches(tracked, today, world_cup_mode=wc_mode, now_il_time=None if sim_date else now_israel_time())
                total = sum(len(v) for v in matches_by_day.values())
                print(f"      🗓️  {total} match(es) across {len(matches_by_day)} day(s)")
                if send_mode:
                    ok = send_weekly_email(user["email"], matches_by_day, today)
                    if user.get("synthetic") and not ok:
                        synthetic_failures.append({"type": "send_failed", "error": "send_weekly_email returned False", "details": {"total_matches": total}})
                else:
                    print(f"      ℹ️  Dry-run (add --send)")
            except Exception as e:
                print(f"   ❌ {user['display_name']}: weekly email failed — {e}")
                if user.get("synthetic"):
                    import traceback as _tb
                    synthetic_failures.append({"type": "exception", "error": str(e), "traceback": _tb.format_exc()})
        if synthetic_failures:
            _send_synthetic_alert("weekly", today, synthetic_failures)
        return

    # ── Multi-player stats mode (post-game email, 07:00 IL) ──────────────
    if player_stats_m:
        print(f"\n📊 Multi-player stats mode")
        synthetic_failures = []
        for user in users:
            if user.get("emails_paused") and not test_mode:
                print(f"\n  \u23f8\ufe0f {user['display_name']}: emails paused \u2014 skipping")
                continue
            try:
                print(f"\n   👤 {user['display_name']}...")
                send_player_stats_emails(
                    doc_id=user["doc_id"],
                    gmail_user=GMAIL_SENDER,
                    gmail_pass=GMAIL_APP_PASSWORD,
                    target_date=today,
                    send=send_mode,
                )
            except Exception as e:
                print(f"   ❌ {user['display_name']}: stats email failed — {e}")
                if user.get("synthetic"):
                    import traceback as _tb
                    synthetic_failures.append({"type": "exception", "error": str(e), "traceback": _tb.format_exc()})
        if synthetic_failures:
            _send_synthetic_alert("stats", today, synthetic_failures)
        return

    # ── Daily morning email (09:00 IL) ────────────────────────────────────

    # 1. Collect all unique leagues from all users
    all_leagues = set()
    for user in users:
        for t in user["teams"]:
            all_leagues.add(t["leagueId"])
    print(f"\n🔍 Fetching games for {len(all_leagues)} league(s)...")

    # 2. Fetch games once per league (the expensive step)
    games_by_league = fetch_league_games(all_leagues, today)

    # 2b. World Cup games (global, fetched once)
    wc_games = []
    if wc_mode:
        all_tracked_names = set()
        for user in users:
            for t in user["teams"]:
                all_tracked_names.add(t["name"])
        wc_games = fetch_all_world_cup_games(today, all_tracked_names)
        print(f"   🏆 {len(wc_games)} WC game(s) today")

    # 3. Per-user: filter matches → send email
    synthetic_failures = []
    for user in users:
        if user.get("emails_paused") and not test_mode:
            print(f"\n  \u23f8\ufe0f {user['display_name']}: emails paused \u2014 skipping")
            continue
        try:
            tracked = user["teams"]
            if not tracked and not wc_mode:
                print(f"\n   ⏭️  {user['display_name']}: no tracked teams → skipping")
                continue

            print(f"\n   👤 {user['display_name']} ({len(tracked)} teams)")

            # Filter pre-fetched games by this user's teams
            matches = filter_matches_for_user(tracked, games_by_league, today)

            # Merge WC games if world_cup_mode is on
            if wc_mode and wc_games:
                user_tracked_names = {t["name"] for t in tracked}
                existing_keys = {f"{m['home']}_{m['away']}_fifa_world_cup" for m in matches
                                 if m.get("league_id") == "fifa_world_cup"}
                for wc in wc_games:
                    key = f"{wc['home']}_{wc['away']}_fifa_world_cup"
                    if key not in existing_keys:
                        wc_copy = {**wc, "tracked_team": ""}
                        for tname in user_tracked_names:
                            if names_match(wc_copy["home"], tname) or names_match(wc_copy["away"], tname):
                                wc_copy["tracked_team"] = tname
                                break
                        if not wc_copy["tracked_team"]:
                            continue
                        matches.append(wc_copy)
                        existing_keys.add(key)
                    else:
                        for m in matches:
                            if m.get("league_id") == "fifa_world_cup" and \
                               m["home"] == wc["home"] and m["away"] == wc["away"]:
                                m["is_world_cup"] = True
                                break
                matches.sort(key=lambda m: (m.get("display_date", m.get("il_date", today)), m.get("il_date", today), m["time"]))

            wc_count = sum(1 for m in matches if m.get("is_world_cup") or m.get("league_id") == "fifa_world_cup")
            other_count = len(matches) - wc_count
            print(f"      🎯 {len(matches)} match(es) ({wc_count} WC + {other_count} other)")

            if test_mode:
                if not matches:
                    matches = [{"home": "Real Madrid", "away": "FC Barcelona",
                        "time": "21:00", "status": "Scheduled",
                        "tracked_team": "FC Barcelona", "league_name": "La Liga", "sport": "soccer"}]
                print(f"      📧 Test email → {user['email']}")
                send_email(user["email"], matches, today)

            elif send_mode:
                if matches:
                    print(f"      📧 Sending → {user['email']}")
                    ok = send_email(user["email"], matches, today)
                    if user.get("synthetic") and not ok:
                        synthetic_failures.append({"type": "send_failed", "error": "send_email returned False", "details": {"matches": len(matches)}})
                else:
                    print(f"      📭 No matches → no email")

            else:
                print(f"      ℹ️  Dry-run (add --send)")

        except Exception as e:
            print(f"   ❌ {user['display_name']}: daily email failed — {e}")
            if user.get("synthetic"):
                import traceback as _tb
                synthetic_failures.append({"type": "exception", "error": str(e), "traceback": _tb.format_exc()})
    if synthetic_failures:
        _send_synthetic_alert("morning", today, synthetic_failures)

if __name__ == "__main__":
    main()
