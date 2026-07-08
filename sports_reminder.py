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
import logging

logger = logging.getLogger(__name__)

from email_sender import send_raw_email
from player_stats import send_player_stats_emails

from config import *
from tz_utils import *
from matching import *
from firestore_helpers import *


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
                logger.warning("  ESPN fetch failed for %s: %s", league_id, e)
        data = {"events": all_events}
    else:
        try:
            data = fetch_json(f"{url}?dates={today.replace('-', '')}")
        except Exception as e:
            logger.warning("  ESPN fetch failed for %s: %s", league_id, e)
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
        if league_id in ("nba", "mls", "fifa_world_cup") and game_utc_dt is not None and not weekly_mode:
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
        if league_id in ("fifa_world_cup", "champions_league", "europa_league"):
            for note in comp.get("notes", []):
                headline = note.get("headline", "")
                if headline:
                    tournament_note = headline  # e.g. "Group A", "Round of 16"

        # Season slug for knockout detection (ESPN tournaments)
        season_slug = event.get("season", {}).get("slug", "")

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
            "season_slug": season_slug,
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
        logger.warning("  EuroLeague API fetch failed for %s: %s", league_id, e)
        return []

    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        logger.warning("  EuroLeague XML parse error for %s: %s", league_id, e)
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
        el_round = (game.findtext("round") or "").strip()
        el_group = (game.findtext("group") or "").strip()
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
            "el_round": el_round,
            "el_group": el_group,
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
        logger.warning("  TheSportsDB fetch failed for %s: %s", league_id, e)
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
    Returns {"disabled": [...], "reenabled": int, "total": int, "error": str|None}
    """
    # --- 1. Fetch raw Firestore doc via Admin SDK ---
    try:
        doc_ref = _get_db().collection("configs").document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            return {"disabled": [], "reenabled": 0, "total": 0, "error": "Doc not found"}
        doc_data = doc.to_dict()
    except Exception as e:
        return {"disabled": [], "reenabled": 0, "total": 0, "error": str(e)}

    teams_list = doc_data.get("teams", [])
    if not isinstance(teams_list, list):
        return {"disabled": [], "reenabled": 0, "total": 0, "error": "No teams array"}

    # --- 2. Re-enable ALL disabled teams so we get a fresh slate ---
    reenabled = 0
    for t in teams_list:
        if isinstance(t, dict) and t.get("enabled") is False:
            t["enabled"] = True
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
    for t in teams_list:
        if not isinstance(t, dict):
            continue
        name = t.get("name", "")
        league_id = t.get("leagueId", "")
        if (name, league_id) in failing:
            t["enabled"] = False
            disabled_names.append(name)

    # --- 5. Write back via Admin SDK ---
    try:
        doc_ref.set({"teams": teams_list}, merge=True)
    except Exception as e:
        return {"disabled": disabled_names, "reenabled": reenabled,
                "total": len(teams_list), "error": f"Write failed: {e}"}

    return {"disabled": disabled_names, "reenabled": reenabled,
            "total": len(teams_list), "error": None}
def fetch_league_games(leagues: set, today: str) -> dict:
    """Fetch today's games for a set of league IDs. Returns {league_id: [games]}.
    Called ONCE for all users — the expensive ESPN/API step."""
    games_by_league: dict[str, list] = {}
    for league_id in leagues:
        if league_id in ESPN_ENDPOINTS or league_id in EUROLEAGUE_COMPETITION_CODES:
            games_by_league[league_id] = fetch_todays_games(league_id, today)
    return games_by_league


# -----------------------------------------------------------------------------
# KNOCKOUT DETECTION - identify playoff/knockout games per tournament
# -----------------------------------------------------------------------------
KNOCKOUT_TOURNAMENTS = {
    "fifa_world_cup", "champions_league", "europa_league",
    "nba", "euroleague", "eurocup",
}

# ESPN season.slug values that are NOT knockout
_ESPN_NON_KNOCKOUT_SLUGS = {"group-stage", "league-phase"}

# EuroCup knockout round codes
_EUROCUP_KNOCKOUT_ROUNDS = {"8F", "4F", "2F", "Final"}

# Stage name display mapping for ESPN season.slug
_KNOCKOUT_STAGE_NAMES = {
    "knockout-round-playoffs": "Knockout playoffs",
    "round-of-32": "Round of 32",
    "round-of-16": "Round of 16",
    "quarterfinals": "Quarter-final",
    "semifinals": "Semi-final",
    "3rd-place-match": "3rd place",
    "final": "Final",
}

_KNOCKOUT_LEAGUE_NAMES = {
    "fifa_world_cup": "FIFA World Cup",
    "champions_league": "Champions League",
    "europa_league": "Europa League",
    "nba": "NBA",
    "euroleague": "EuroLeague",
    "eurocup": "EuroCup",
}

_KNOCKOUT_LEAGUE_SPORTS = {
    "fifa_world_cup": "soccer",
    "champions_league": "soccer",
    "europa_league": "soccer",
    "nba": "basketball",
    "euroleague": "basketball",
    "eurocup": "basketball",
}


def is_knockout_game(match: dict) -> bool:
    """Check if a match is a knockout/playoff game."""
    league_id = match.get("league_id", "")

    # ESPN-based tournaments (WC, UCL, Europa)
    if league_id in ("fifa_world_cup", "champions_league", "europa_league"):
        slug = match.get("season_slug", "")
        return slug != "" and slug not in _ESPN_NON_KNOCKOUT_SLUGS

    # NBA: playoff_note ends with "Finals" (NBA Finals + Conference Finals)
    if league_id == "nba":
        note = match.get("playoff_note", "")
        return note.endswith("Finals") or " Finals " in note

    # EuroLeague: round FF (Final Four)
    if league_id == "euroleague":
        return match.get("el_round", "") == "FF"

    # EuroCup: knockout rounds (8F, 4F, 2F, Final)
    if league_id == "eurocup":
        return match.get("el_round", "") in _EUROCUP_KNOCKOUT_ROUNDS

    return False


def get_knockout_stage_name(match: dict) -> str:
    """Get human-readable knockout stage name for email display."""
    league_id = match.get("league_id", "")

    # ESPN tournaments
    if league_id in ("fifa_world_cup", "champions_league", "europa_league"):
        slug = match.get("season_slug", "")
        return _KNOCKOUT_STAGE_NAMES.get(slug, slug.replace("-", " ").title())

    # NBA: extract from playoff_note
    if league_id == "nba":
        note = match.get("playoff_note", "")
        if "NBA Finals" in note:
            game_part = note.split(" - ")[-1] if " - " in note else ""
            return ("Final " + game_part).strip() if game_part else "Final"
        if "Finals" in note:
            conf = note.split(" Finals")[0]
            game_part = note.split(" - ")[-1] if " - " in note else ""
            label = conf + " Final"
            return (label + " " + game_part).strip() if game_part else label
        return note

    # EuroLeague: use group field for detail
    if league_id == "euroleague":
        group = match.get("el_group", "").strip().upper()
        if "SEMIFINAL" in group:
            return "Semi-final"
        if "CHAMPIONSHIP" in group:
            return "Final"
        return "Final Four"

    # EuroCup: use round field
    if league_id == "eurocup":
        el_round = match.get("el_round", "")
        mapping = {"8F": "Round of 16", "4F": "Quarter-final", "2F": "Semi-final", "Final": "Final"}
        return mapping.get(el_round, el_round)

    return ""


def filter_matches_for_user(tracked: list[dict], games_by_league: dict, today: str, knockout_follow: dict = None) -> list[dict]:
    """Filter pre-fetched games by a user's tracked teams + knockout tournaments."""
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

    # Knockout games from followed tournaments
    if knockout_follow:
        for ko_league, enabled in knockout_follow.items():
            if not enabled:
                continue
            ko_games = games_by_league.get(ko_league, [])
            for game in ko_games:
                if not is_knockout_game(game):
                    continue
                game_key = f"{game['home']}_{game['away']}_{ko_league}"
                if game_key in seen:
                    # Already included as tracked-team match - add knockout badge
                    for m in matches:
                        mk = f"{m['home']}_{m['away']}_{m.get('league_id', '')}"
                        if mk == game_key:
                            m["knockout_stage"] = get_knockout_stage_name(game)
                    continue
                # New knockout-only match
                league_display = _KNOCKOUT_LEAGUE_NAMES.get(ko_league, ko_league)
                sport = _KNOCKOUT_LEAGUE_SPORTS.get(ko_league, "soccer")
                matches.append({
                    **game,
                    "tracked_team": "",
                    "league_name": league_display,
                    "sport": sport,
                    "knockout_stage": get_knockout_stage_name(game),
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
        logger.info("  📅 Fetching %s...", date_str)
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
        logger.info("    → %s match(es)", len(matches))
        return matches

    # Fetch serially (one date at a time) with a pause between dates.
    # Parallelism caused ESPN rate-limiting → all leagues returning [] silently.
    all_matches: list[dict] = []
    for i, d in enumerate(espn_dates):
        try:
            all_matches.extend(fetch_for_espn_date(d))
        except Exception as e:
            logger.warning("  Week fetch failed for %s: %s", d, e)
        if i < len(espn_dates) - 1:
            _time.sleep(1.0)  # 1s between dates to avoid ESPN rate limiting

    # World Cup mode: fetch all WC games for each ESPN date and merge
    if world_cup_mode:
        tracked_names = {t["name"] for t in tracked}
        logger.info("  🏆 World Cup mode — fetching all WC games for the week...")
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
                logger.warning("  WC week fetch failed for %s: %s", d, e)
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

    logger.info("  🏆 Fetching %s days of World Cup games...", len(espn_dates))
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
            logger.info("    📅 %s: %s game(s)", date_str, len(games))
        except Exception as e:
            logger.warning("    %s: %s", date_str, e)
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

    logger.info("  ✅ Total: %s games across %s days", len(all_matches), len(results))
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
        <div style="margin:12px 16px 0;background:#25D366;border-radius:8px;padding:10px 16px;text-align:center;">
          <a href="https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT" target="_blank" style="color:white;text-decoration:none;font-size:13px;font-weight:600;">📱 Get updates on WhatsApp</a>
        </div>
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb; text-align:center;">
          <a href="https://app.sportsreminder.pro?utm_source=email&utm_medium=tournament"
             style="font-size:12px; color:#3b82f6; text-decoration:underline;">
            ✏️ Edit your teams here
          </a>
          <div style="margin-top:8px;font-size:12px;color:#999;">
            <a href="https://app.sportsreminder.pro?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
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
        # Tournament round info (World Cup, UCL, Europa)
        tournament_html = ""
        t_note = m.get("tournament_note", "")
        # Knockout stage badge
        knockout_html = ""
        ko_stage = m.get("knockout_stage", "")
        if ko_stage:
            knockout_html = f'<div style="font-size:11px; color:#6b7280; margin-top:2px;">\U0001F3C6 {ko_stage}</div>'
        if t_note:
            tournament_html = f'<div style="font-size:11px; color:#b45309; margin-top:2px; font-style:italic;">{t_note}</div>'
        # Time display — TBD gets a muted style; "If Necessary" gets extra note
        is_if_necessary = "if necessary" in p_note.lower()
        # Show date label next to time when game falls on a different day
        game_il_date = m.get("il_date", today)
        game_display_date = m.get("display_date", game_il_date)
        if m["time"] == "00:00" and game_display_date != game_il_date:
            _dd_dt = datetime.datetime.strptime(game_display_date, "%Y-%m-%d")
            _il_dt = datetime.datetime.strptime(game_il_date, "%Y-%m-%d")
            date_prefix = f'<div style="font-size:11px; color:#6b7280; margin-bottom:1px;">{_dd_dt.strftime("%a")}-{_il_dt.strftime("%a")} night</div>'
        elif game_display_date != today and m["time"] != "TBD":
            _label_date = game_il_date if game_il_date != game_display_date else game_display_date
            _g_dt = datetime.datetime.strptime(_label_date, "%Y-%m-%d")
            _g_day_name = _g_dt.strftime("%a")
            _g_date_str = f"{_g_day_name} {_g_dt.day}/{_g_dt.month}"
            date_prefix = f'<div style="font-size:11px; color:#6b7280; margin-bottom:1px;">{_g_date_str}</div>'
        elif game_il_date != game_display_date and m["time"] != "TBD":
            # Game is tonight but crosses midnight (e.g. Panama 02:00 = early morning of il_date)
            _g_dt = datetime.datetime.strptime(game_il_date, "%Y-%m-%d")
            _g_day_name = _g_dt.strftime("%a")
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
            {knockout_html}
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
        <div style="margin:12px 16px 0;background:#25D366;border-radius:8px;padding:10px 16px;text-align:center;">
          <a href="https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT" target="_blank" style="color:white;text-decoration:none;font-size:13px;font-weight:600;">📱 Get updates on WhatsApp</a>
        </div>
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb; text-align:center;">
          <a href="https://app.sportsreminder.pro?utm_source=email&utm_medium=daily"
             style="font-size:12px; color:#3b82f6; text-decoration:underline;">
            ✏️ Edit your teams here
          </a>
          <div style="margin-top:8px;font-size:12px;color:#999;">
            <a href="https://app.sportsreminder.pro?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
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
    plain += f"\nEdit your teams: https://app.sportsreminder.pro?utm_source=email&utm_medium=daily"

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
        <div style="margin:12px 16px 0;background:#25D366;border-radius:8px;padding:10px 16px;text-align:center;">
          <a href="https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT" target="_blank" style="color:white;text-decoration:none;font-size:13px;font-weight:600;">📱 Get updates on WhatsApp</a>
        </div>
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb; text-align:center;">
          <a href="https://app.sportsreminder.pro?utm_source=email&utm_medium=weekly"
             style="font-size:12px; color:#3b82f6; text-decoration:underline;">
            ✏️ Edit your teams here
          </a>
          <div style="margin-top:8px;font-size:12px;color:#999;">
            <a href="https://app.sportsreminder.pro?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
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
        plain = f"No matches this week for your teams. Enjoy the break! ⚽🏀\n\nEdit your teams: https://app.sportsreminder.pro?utm_source=email&utm_medium=weekly"
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
        plain += f"Edit your teams: https://app.sportsreminder.pro?utm_source=email&utm_medium=weekly"

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

    logger.info("\n🗓️  Sports Reminder — %s", today)
    logger.info("%s", "=" * 50)
    if test_user_email:
        logger.info("\U0001f9ea TEST USER MODE: only sending to %s", test_user_email)

    if mock_mode:
        logger.info("\n🧪 MOCK MODE — using fake teams & games (no network calls)\n")
        tracked = MOCK_TEAMS
        matches = MOCK_MATCHES
        logger.info("   Tracked teams (%s):", len(tracked))
        for t in tracked:
            logger.info("   • %s  [%s / %s]", t['name'], t['league'], t['sport'])
        logger.info("\n🎯 %s mock match(es) today:\n", len(matches))
        for m in matches:
            emoji = "⚽" if m["sport"] == "soccer" else "🏀"
            logger.info("  %s  %s @ %s", emoji, m['away'], m['home'])
            logger.info("      %s  —  %s (Israel time)\n", m['league_name'], m['time'])
        if send_mode:
            logger.info("📧 Sending mock email to ronen6213@gmail.com...")
            send_email("ronen6213@gmail.com", matches, today)
        else:
            # Show the HTML that would be sent
            html = build_email_html(matches, today)
            out_path = "/tmp/sports_reminder_preview.html"
            with open(out_path, "w") as f:
                f.write(html)
            logger.info("📄 Email HTML preview saved to: %s", out_path)
            logger.info("   Open it in a browser to see how the email looks.")
            logger.info("\n   Run with --mock --send to actually send it.")
        return

    # ── Full tournament mode (one-off WC schedule) ──────────────────────────
    if tournament_mode:
        logger.info("\n🏆 Full Tournament mode — fetching all FIFA World Cup 2026 games...")
        all_u = load_all_users()
        if test_user_email:
            all_u = [u for u in all_u if u.get("email","").lower() == test_user_email.lower()]
        tracked = all_u[0]["teams"] if all_u else []
        tracked_names = {t["name"] for t in tracked} if tracked else set()
        logger.info("   %s tracked team(s) found", len(tracked_names))
        matches_by_day = fetch_full_tournament_games(tracked_names)
        total = sum(len(v) for v in matches_by_day.values())
        logger.info("\n🏆 %s match(es) found across %s day(s)", total, len(matches_by_day))
        for date_str, day_matches in matches_by_day.items():
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            logger.info("\n  %s %s: %s game(s)", dt.strftime('%A, %b'), dt.day, len(day_matches))
            for m in day_matches:
                logger.info("    ⚽  %s Vs %s  —  %s", m['home'], m['away'], m['time'])
        if send_mode:
            logger.info("\n📧 Sending tournament email to ronen6213@gmail.com...")
            send_tournament_email("ronen6213@gmail.com", matches_by_day)
        else:
            logger.info("\nℹ️  Dry-run. Add --send to send the tournament email.")
        return

    # ── Load all users ──────────────────────────────────────────────────────
    logger.info("\n📥 Loading users...")
    users = load_all_users()
    if test_user_email:
        users = [u for u in users if u.get("email","").lower() == test_user_email.lower()]
        logger.info("   \U0001f9ea TEST MODE: filtered to %s user(s) matching %s", len(users), test_user_email)
    if not users:
        logger.info("   No active users found. Exiting.")
        return
    logger.info("   Found %s active user(s): %s", len(users), ', '.join(u['display_name'] for u in users))

    # ── Load global config ────────────────────────────────────────────────
    global_config = load_global_config()
    wc_mode = global_config.get("world_cup_mode", False)
    if wc_mode:
        logger.info("   🏆 World Cup mode ON (global)")

    # ── Weekly digest mode (Saturday night, 20:00 IL) ─────────────────────
    if weekly_mode:
        logger.info("\n📅 Weekly digest mode — %s", today)
        for user in users:
            if user.get("emails_paused") and not test_mode:
                logger.info("\n  \u23f8\ufe0f %s: emails paused \u2014 skipping", user['display_name'])
                continue
            if user.get("synthetic") and not test_user_email:
                continue
            if not user.get("weekly_digest") and not test_mode:
                logger.info("\n   ⏭️  %s: weekly digest disabled → skipping", user['display_name'])
                continue
            try:
                tracked = user["teams"]
                if not tracked:
                    logger.info("\n   ⏭️  %s: no tracked teams → skipping", user['display_name'])
                    continue
                logger.info("\n   👤 %s (%s teams)...", user['display_name'], len(tracked))
                matches_by_day = find_week_matches(tracked, today, world_cup_mode=wc_mode, now_il_time=None if sim_date else now_israel_time())
                total = sum(len(v) for v in matches_by_day.values())
                logger.info("      🗓️  %s match(es) across %s day(s)", total, len(matches_by_day))
                if send_mode:
                    ok = send_weekly_email(user["email"], matches_by_day, today)
                else:
                    logger.info("      ℹ️  Dry-run (add --send)")
            except Exception as e:
                logger.error("   %s: weekly email failed — %s", user['display_name'], e)
        return

    # ── Multi-player stats mode (post-game email, 07:00 IL) ──────────────
    if player_stats_m:
        logger.info("\n📊 Multi-player stats mode")
        for user in users:
            if user.get("emails_paused") and not test_mode:
                logger.info("\n  \u23f8\ufe0f %s: emails paused \u2014 skipping", user['display_name'])
                continue
            if user.get("synthetic") and not test_user_email:
                continue
            try:
                logger.info("\n   👤 %s...", user['display_name'])
                send_player_stats_emails(
                    doc_id=user["doc_id"],
                    to_email=user["email"],
                    target_date=today,
                    send=send_mode,
                )
            except Exception as e:
                logger.error("   %s: stats email failed — %s", user['display_name'], e)
        return

    # ── Daily morning email (09:00 IL) ────────────────────────────────────

    # 1. Collect all unique leagues from all users (tracked teams + knockout follows)
    all_leagues = set()
    for user in users:
        for t in user["teams"]:
            all_leagues.add(t["leagueId"])
        # Add knockout-followed leagues so their games are fetched too
        for ko_league, enabled in user.get("knockout_follow", {}).items():
            if enabled:
                all_leagues.add(ko_league)
    logger.info("\n🔍 Fetching games for %s league(s)...", len(all_leagues))

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
        logger.info("   🏆 %s WC game(s) today", len(wc_games))

    # 3. Per-user: filter matches → send email
    for user in users:
        if user.get("emails_paused") and not test_mode:
            logger.info("\n  \u23f8\ufe0f %s: emails paused \u2014 skipping", user['display_name'])
            continue
        if user.get("synthetic") and not test_user_email:
            continue
        try:
            tracked = user["teams"]
            if not tracked and not wc_mode:
                logger.info("\n   ⏭️  %s: no tracked teams → skipping", user['display_name'])
                continue

            logger.info("\n   👤 %s (%s teams)", user['display_name'], len(tracked))

            # Filter pre-fetched games by this user's teams
            knockout_follow = user.get("knockout_follow", {})
            matches = filter_matches_for_user(tracked, games_by_league, today, knockout_follow=knockout_follow)

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
            logger.info("      🎯 %s match(es) (%s WC + %s other)", len(matches), wc_count, other_count)

            if test_mode:
                if not matches:
                    matches = [{"home": "Real Madrid", "away": "FC Barcelona",
                        "time": "21:00", "status": "Scheduled",
                        "tracked_team": "FC Barcelona", "league_name": "La Liga", "sport": "soccer"}]
                logger.info("      📧 Test email → %s", user['email'])
                send_email(user["email"], matches, today)

            elif send_mode:
                if matches:
                    logger.info("      📧 Sending → %s", user['email'])
                    ok = send_email(user["email"], matches, today)
                else:
                    logger.info("      📭 No matches → no email")

            else:
                logger.info("      ℹ️  Dry-run (add --send)")

        except Exception as e:
            logger.error("   %s: daily email failed — %s", user['display_name'], e)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
