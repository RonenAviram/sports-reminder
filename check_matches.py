#!/usr/bin/env python3
"""
SportsReminder - check_matches.py
Checks if any configured teams have a match today.
Uses ESPN's free public API (no key required).
Outputs JSON list of matches, or "NO_MATCHES".
"""

import json
import urllib.request
import urllib.error
import datetime
import sys
import os

# ─── League IDs on ESPN ───────────────────────────────────────────────────────
FOOTBALL_LEAGUES = {
    "La Liga":        "esp.1",
    "Champions League": "uefa.champions",
    "Premier League": "eng.1",
    "Serie A":        "ita.1",
    "Bundesliga":     "ger.1",
    "Ligue 1":        "fra.1",
    "Europa League":  "uefa.europa",
}

BASKETBALL_LEAGUES = {
    "NBA":        "nba",
    "Euroleague": "euroleague",
}

ESPN_FOOTBALL_BASE  = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"
ESPN_BASKETBALL_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/scoreboard?dates={date}"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def format_time(iso_string, tz_offset):
    """Convert ESPN UTC ISO time to local time string."""
    try:
        dt = datetime.datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        local = dt + datetime.timedelta(hours=tz_offset)
        return local.strftime("%H:%M")
    except Exception:
        return "TBD"


def check_football(team_name, competitions, today_str, tz_offset):
    matches = []
    for comp_name in competitions:
        league_id = FOOTBALL_LEAGUES.get(comp_name)
        if not league_id:
            print(f"  [skip] Unknown football competition: {comp_name}", file=sys.stderr)
            continue

        url = ESPN_FOOTBALL_BASE.format(league=league_id, date=today_str)
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"  [error] {comp_name}: {e}", file=sys.stderr)
            continue

        for event in data.get("events", []):
            comps = event.get("competitions", [{}])
            competitors = comps[0].get("competitors", []) if comps else []
            names = [c.get("team", {}).get("displayName", "") for c in competitors]

            if any(team_name.lower() in n.lower() for n in names):
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1] if competitors else {})
                matches.append({
                    "sport":       "football",
                    "competition": comp_name,
                    "home_team":   home.get("team", {}).get("displayName", "?"),
                    "away_team":   away.get("team", {}).get("displayName", "?"),
                    "time":        format_time(event.get("date", ""), tz_offset),
                    "venue":       comps[0].get("venue", {}).get("fullName", "") if comps else "",
                })
    return matches


def check_basketball(team_name, competitions, today_str, tz_offset):
    matches = []
    for comp_name in competitions:
        league_id = BASKETBALL_LEAGUES.get(comp_name)
        if not league_id:
            print(f"  [skip] Unknown basketball competition: {comp_name}", file=sys.stderr)
            continue

        url = ESPN_BASKETBALL_BASE.format(league=league_id, date=today_str)
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"  [error] {comp_name}: {e}", file=sys.stderr)
            continue

        for event in data.get("events", []):
            comps = event.get("competitions", [{}])
            competitors = comps[0].get("competitors", []) if comps else []
            names = [c.get("team", {}).get("displayName", "") for c in competitors]

            if any(team_name.lower() in n.lower() for n in names):
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1] if competitors else {})
                matches.append({
                    "sport":       "basketball",
                    "competition": comp_name,
                    "home_team":   home.get("team", {}).get("displayName", "?"),
                    "away_team":   away.get("team", {}).get("displayName", "?"),
                    "time":        format_time(event.get("date", ""), tz_offset),
                    "venue":       comps[0].get("venue", {}).get("fullName", "") if comps else "",
                })
    return matches


def main():
    # Load config from same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")

    if not os.path.exists(config_path):
        print(f"ERROR: config.json not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    tz_offset  = config.get("timezone_offset_hours", 3)
    today      = datetime.date.today()
    today_str  = today.strftime("%Y%m%d")   # ESPN format

    all_matches = []

    for team in config.get("teams", []):
        name         = team["name"]
        sport        = team.get("sport", "football").lower()
        competitions = team.get("competitions", [])

        print(f"Checking {name} ({sport})...", file=sys.stderr)

        if sport == "football":
            all_matches.extend(check_football(name, competitions, today_str, tz_offset))
        elif sport == "basketball":
            all_matches.extend(check_basketball(name, competitions, today_str, tz_offset))
        else:
            print(f"  [skip] Unknown sport: {sport}", file=sys.stderr)

    if all_matches:
        print(json.dumps(all_matches, ensure_ascii=False))
    else:
        print("NO_MATCHES")


if __name__ == "__main__":
    main()
