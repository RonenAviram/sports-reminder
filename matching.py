"""Name matching and display utilities for SportsReminder.

Team name matching (aliases, normalization, noise stripping),
country flag emoji generation, and series summary formatting.
"""

import datetime
import re
import unicodedata

from config import NOISE_TOKENS, TEAM_ALIASES, _ESPN_ABBR_TO_ISO2, _SPECIAL_FLAGS

__all__ = [
    "_country_flag_emoji",
    "_team_display_with_flag",
    "strip_accents",
    "normalize_name",
    "strip_noise",
    "names_match",
    "_format_series_summary",
]

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

