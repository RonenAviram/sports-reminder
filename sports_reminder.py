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
import smtplib
import os
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
    dst_start = datetime.datetime(y, 3, _last_weekday(y, 3, 4), 0, 0)   # Friâ00:00 UTC
    dst_end   = datetime.datetime(y, 10, _last_weekday(y, 10, 6), 1, 0)  # Sunâ01:00 UTC
    return 3 if dst_start <= at_utc < dst_end else 2

def _berlin_utc_offset_h(at_utc: datetime.datetime) -> int:
    """Europe/Berlin UTC offset at a given UTC moment: +2 (CEST, summer) or +1 (CET, winter).
    CEST starts last Sunday of March 01:00 UTC, ends last Sunday of Oct 01:00 UTC."""
    if _HAS_ZONEINFO:
        aware = at_utc.replace(tzinfo=datetime.timezone.utc).astimezone(_BERLIN_TZ)
        return int(aware.utcoffset().total_seconds() // 3600)
    y = at_utc.year
    cest_start = datetime.datetime(y, 3, _last_weekday(y, 3, 6), 1, 0)   # Sunâ01:00 UTC
    cest_end   = datetime.datetime(y, 10, _last_weekday(y, 10, 6), 1, 0)  # Sunâ01:00 UTC
    return 2 if cest_start <= at_utc < cest_end else 1

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# CONFIG â edit these before first run
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
FIREBASE_PROJECT   = "sports-reminder-55578"
FIREBASE_API_KEY   = "AIzaSyCd3C1_XN69r8lWUBYPndoGFxmDjnsjX1E"
FIRESTORE_DOC      = "ronen"          # the doc under configs/

GMAIL_SENDER       = "ronen6213@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")   # set env var or paste here

TIMEZONE_OFFSET    = 3    # Israel (UTC+3)

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# PLAYER WATCH â stats for specific players, shown in the morning email
# Each entry: display_name, espn_id, team_id (ESPN), team_name, league_id
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
PLAYER_WATCH = [
    {
        "display_name": "Deni Avdija",
        "espn_id":      "4683021",
        "team_id":      "22",           # Portland Trail Blazers
        "team_name":    "Portland Trail Blazers",
        "league_id":    "nba",
    },
]

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ESPN ENDPOINTS  (league_id â URL)
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
    "nba":                  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "euroleague":            None,    # uses EuroLeague official API (see below)
    "eurocup":               None,    # uses EuroCup official API (see below)
    "israeli_pl_basketball": None,    # uses TheSportsDB (ESPN returns empty for isr.1 basketball)
}

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# THESPORTSDB â Israeli leagues (ESPN isr.1 returns only partial team list)
# Free key "3" covers eventsday + eventsseason.
# Basketball ID=4474, Soccer ID=4644 (Israeli Premier League / Ligat HaAl)
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# EUROLEAGUE / EUROCUP OFFICIAL API
# ESPN dropped these â use api-live.euroleague.net instead
# Competition codes: E = EuroLeague, U = EuroCup
# Season codes: E2025 = 2025-26 EuroLeague, U2025 = 2025-26 EuroCup
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
EUROLEAGUE_COMPETITION_CODES = {
    "euroleague": ("E", "E2025"),
    "eurocup":    ("U", "U2025"),
}

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# TEAM NAME MATCHING
# Three-layer approach:
#   1. NOISE_TOKENS  â strip known sponsor words before comparing
#   2. Word-coverage â all words of user's name appear in API name (multi-word)
#   3. ALIASES       â last resort for abbreviations that can't be solved algorithmically
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# Sponsor / filler words that APIs inject into team names.
# These are NEVER part of a team's actual identity â safe to ignore.
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
    "AtlÃ©tico Madrid":             "Atletico Madrid",
    "Hoffenheim":                  "TSG Hoffenheim",
    "RB Leipzig":                  "RB Leipzig",
    "KÃ¶ln":                        "FC Koln",
    "Koln":                        "FC Koln",
    "Valencia":                    "Valencia CF",
    "Sevilla":                     "Sevilla FC",
    "Espanol":                     "RCD Espanyol",
    "Espanyol":                    "RCD Espanyol",
    "Osasuna":                     "CA Osasuna",
    "Alaves":                      "Deportivo Alaves",
    "AlavÃ©s":                      "Deportivo Alaves",
    "Paris Saint-Germain":         "Paris Saint-Germain",  # identity, resolves accent issues
    "PSG":                         "Paris Saint-Germain",
    "Lens":                        "Lens",                 # identity
    "Rennes":                      "Rennes",
    "Brentford":                   "Brentford",
    "Bournemouth":                 "Bournemouth",
    # Israeli teams â ESPN uses hyphens / apostrophes
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
    "EA7 Emporio Armani Milan":    "Olimpia Milano",       # full sponsor name â common name
    "EA7 EMPORIO ARMANI MILAN":    "Olimpia Milano",       # uppercase variant from API
    "Armani Milan":                "Olimpia Milano",
    "Olimpia Milano":              "Olimpia Milano",       # identity
    "Baskonia Vitoria-Gasteiz":    "Baskonia",
    "LDLC ASVEL VILLEURBANNE":     "ASVEL",                # EuroCup â ASVEL is 5 chars, below threshold
    "LDLC ASVEL Villeurbanne":     "ASVEL",
    # ESPN uses Italian name for Inter
    "Internazionale":              "Inter Milan",
    "FC Internazionale":           "Inter Milan",
    "FC Internazionale Milano":    "Inter Milan",
    # Rennes â ESPN uses full French name
    "Stade Rennais":               "Rennes",
    "Stade Rennais FC":            "Rennes",
    # MLS abbreviation
    "LAFC":                        "Los Angeles FC",
    # Red Bull Salzburg â ESPN sometimes uses RB abbreviation
    "RB Salzburg":                 "Red Bull Salzburg",
    "FC Red Bull Salzburg":        "Red Bull Salzburg",
    # Champions League / Europa League â ESPN sometimes uses shorter names
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
    # NBA â "LA" abbreviation for Los Angeles teams
    "Los Angeles Lakers":          "LA Lakers",
    "Los Angeles Clippers":        "LA Clippers",
    # MLS
    "Los Angeles FC":              "Los Angeles FC",
    "Nashville SC":                "Nashville SC",
}

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# HELPERS
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def strip_accents(s: str) -> str:
    """Remove accents: AtlÃ©tico â Atletico"""
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
      1. Alias table  â handles abbreviations (Man City â Manchester City)
      2. Exact norm   â handles accents, FC/AS prefixes
      3. Word-subset  â handles sponsor insertions (Maccabi Rapyd Tel Aviv â Maccabi Tel Aviv)
      4. Noise-strip + word-subset  â handles sponsor at start/end for short names
      5. Noise-strip + single-word  â "Panathinaikos" matches "Panathinaikos Aktor Athens"
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
    #    â after stripping "aktor": "panathinaikos athens"
    #    â "panathinaikos" is the FIRST word â match
    #    Require â¥6 chars to avoid false positives on city names like "Milan"
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

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# FIREBASE  â read user's tracked teams
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def load_tracked_teams(doc_id: str, enabled_only: bool = True) -> list[dict]:
    """
    Returns list of dicts: [{name, sport, leagueId, league, enabled}, ...]
    Uses Firebase REST API â no SDK needed.

    enabled_only=True  â skip teams where enabled=false (for dry-run / real send)
    enabled_only=False â return ALL teams regardless of enabled flag (for validation)
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
        print(f"â ï¸  Could not read Firestore: {e}")
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
    """Returns True if weekly digest email is enabled (default: False â opt-in feature)."""
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


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ESPN  â fetch today's games per league
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

    # For NBA and MLS, query both today and tomorrow (UTC) to catch overnight games
    if league_id in ("nba", "mls"):
        today_fmt    = today.replace("-", "")
        tomorrow_utc = (datetime.datetime.strptime(today, "%Y-%m-%d")
                        + datetime.timedelta(days=1)).strftime("%Y%m%d")
        all_events: list = []
        for dated_url in [f"{url}?dates={today_fmt}", f"{url}?dates={tomorrow_utc}"]:
            try:
                all_events.extend(fetch_json(dated_url).get("events", []))
            except Exception as e:
                print(f"  â ï¸  ESPN fetch failed for {league_id}: {e}")
        data = {"events": all_events}
    else:
        try:
            data = fetch_json(f"{url}?dates={today.replace('-', '')}")
        except Exception as e:
            print(f"  â ï¸  ESPN fetch failed for {league_id}: {e}")
            return []

    # tomorrow_utc string for date filtering (NBA only)
    tomorrow_utc_str = (datetime.datetime.strptime(today, "%Y-%m-%d")
                        + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    games = []
    for event in data.get("events", []):
        game_date = event.get("date", "")[:10]
        if league_id == "nba":
            if game_date != today and game_date != tomorrow_utc_str:
                continue
        else:
            if game_date != today:
                continue

        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        # Try to get game time in Israel timezone (DST-aware)
        game_utc_dt = None
        game_local  = None
        time_valid  = comp.get("timeValid", True)  # ESPN: false = placeholder time
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

        games.append({
            "home":      home["team"]["displayName"],
            "away":      away["team"]["displayName"],
            "time":      time_str,
            "il_date":   il_date,
            "status":    comp.get("status", {}).get("type", {}).get("description", ""),
            "league_id": league_id,
            "series_summary": series_summary,
            "playoff_note":   playoff_note,
        })
    return games

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# EUROLEAGUE OFFICIAL API â fetch today's games
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def fetch_euroleague_games(league_id: str, today: str) -> list[dict]:
    """
    Fetch today's games from the official EuroLeague/EuroCup API.
    Returns XML with all season results; we filter to today's date.
    Date format in XML: "Mar 24, 2026"  â  we compare with YYYY-MM-DD today.
    """
    _, season_code = EUROLEAGUE_COMPETITION_CODES[league_id]
    # Use /schedules (not /results) â results only has played games; schedules has everything
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
        print(f"  â ï¸  EuroLeague API fetch failed for {league_id}: {e}")
        return []

    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        print(f"  â ï¸  EuroLeague XML parse error for {league_id}: {e}")
        return []

    # Parse today's date for comparison
    try:
        today_dt = datetime.datetime.strptime(today, "%Y-%m-%d").date()
    except Exception:
        return []

    games = []
    # schedules API uses <item> elements; results API used <game>
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

        # Convert CET/CEST (Berlin) â Israel time (EuroLeague API returns startime in CET)
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
        })
    return games

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# THESPORTSDB â Israeli Basketball Premier League
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
        print(f"  â ï¸  TheSportsDB fetch failed for {league_id}: {e}")
        return []
    events = data.get("events") or []
    games = []
    for ev in events:
        if ev.get("strStatus") in ("FT", "AOT", "AET"):
            continue  # skip finished games
        home = ev.get("strHomeTeam", "")
        away = ev.get("strAwayTeam", "")
        # Always use strTime (UTC) + DST-aware offset.
        # strTimeLocal is unreliable â TheSportsDB returns UTC+2 (IST) even during IDT (UTC+3),
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

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# VALIDATION â check every tracked team can be found in its league's API
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# FIRESTORE WRITE â disable teams that fail validation
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# MATCHING â find which of your teams play today
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def find_my_matches(tracked: list[dict], today: str) -> list[dict]:
    """Cross-reference tracked teams with today's ESPN schedule."""
    # Group tracked teams by leagueId
    leagues_needed = set(t["leagueId"] for t in tracked)

    # Fetch games per league (cache per league_id)
    games_by_league: dict[str, list] = {}
    for league_id in leagues_needed:
        if league_id in ESPN_ENDPOINTS or league_id in EUROLEAGUE_COMPETITION_CODES:
            games_by_league[league_id] = fetch_todays_games(league_id, today)

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

    # Sort by time
    matches.sort(key=lambda m: m["time"])
    return matches


def find_week_matches(tracked: list[dict], start_date: str) -> dict:
    """Fetch matches for 7 days starting from start_date (serial).
    Games are bucketed by their *Israel date* (il_date), not the ESPN query date.
    This ensures NBA overnight games appear on the correct Israel day.
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
        print(f"  ð Fetching {date_str}...")
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
                # EuroLeague / TSDB games don't carry il_date â use the query date
                if "il_date" not in game:
                    game["il_date"] = date_str
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
        print(f"    â {len(matches)} match(es)")
        return matches

    # Fetch serially (one date at a time) with a pause between dates.
    # Parallelism caused ESPN rate-limiting â all leagues returning [] silently.
    all_matches: list[dict] = []
    for i, d in enumerate(espn_dates):
        try:
            all_matches.extend(fetch_for_espn_date(d))
        except Exception as e:
            print(f"  â ï¸  Week fetch failed for {d}: {e}")
        if i < len(espn_dates) - 1:
            _time.sleep(1.0)  # 1s between dates to avoid ESPN rate limiting

    # Re-bucket by Israel date; deduplicate globally; keep only [start_date, end_date]
    results: dict[str, list] = {}
    seen_global: set = set()
    for match in all_matches:
        il_date  = match.get("il_date", start_date)
        if il_date < start_date or il_date > end_date:
            continue
        game_key = f"{match['home']}_{match['away']}_{match['league_id']}"
        if game_key in seen_global:
            continue
        seen_global.add(game_key)
        results.setdefault(il_date, []).append(match)

    # Sort matches within each day by time
    for day_matches in results.values():
        day_matches.sort(key=lambda m: m["time"])

    return dict(sorted(results.items()))


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# PLAYER STATS â fetch last completed game stats for a watched player
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# EMAIL
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def _gcal_url(match: dict, today: str) -> str | None:
    """Build a Google Calendar 'add event' URL from a match dict + today's date string."""
    if match.get("time") in (None, "TBD", ""):
        return None
    try:
        h, mi = map(int, match["time"].split(":"))
        y, mo, d = map(int, today.split("-"))
        il_dt = datetime.datetime(y, mo, d, h, mi)
        # Estimate UTC: use il_dt minus 3h as rough UTC to determine DST offset
        rough_utc = il_dt - datetime.timedelta(hours=3)
        il_offset = _israel_utc_offset_h(rough_utc)
        utc_start = il_dt - datetime.timedelta(hours=il_offset)
        utc_end   = utc_start + datetime.timedelta(hours=2)
        start_s = utc_start.strftime("%Y%m%dT%H%M%SZ")
        end_s   = utc_end.strftime("%Y%m%dT%H%M%SZ")
        sport_emoji_map = {"soccer": "â½", "basketball": "ð"}
        s_emoji = sport_emoji_map.get(match.get("sport", ""), "ðï¸")
        title   = urllib.parse.quote(f"{s_emoji} {match['away']} Vs {match['home']}")
        details = urllib.parse.quote(match.get("league_name", ""))
        return (
            f"https://calendar.google.com/calendar/render"
            f"?action=TEMPLATE&text={title}&dates={start_s}/{end_s}&details={details}"
        )
    except Exception:
        return None


def build_email_html(matches: list[dict], today: str, player_stats: list[dict] | None = None) -> str:
    sport_emoji = {"soccer": "â½", "basketball": "ð"}
    rows = ""
    for m in matches:
        emoji = sport_emoji.get(m["sport"], "ðï¸")
        gcal = _gcal_url(m, today)
        gcal_html = (
            f'<div style="margin-top:5px;">'
            f'<a href="{gcal}" style="font-size:11px; color:#1a56db; text-decoration:none;">ð Add to Calendar</a>'
            f'</div>'
        ) if gcal and m["time"] != "TBD" else ""
        # Playoff series info line (NBA)
        playoff_html = ""
        p_note  = m.get("playoff_note", "")
        p_series = m.get("series_summary", "")
        if p_note or p_series:
            parts = []
            if p_note:
                parts.append(p_note)
            if p_series:
                parts.append(p_series)
            playoff_html = f'<div style="font-size:11px; color:#9333ea; margin-top:2px; font-style:italic;">{" \u00b7 ".join(parts)}</div>'
        # Time display
        if m["time"] == "TBD":
            time_html = '<span style="font-weight:600; color:#9ca3af;">TBD</span>'
            time_sub  = ""
        else:
            time_html = f'<span style="font-weight:600; color:#1a56db;">{m["time"]}</span>'
            time_sub  = '<div style="font-size:12px; color:#999;">Israel time</div>'
        rows += f"""
        <tr>
          <td style="padding:12px 16px; font-size:16px; border-bottom:1px solid #f0f0f0;">
            {emoji}
          </td>
          <td style="padding:12px 16px; border-bottom:1px solid #f0f0f0;">
            <div style="font-weight:600; color:#111;">{m['away']} @ {m['home']}</div>
            <div style="font-size:13px; color:#666; margin-top:2px;">{m['league_name']}</div>
            {playoff_html}
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
            ð {ps['player_name']} | {ps['away']} @ {ps['home']} ({ps['game_date_il']})
          </div>
          <div style="font-size:14px; color:#64748b; margin-top:4px;">Did Not Play (DNP)</div>
        </div>"""
        else:
            result_color = "#16a34a" if ps["won"] else "#dc2626"
            result_text  = "Win" if ps["won"] else "Loss"
            pm_val       = ps.get("plus_minus", "?")
            try:
                pm_color = "#16a34a" if int(pm_val) > 0 else ("#dc2626" if int(pm_val) < 0 else "#64748b")
                pm_display = f"+{pm_val}" if int(pm_val) > 0 else str(pm_val)
            except (ValueError, TypeError):
                pm_color   = "#64748b"
                pm_display = pm_val
            player_stats_html += f"""
        <div style="margin:16px 0 0; padding:12px 16px; background:#eff6ff;
                    border-radius:8px; border-left:3px solid #1a56db;">
          <div style="font-size:13px; font-weight:600; color:#1a56db; margin-bottom:8px;">
            ð {ps['player_name']} | {ps['away']} {ps['away_score']}â{ps['home_score']} {ps['home']}
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
            FG {ps['fg'].replace('-','/')} &nbsp;Â·&nbsp; 3PT {ps['three_pt'].replace('-','/')} &nbsp;Â·&nbsp; FT {ps['ft'].replace('-','/')}
            &nbsp;Â·&nbsp; {ps['stl']} STL &nbsp;Â·&nbsp; {ps['blk']} BLK
            &nbsp;Â·&nbsp; {ps['to']} TO &nbsp;Â·&nbsp; {ps['pf']} PF
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
          <div style="font-size:40px; margin-bottom:4px; line-height:1;">ðï¸</div>
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
          <a href="https://sports-reminder-ui.vercel.app"
             style="font-size:12px; color:#6b7280; text-decoration:none;">
            âï¸ Edit your teams at sports-reminder-ui.vercel.app
          </a>
        </div>
      </div>
    </body></html>
    """

def send_email(to: str, matches: list[dict], today: str, player_stats: list[dict] | None = None):
    if not GMAIL_APP_PASSWORD:
        print("â  GMAIL_APP_PASSWORD not set. Export it as an env variable:")
        print("    export GMAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx'")
        return False

    _dt2 = datetime.datetime.strptime(today, "%Y-%m-%d")
    date_str = _dt2.strftime("%b ") + str(_dt2.day)
    if not matches and player_stats:
        ps = player_stats[0]
        if ps.get("dnp"):
            subject = f"ð {ps['player_name']} â DNP â {ps['game_date_il']}"
        else:
            result = "W" if ps["won"] else "L"
            subject = f"ð {ps['player_name']} â {ps['pts']} pts / {ps['reb']} reb / {ps['ast']} ast ({result}) â {ps['game_date_il']}"
    else:
        subject  = f"ðï¸ {len(matches)} match{'es' if len(matches)!=1 else ''} ahead â {date_str}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = to

    # Plain text fallback
    plain = f"Your matches for {date_str}:\n\n"
    for m in matches:
        plain += f"  {m['away']} @ {m['home']}  â  {m['league_name']}  â  {m['time']} (IL)\n"
    if player_stats:
        plain += "\n---\n"
        for ps in player_stats:
            if ps.get("dnp"):
                plain += f"\nð {ps['player_name']} Did Not Play ({ps['game_date_il']})\n"
            else:
                result = "× ××¦×××" if ps["won"] else "××¤×¡×"
                pm_str = ps.get("plus_minus", "?")
                try:
                    pm_str = f"+{pm_str}" if int(pm_str) > 0 else str(pm_str)
                except (ValueError, TypeError):
                    pass
                plain += (f"\nð {ps['player_name']} | {ps['away']} {ps['away_score']}â{ps['home_score']} {ps['home']}"
                          f" ({result}, {ps['game_date_il']})\n"
                          f"   {ps['min']} min Â· {ps['pts']} pts Â· {ps['reb']} reb Â· {ps['ast']} ast Â· {pm_str}\n"
                          f"   FG {ps['fg'].replace('-','/')} Â· 3PT {ps['three_pt'].replace('-','/')} Â· FT {ps['ft'].replace('-','/')}"
                          f" Â· {ps['stl']} stl Â· {ps['blk']} blk Â· {ps['to']} to Â· {ps['pf']} pf\n")
    plain += f"\nEdit your teams: https://sports-reminder-ui.vercel.app"

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(build_email_html(matches, today, player_stats), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to, msg.as_string())
        print(f"â  Email sent to {to}")
        return True
    except Exception as e:
        print(f"â  Email failed: {e}")
        return False

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# WEEKLY DIGEST â helper, HTML builder, sender
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def _week_label(start_date: str) -> str:
    """Returns e.g. 'Apr 12â18' or 'Apr 28 â May 4'."""
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end   = start + datetime.timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}â{end.day}"
    return f"{start.strftime('%b')} {start.day} â {end.strftime('%b')} {end.day}"


def build_weekly_email_html(matches_by_day: dict, start_date: str) -> str:
    week_lbl    = _week_label(start_date)
    sport_emoji = {"soccer": "â½", "basketball": "ð"}

    if not matches_by_day:
        body_html = """
        <div style="padding:32px 24px; text-align:center; color:#6b7280; font-size:14px;">
          No matches this week for your teams. Enjoy the break! â½ð
        </div>"""
    else:
        days_html = ""
        for date_str, matches in matches_by_day.items():
            dt        = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            day_label = dt.strftime("%A, %b ") + str(dt.day)
            rows      = ""
            for m in matches:
                emoji    = sport_emoji.get(m["sport"], "ðï¸")
                gcal     = _gcal_url(m, date_str)
                gcal_html = (
                    f'<div style="margin-top:4px;">'
                    f'<a href="{gcal}" style="font-size:11px; color:#1a56db; text-decoration:none;">ð Add to Calendar</a>'
                    f'</div>'
                ) if gcal and m["time"] != "TBD" else ""
                # Playoff series info line (NBA)
                playoff_html = ""
                p_note  = m.get("playoff_note", "")
                p_series = m.get("series_summary", "")
                if p_note or p_series:
                    parts = []
                    if p_note:
                        parts.append(p_note)
                    if p_series:
                        parts.append(p_series)
                    playoff_html = f'<div style="font-size:11px; color:#9333ea; margin-top:2px; font-style:italic;">{" \u00b7 ".join(parts)}</div>'
                # Time display — TBD gets a muted style
                if m["time"] == "TBD":
                    time_html = '<span style="font-weight:600; color:#9ca3af;">TBD</span>'
                else:
                    time_html = f'<span style="font-weight:600; color:#1a56db;">{m["time"]}</span>'
                rows += f"""
                <tr>
                  <td style="padding:10px 12px; font-size:15px; border-bottom:1px solid #f0f0f0; width:32px;">{emoji}</td>
                  <td style="padding:10px 12px; border-bottom:1px solid #f0f0f0;">
                    <div style="font-weight:600; color:#111;">{m['away']} @ {m['home']}</div>
                    <div style="font-size:12px; color:#666; margin-top:2px;">{m['league_name']}</div>
                    {playoff_html}
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
          <div style="font-size:40px; margin-bottom:4px; line-height:1;">ðï¸</div>
          <h1 style="color:white; margin:0; font-size:18px; font-weight:700;">Upcoming Matches</h1>
          <p style="color:#94a3b8; margin:4px 0 0; font-size:13px;">{week_lbl} Â· Israel time</p>
        </div>
        {body_html}
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb;">
          <a href="https://sports-reminder-ui.vercel.app"
             style="font-size:12px; color:#6b7280; text-decoration:none;">
            âï¸ Edit your teams at sports-reminder-ui.vercel.app
          </a>
        </div>
      </div>
    </body></html>
    """


def send_weekly_email(to: str, matches_by_day: dict, start_date: str):
    if not GMAIL_APP_PASSWORD:
        print("â  GMAIL_APP_PASSWORD not set.")
        return False

    week_lbl = _week_label(start_date)
    total    = sum(len(v) for v in matches_by_day.values())
    subject  = f"ðï¸ No upcoming matches â {week_lbl}" if total == 0 \
               else f"ðï¸ Upcoming matches â {week_lbl}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = to

    if total == 0:
        plain = f"No matches this week for your teams. Enjoy the break! â½ð\n\nEdit your teams: https://sports-reminder-ui.vercel.app"
    else:
        plain = f"Upcoming matches â {week_lbl} (Israel time)\n\n"
        for date_str, matches in matches_by_day.items():
            dt     = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            plain += f"{dt.strftime('%A, %b')} {dt.day}\n"
            for m in matches:
                icon = "ð" if m["sport"] == "basketball" else "â½"
                plain += f"  {icon}  {m['away']} @ {m['home']}  â  {m['league_name']}  â  {m['time']}\n"
                p_note  = m.get("playoff_note", "")
                p_series = m.get("series_summary", "")
                if p_note or p_series:
                    parts = [p for p in [p_note, p_series] if p]
                    plain += f"      {' · '.join(parts)}\n"
            plain += "\n"
        plain += f"Edit your teams: https://sports-reminder-ui.vercel.app"

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(build_weekly_email_html(matches_by_day, start_date), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to, msg.as_string())
        print(f"â  Weekly email sent to {to}")
        return True
    except Exception as e:
        print(f"â  Weekly email failed: {e}")
        return False


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# MAIN
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

def main():
    args           = sys.argv[1:]
    send_mode      = "--send"        in args
    test_mode      = "--test"        in args
    mock_mode      = "--mock"        in args
    stats_only     = "--stats-only"  in args   # 07:00 IL â post-game stats only
    no_stats       = "--no-stats"    in args   # 09:00 IL â gorning games only
    weekly_mode    = "--weekly"      in args   # Saturday 22:00 IL â weekly digest
    today          = today_israel()

    print(f"\nðï¸  Sports Reminder â {today}")
    print("=" * 50)

    if mock_mode:
        print("\nð§ª MOCK MODE â using fake teams & games (no network calls)\n")
        tracked = MOCK_TEAMS
        matches = MOCK_MATCHES
        print(f"   Tracked teams ({len(tracked)}):")
        for t in tracked:
            print(f"   â¢ {t['name']}  [{t['league']} / {t['sport']}]")
        print(f"\nð¯ {len(matches)} mock match(es) today:\n")
        for m in matches:
            emoji = "â½" if m["sport"] == "soccer" else "ð"
            print(f"  {emoji}  {m['away']} @ {m['home']}")
            print(f"      {m['league_name']}  â  {m['time']} (Israel time)\n")
        if send_mode:
            print(f"ð§ Sending mock email to {GMAIL_SENDER}...")
            send_email(GMAIL_SENDER, matches, today)
        else:
            # Show the HTML that would be sent
            html = build_email_html(matches, today)
            out_path = "/tmp/sports_reminder_preview.html"
            with open(out_path, "w") as f:
                f.write(html)
            print(f"ð Email HTML preview saved to: {out_path}")
            print("   Open it in a browser to see how the email looks.")
            print("\n   Run with --mock --send to actually send it.")
        return

    # ââ Weekly digest mode (Saturday night, 22:00 IL) âââââââââââââââââââââââ
    if weekly_mode:
        weekly_enabled = load_weekly_digest_flag(FIRESTORE_DOC)
        if not weekly_enabled and not test_mode:
            print("\nð Weekly digest disabled in user settings â skipping.")
            return
        print(f"\nð Weekly digest mode â fetching 7 days from {today}...")
        tracked = load_tracked_teams(FIRESTORE_DOC)
        if not tracked:
            print("   No tracked teams found.")
            return
        print(f"   Found {len(tracked)} tracked team(s).")
        matches_by_day = find_week_matches(tracked, today)
        total = sum(len(v) for v in matches_by_day.values())
        print(f"\nðï¸  {total} match(es) found across {len(matches_by_day)} day(s):")
        for date_str, day_matches in matches_by_day.items():
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            print(f"\n  {dt.strftime('%A, %b')} {dt.day}:")
            for m in day_matches:
                icon = "ð" if m["sport"] == "basketball" else "â½"
                print(f"    {icon}  {m['away']} @ {m['home']}  â  {m['league_name']}  â  {m['time']}")
        if send_mode:
            print(f"\nð§ Sending weekly email to {GMAIL_SENDER}...")
            send_weekly_email(GMAIL_SENDER, matches_by_day, today)
        else:
            print("\nâ¹ï¸  Dry-run. Add --send to send the weekly digest.")
        return

    # ââ Stats-only mode (post-game email, 07:00 IL) âââââââââââââââââââââââââ
    if stats_only:
        avdija_enabled = load_avdija_stats_flag(FIRESTORE_DOC)
        if not avdija_enabled:
            print("\nð Avdija stats disabled in user settings â skipping stats email.")
            return
        print("\nð Stats-only mode â fetching last game stats...")
        player_stats = []
        for p in PLAYER_WATCH:
            ps = fetch_player_last_game_stats(p)
            if ps:
                label = "DNP" if ps.get("dnp") else f"{ps['pts']} pts / {ps['reb']} reb / {ps['ast']} ast"
                print(f"   ð {p['display_name']}: {label} ({ps['game_date_il']})")
                player_stats.append(ps)
            else:
                print(f"   â ï¸  {p['display_name']}: no recent game found")
        if send_mode:
            if player_stats:
                print(f"\nð§ Sending stats email to {GMAIL_SENDER}...")
                send_email(GMAIL_SENDER, [], today, player_stats)
            else:
                print("\nð­ No player stats found â no email sent.")
        else:
            print("â¹ï¸  Dry-run (stats-only). Add --send to send.")
        return

    # 1. Load tracked teams from Firestore
    print(f"\nð¥ Loading teams from Firestore (doc: {FIRESTORE_DOC})...")
    tracked = load_tracked_teams(FIRESTORE_DOC)
    if not tracked:
        print("   No tracked teams found.")
        return

    print(f"   Found {len(tracked)} tracked team(s):")
    for t in tracked:
        print(f"   â¢ {t['name']}  [{t['league']} / {t['sport']}]")

    # 2. Check today's matches
    print(f"\nð Checking ESPN for today's games...")
    matches = find_my_matches(tracked, today)

    # 3. Fetch player stats (skipped when --no-stats or flag disabled in Firestore)
    player_stats = []
    if no_stats:
        print(f"\nð Skipping player stats (--no-stats mode).")
        watch_list = []
    else:
        avdija_enabled = load_avdija_stats_flag(FIRESTORE_DOC)
        if avdija_enabled:
            print(f"\nð Fetching player stats...")
            watch_list = PLAVE_WATCH
        else:
            print(f"\nð Avdija stats disabled in user settings â skipping.")
            watch_list = []
    for p in watch_list:
        ps = fetch_player_last_game_stats(p)
        if ps:
            label = "×× ×©×××§" if ps.get("dnp") else f"{ps['pts']} pts / {ps['reb']} reb / {ps['ast']} ast"
            print(f"   ð {ps['player_name']}: {label} ({ps['game_date_il']})")
            player_stats.append(ps)
        else:
            print(f"   â ï¸  {p['display_name']}: ×× × ××¦× ××©×¹××§ ×××¨××")

    # 4. Show results
    if not matches:
        print(f"\nð´ No matches today for your teams.")
    else:
        print(f"\nð¯ {len(matches)} match(es) today:\n")
        for m in matches:
            emoji = "â½" if m["sport"] == "soccer" else "ð"
            print(f"  {emoji}  {m['away']} @ {m['home']}")
            print(f"      {m['league_name']}  â  {m['time']} (Israel time)")
            print()

    # 5. Send email?
    if test_mode:
        # Send a test email with dummy data if no real matches
        if not matches:
            matches = [{
                "home": "Real Madrid", "away": "FC Barcelona",
                "time": "21:00", "status": "Scheduled",
                "tracked_team": "FC Barcelona", "league_name": "La Liga", "sport": "soccer"
            }]
        print(f"\nð§ Test mode â sending email to {GMAIL_SENDER}...")
        send_email(GMAIL_SENDER, matches, today, player_stats)

    elif send_mode:
        if matches or player_stats:
            print(f"\nð§ Sending email to {GMAIL_SENDER}...")
            send_email(GMAIL_SENDER, matches, today, player_stats)
        else:
            print("\nð­ No matches and no player stats â no email sent.")

    else:
        print("â¹ï¸  Dry-run mode. Run with --send to send email, --test to test email delivery.")

if __name__ == "__main__":
    main()
