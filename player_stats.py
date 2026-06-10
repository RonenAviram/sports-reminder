#!/usr/bin/env python3
"""
player_stats.py — Multi-player NBA stats email system.

Fetches last-game stats for tracked players from ESPN,
partitions them into up to 3 email buckets (dedicated Avdija,
Israeli players, general), and sends personalized email digests.

Called from sports_reminder.py via:
    from player_stats import send_player_stats_emails
"""

import json
import time
import datetime
import urllib.request
import urllib.parse

from email_sender import send_raw_email
from email.header import Header

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

FIREBASE_PROJECT = "sports-reminder-55578"
FIREBASE_API_KEY = "AIzaSyCd3C1_XN69r8lWUBYPndoGFxmDjnsjX1E"

# ESPN Player IDs for all tracked players
# Keys = ESPN ID (string), values used only for initial Firestore population
DEFAULT_PLAYERS = {
    # ── MVP Tier (auto-enabled) ──────────────────────────────────
    "4278073": {"name": "Shai Gilgeous-Alexander", "tags": [], "tier": "mvp"},
    "3112335": {"name": "Nikola Jokic",            "tags": [], "tier": "mvp"},
    "3945274": {"name": "Luka Doncic",             "tags": [], "tier": "mvp"},
    "3032977": {"name": "Giannis Antetokounmpo",   "tags": [], "tier": "mvp"},
    "1966":    {"name": "LeBron James",            "tags": [], "tier": "mvp"},
    "3975":    {"name": "Stephen Curry",           "tags": [], "tier": "mvp"},
    "3202":    {"name": "Kevin Durant",            "tags": [], "tier": "mvp"},
    "4594268": {"name": "Anthony Edwards",         "tags": [], "tier": "mvp"},
    "5104157": {"name": "Victor Wembanyama",       "tags": [], "tier": "mvp"},
    "4065648": {"name": "Jayson Tatum",            "tags": [], "tier": "mvp"},
    "3059318": {"name": "Joel Embiid",             "tags": [], "tier": "mvp"},
    "3908809": {"name": "Donovan Mitchell",        "tags": [], "tier": "mvp"},
    # ── All-Star Tier (disabled by default) ──────────────────────
    "3917376": {"name": "Jaylen Brown",            "tags": [], "tier": "allstar"},
    "4432158": {"name": "Evan Mobley",             "tags": [], "tier": "allstar"},
    "4396993": {"name": "Tyrese Haliburton",       "tags": [], "tier": "allstar"},
    "6430":    {"name": "Jimmy Butler",            "tags": [], "tier": "allstar"},
    "4066261": {"name": "Bam Adebayo",             "tags": [], "tier": "allstar"},
    "3934672": {"name": "Jalen Brunson",           "tags": [], "tier": "allstar"},
    "3136195": {"name": "Karl-Anthony Towns",      "tags": [], "tier": "allstar"},
    "4432573": {"name": "Paolo Banchero",          "tags": [], "tier": "allstar"},
    "4251":    {"name": "Paul George",             "tags": [], "tier": "allstar"},
    "6606":    {"name": "Damian Lillard",           "tags": [], "tier": "allstar"},
    "6442":    {"name": "Kyrie Irving",            "tags": [], "tier": "allstar"},
    "3936299": {"name": "Jamal Murray",            "tags": [], "tier": "allstar"},
    "4437244": {"name": "Jalen Green",             "tags": [], "tier": "allstar"},
    "6450":    {"name": "Kawhi Leonard",           "tags": [], "tier": "allstar"},
    "6583":    {"name": "Anthony Davis",           "tags": [], "tier": "allstar"},
    "4279888": {"name": "Ja Morant",               "tags": [], "tier": "allstar"},
    "3032976": {"name": "Rudy Gobert",             "tags": [], "tier": "allstar"},
    "4395628": {"name": "Zion Williamson",         "tags": [], "tier": "allstar"},
    "4433255": {"name": "Chet Holmgren",           "tags": [], "tier": "allstar"},
    "3136193": {"name": "Devin Booker",            "tags": [], "tier": "allstar"},
    "6580":    {"name": "Bradley Beal",            "tags": [], "tier": "allstar"},
    "4066259": {"name": "De'Aaron Fox",            "tags": [], "tier": "allstar"},
    "3155942": {"name": "Domantas Sabonis",        "tags": [], "tier": "allstar"},
    "4277905": {"name": "Trae Young",              "tags": [], "tier": "allstar"},
    # ── User-Selected Tier (disabled by default) ─────────────────
    "4432816": {"name": "LaMelo Ball",             "tags": [], "tier": "user"},
    "3064440": {"name": "Zach LaVine",             "tags": [], "tier": "user"},
    "4432166": {"name": "Cade Cunningham",         "tags": [], "tier": "user"},
    "3147657": {"name": "Mikal Bridges",           "tags": [], "tier": "user"},
    "4277956": {"name": "Jordan Poole",            "tags": [], "tier": "user"},
    "4871144": {"name": "Alperen Sengun",          "tags": [], "tier": "user"},
    "3992":    {"name": "James Harden",            "tags": [], "tier": "user"},
    "4066336": {"name": "Lauri Markkanen",         "tags": [], "tier": "user"},
    # ── Israeli Players (enabled by default) ─────────────────────
    "4683021": {"name": "Deni Avdija",    "tags": ["israeli"], "tier": "israeli"},
    "5242502": {"name": "Ben Saraf",      "tags": ["israeli"], "tier": "israeli"},
    "5107173": {"name": "Danny Wolf",     "tags": ["israeli"], "tier": "israeli"},
}

# Avdija ESPN ID — used for dedicated email routing
AVDIJA_ESPN_ID = "4683021"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS (imported from sports_reminder at runtime, but defined here for
#          standalone testing)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_json(url: str) -> dict:
    """Fetch JSON from a URL with ESPN-compatible headers."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
        "Referer": "https://www.espn.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _israel_utc_offset_h(at_utc: datetime.datetime) -> int:
    """Israel UTC offset: +3 (IDT summer) or +2 (IST winter)."""
    try:
        from zoneinfo import ZoneInfo
        aware = at_utc.replace(tzinfo=datetime.timezone.utc).astimezone(ZoneInfo("Asia/Jerusalem"))
        return int(aware.utcoffset().total_seconds() // 3600)
    except Exception:
        pass
    import calendar as _cal
    y = at_utc.year
    def _last_wd(yr, mo, wd):
        last = _cal.monthrange(yr, mo)[1]
        return max(d for d in range(last, last - 7, -1)
                   if datetime.date(yr, mo, d).weekday() == wd)
    dst_start = datetime.datetime(y, 3, _last_wd(y, 3, 4), 0, 0)
    dst_end   = datetime.datetime(y, 10, _last_wd(y, 10, 6), 1, 0)
    return 3 if dst_start <= at_utc < dst_end else 2


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING — Firestore
# ─────────────────────────────────────────────────────────────────────────────

def load_tracked_players(doc_id: str) -> dict:
    """
    Read tracked_players map from Firestore users/{doc_id}.
    Returns {espn_id_str: {name, enabled, tags, team}} for ENABLED players only.
    """
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/users/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = _fetch_json(url)
    except Exception as e:
        print(f"⚠️  Could not read Firestore: {e}")
        return {}

    fields = data.get("fields", {})
    tp_field = fields.get("tracked_players", {}).get("mapValue", {}).get("fields", {})

    players = {}
    for espn_id, val in tp_field.items():
        m = val.get("mapValue", {}).get("fields", {})
        # Check enabled flag
        enabled_field = m.get("enabled", {})
        if "booleanValue" in enabled_field:
            enabled = bool(enabled_field["booleanValue"])
        else:
            enabled = True
        if not enabled:
            continue

        # Read tags array
        tags_raw = m.get("tags", {}).get("arrayValue", {}).get("values", [])
        tags = [t.get("stringValue", "") for t in tags_raw]

        players[espn_id] = {
            "name":  m.get("name", {}).get("stringValue", f"Player {espn_id}"),
            "enabled": True,
            "tags":  tags,
            "team":  m.get("team", {}).get("stringValue", ""),
        }
    return players


def load_player_email_toggles(doc_id: str) -> dict:
    """
    Read the 3 email toggle booleans from Firestore.
    Returns {avdija_dedicated: bool, israeli: bool, general: bool}.
    """
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/users/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = _fetch_json(url)
    except Exception as e:
        print(f"⚠️  Could not read Firestore toggles: {e}")
        return {"avdija_dedicated": False, "israeli": False, "general": False}

    fields = data.get("fields", {})

    def _bool_field(name, default=False):
        f = fields.get(name, {})
        if "booleanValue" in f:
            return bool(f["booleanValue"])
        return default

    return {
        "avdija_dedicated": _bool_field("avdija_dedicated_email", False),
        "israeli":          _bool_field("israeli_players_email", False),
        "general":          _bool_field("player_stats_email", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ESPN FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def check_nba_games_yesterday(yesterday_il: str) -> bool:
    """
    Check ESPN NBA scoreboard for the given date (YYYY-MM-DD).
    Returns True if at least one game was played.
    """
    date_str = yesterday_il.replace("-", "")
    url = (f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
           f"/scoreboard?dates={date_str}")
    try:
        data = _fetch_json(url)
        events = data.get("events", [])
        return len(events) > 0
    except Exception as e:
        print(f"⚠️  Could not check NBA scoreboard: {e}")
        return True  # assume games exist on error, to avoid skipping


def fetch_player_stats(espn_id: str, yesterday_il: str,
                       target_date: str = "") -> dict | None:
    """
    Fetch last-game stats for a player from ESPN.
    Checks yesterday + today UTC dates to handle overnight Israel games.
    Accepts games on yesterday_il OR on target_date before 08:00 IL
    (overnight games that crossed midnight in Israel time).
    Returns stat dict or None if no game found on the target date.
    """
    # Derive ESPN dates from yesterday_il to support --simulate-date
    yesterday_date = datetime.datetime.strptime(yesterday_il, "%Y-%m-%d")
    dates_to_check = [
        yesterday_date.strftime("%Y%m%d"),
        (yesterday_date - datetime.timedelta(days=1)).strftime("%Y%m%d"),
    ]

    for date_str in dates_to_check:
        try:
            url = (f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
                   f"/scoreboard?dates={date_str}")
            data = _fetch_json(url)
        except Exception:
            continue

        for event in data.get("events", []):
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])

            # Only completed games
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue

            # Check game date in Israel time
            try:
                game_utc_dt = datetime.datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ")
                il_offset = _israel_utc_offset_h(game_utc_dt)
                game_il = game_utc_dt + datetime.timedelta(hours=il_offset)
                game_date_il_str = game_il.strftime("%Y-%m-%d")
                game_date_il_display = game_il.strftime("%d/%m")
            except Exception:
                continue

            # Skip if game date doesn't match:
            # - Accept games on yesterday (Israel time)
            # - Accept overnight games on target_date before 08:00 IL
            #   (latest NBA tip-off is 05:30 IL; 08:00 gives 2.5h margin)
            game_hour_il = game_il.hour
            if game_date_il_str == yesterday_il:
                pass  # normal match
            elif target_date and game_date_il_str == target_date and game_hour_il < 8:
                pass  # overnight game that crossed midnight IL
            else:
                continue

            # Fetch box score
            game_id = event["id"]
            try:
                summary = _fetch_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
                    f"/summary?event={game_id}"
                )
            except Exception:
                continue

            # Find the player in the boxscore
            for team_data in summary.get("boxscore", {}).get("players", []):
                team_info = team_data.get("team", {})
                team_name_from_espn = team_info.get("displayName", "")

                for cat in team_data.get("statistics", []):
                    athlete = next(
                        (a for a in cat.get("athletes", [])
                         if a.get("athlete", {}).get("id") == espn_id),
                        None
                    )
                    if not athlete:
                        continue

                    labels = cat.get("labels", [])
                    stats = athlete.get("stats", [])
                    stat_map = {labels[i]: stats[i]
                                for i in range(min(len(labels), len(stats)))}

                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

                    # Determine if this player's team won
                    player_team_id = team_info.get("id", "")
                    our_team = next(
                        (c for c in competitors if c.get("team", {}).get("id") == player_team_id),
                        None
                    )
                    won = our_team.get("winner", False) if our_team else False

                    # Check DNP
                    is_dnp = athlete.get("didNotPlay", False)
                    minutes_str = stat_map.get("MIN", "0")
                    try:
                        minutes_val = int(minutes_str)
                    except (ValueError, TypeError):
                        minutes_val = 0

                    # Get opponent
                    if player_team_id == home.get("team", {}).get("id"):
                        opponent = away["team"]["displayName"]
                    else:
                        opponent = home["team"]["displayName"]

                    return {
                        "espn_id":       espn_id,
                        "player_name":   athlete.get("athlete", {}).get("displayName", ""),
                        "team":          team_name_from_espn,
                        "team_id":       player_team_id,
                        "opponent":      opponent,
                        "home":          home["team"]["displayName"],
                        "away":          away["team"]["displayName"],
                        "home_score":    home.get("score", ""),
                        "away_score":    away.get("score", ""),
                        "won":           won,
                        "game_date_il":  game_date_il_display,
                        "dnp":           is_dnp or minutes_val == 0,
                        "min":           stat_map.get("MIN", "0"),
                        "pts":           stat_map.get("PTS", "0"),
                        "reb":           stat_map.get("REB", "0"),
                        "ast":           stat_map.get("AST", "0"),
                        "stl":           stat_map.get("STL", "0"),
                        "blk":           stat_map.get("BLK", "0"),
                        "fg":            stat_map.get("FG", "0-0"),
                        "three_pt":      stat_map.get("3PT", "0-0"),
                        "ft":            stat_map.get("FT", "0-0"),
                        "to":            stat_map.get("TO", "0"),
                        "pf":            stat_map.get("PF", "0"),
                        "plus_minus":    stat_map.get("+/-", "0"),
                    }
    return None


def fetch_all_player_stats(players: dict, yesterday_il: str,
                           target_date: str = "") -> list[dict]:
    """
    Fetch stats for all enabled players sequentially.
    0.3s delay between calls. Skips on error.
    Returns list of stat dicts (only players who played or DNP).
    """
    results = []
    player_list = list(players.items())
    for i, (espn_id, info) in enumerate(player_list):
        if i > 0:
            time.sleep(0.3)
        try:
            stats = fetch_player_stats(espn_id, yesterday_il, target_date)
            if stats:
                # Ensure player_name falls back to Firestore name
                if not stats["player_name"]:
                    stats["player_name"] = info["name"]
                results.append(stats)
        except Exception as e:
            print(f"   ⚠️  Error fetching {info['name']} ({espn_id}): {e}")
            continue
    return results


# ─────────────────────────────────────────────────────────────────────────────
# FIRESTORE SYNC — auto-update team names
# ─────────────────────────────────────────────────────────────────────────────

def update_player_teams(doc_id: str, results: list[dict], players: dict) -> None:
    """
    For each player in results, if ESPN returned a different team name
    than Firestore, update the team field. Batched into a single PATCH.
    """
    updates = {}
    for stat in results:
        espn_id = stat["espn_id"]
        espn_team = stat.get("team", "")
        if espn_id in players and espn_team and players[espn_id].get("team", "") != espn_team:
            updates[espn_id] = espn_team

    if not updates:
        return

    # Read full tracked_players, apply changes, write back
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
        f"/databases/(default)/documents/users/{doc_id}"
        f"?key={FIREBASE_API_KEY}"
    )
    try:
        data = _fetch_json(url)
    except Exception:
        return

    tp_field = data.get("fields", {}).get("tracked_players", {}).get("mapValue", {}).get("fields", {})
    changed = False
    for espn_id, new_team in updates.items():
        if espn_id in tp_field:
            tp_field[espn_id]["mapValue"]["fields"]["team"] = {"stringValue": new_team}
            changed = True
            print(f"   🔄 {players[espn_id]['name']}: team updated → {new_team}")

    if not changed:
        return

    # PATCH only tracked_players field
    patch_url = url + "&updateMask.fieldPaths=tracked_players"
    body = json.dumps({
        "fields": {
            "tracked_players": {"mapValue": {"fields": tp_field}}
        }
    }).encode()
    req = urllib.request.Request(patch_url, data=body, method="PATCH",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        print(f"   ⚠️  Firestore team sync failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL PARTITIONING — priority cascade
# ─────────────────────────────────────────────────────────────────────────────

def partition_players_to_emails(stats: list[dict], toggles: dict,
                                 players: dict) -> dict:
    """
    Assign each player to exactly one email bucket via priority cascade:
    1. Dedicated (Avdija only, if toggle ON)
    2. Israeli (tagged 'israeli', not in dedicated, if toggle ON)
    3. General (all remaining, if toggle ON)

    Returns {"dedicated": [...], "israeli": [...], "general": [...]}.
    Empty list = that email not sent.
    """
    dedicated = []
    israeli = []
    general = []

    assigned = set()  # espn_ids already assigned

    # Step 1: Dedicated Avdija
    if toggles.get("avdija_dedicated"):
        for s in stats:
            if s["espn_id"] == AVDIJA_ESPN_ID:
                dedicated.append(s)
                assigned.add(s["espn_id"])
                break

    # Step 2: Israeli players (tagged "israeli", not already assigned)
    if toggles.get("israeli"):
        for s in stats:
            if s["espn_id"] in assigned:
                continue
            player_info = players.get(s["espn_id"], {})
            if "israeli" in player_info.get("tags", []):
                israeli.append(s)
                assigned.add(s["espn_id"])

    # Step 3: General (all remaining enabled players)
    if toggles.get("general"):
        for s in stats:
            if s["espn_id"] in assigned:
                continue
            general.append(s)
            assigned.add(s["espn_id"])

    return {"dedicated": dedicated, "israeli": israeli, "general": general}


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL BUILDING — subject lines
# ─────────────────────────────────────────────────────────────────────────────

def format_subject_line(players_in_email: list[dict], email_type: str) -> str:
    """
    Adaptive subject line based on player count and email type.
    email_type: "dedicated", "israeli", or "general"
    """
    played = [p for p in players_in_email if not p.get("dnp")]
    dnp_only = len(played) == 0

    if email_type == "israeli":
        prefix = "🇮🇱"
    else:
        prefix = "🏀"

    if dnp_only:
        # Only DNP players
        if len(players_in_email) == 1:
            p = players_in_email[0]
            return f"{prefix} {p['player_name']} — DNP — {p['game_date_il']}"
        return f"{prefix} {len(players_in_email)} players — DNP — last night"

    if len(played) == 1:
        # Single player — personal format
        p = played[0]
        result = "W" if p["won"] else "L"
        return (f"{prefix} {p['player_name']} — {p['pts']} pts / {p['reb']} reb / "
                f"{p['ast']} ast ({result}) — {p['game_date_il']}")

    if len(played) <= 3:
        # 2-3 players — compact personal
        parts = []
        for p in played:
            first_name = p["player_name"].split()[-1]  # last name
            parts.append(f"{first_name}: {p['pts']}p/{p['reb']}r/{p['ast']}a")
        return f"{prefix} " + " · ".join(parts) + " — last night"

    # 4+ players — generic
    # Count unique games
    game_keys = set()
    for p in played:
        game_keys.add(f"{p['home']}_{p['away']}")
    n_games = len(game_keys)

    if email_type == "israeli":
        return f"{prefix} {len(played)} Israeli players — last night's stats"
    return f"{prefix} {len(played)} players, {n_games} games — last night's stats"


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL BUILDING — HTML body
# ─────────────────────────────────────────────────────────────────────────────

def build_player_stat_card_html(ps: dict) -> str:
    """Build HTML for a single player stat card (same layout as Avdija card)."""
    if ps.get("dnp"):
        return f"""
        <div style="margin:12px 0; padding:12px 16px; background:#f8fafc;
                    border-radius:8px; border-left:3px solid #94a3b8;">
          <div style="font-size:13px; font-weight:600; color:#64748b;">
            🪑 {ps['player_name']} — DNP
          </div>
          <div style="font-size:11px; color:#94a3b8; margin-top:2px;">
            {ps['away']} @ {ps['home']} ({ps['game_date_il']})
          </div>
        </div>"""

    result_color = "#16a34a" if ps["won"] else "#dc2626"
    result_text  = "Win" if ps["won"] else "Loss"
    pm_val = ps.get("plus_minus", "0")
    try:
        pm_color = "#16a34a" if int(pm_val) > 0 else ("#dc2626" if int(pm_val) < 0 else "#64748b")
        pm_display = f"+{pm_val}" if int(pm_val) > 0 else str(pm_val)
    except (ValueError, TypeError):
        pm_color = "#64748b"
        pm_display = pm_val

    return f"""
        <div style="margin:12px 0; padding:12px 16px; background:#eff6ff;
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


def build_player_stats_email_html(players: list[dict], email_type: str) -> str:
    """
    Build full HTML email body for a player stats email.
    Groups players by team when email_type == "general".
    """
    if email_type == "israeli":
        header_emoji = "🇮🇱"
        header_title = "Israeli Players — Last Night"
        header_bg = "#1e3a5f"
    elif email_type == "dedicated":
        header_emoji = "🏀"
        header_title = "Player Stats"
        header_bg = "#0f172a"
    else:
        header_emoji = "🏀"
        header_title = "NBA Stats — Last Night"
        header_bg = "#0f172a"

    # Group by team for general email, flat for others
    if email_type == "general" and len(players) > 1:
        # Group by team, sort by minutes desc within team
        teams = {}
        for p in players:
            team = p.get("team", "Unknown")
            teams.setdefault(team, []).append(p)
        # Sort players within team by minutes (desc)
        for team in teams:
            teams[team].sort(key=lambda x: int(x.get("min", "0") if x.get("min", "0").isdigit() else "0"),
                             reverse=True)

        cards_html = ""
        for team_name in sorted(teams.keys()):
            team_players = teams[team_name]
            cards_html += f"""
            <div style="margin-top:16px;">
              <div style="font-size:12px; font-weight:700; color:#6b7280;
                          text-transform:uppercase; letter-spacing:0.05em;
                          padding:8px 0; border-bottom:1px solid #e5e7eb;">
                {team_name}
              </div>"""
            for p in team_players:
                cards_html += build_player_stat_card_html(p)
            cards_html += "</div>"
    else:
        cards_html = ""
        for p in players:
            cards_html += build_player_stat_card_html(p)

    return f"""
    <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                       background:#f8fafc; margin:0; padding:20px;">
      <div style="max-width:520px; margin:0 auto; background:white; border-radius:16px;
                  overflow:hidden; box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <div style="background:{header_bg}; padding:20px 24px;">
          <div style="font-size:40px; margin-bottom:4px; line-height:1;">{header_emoji}</div>
          <h1 style="color:white; margin:0; font-size:18px; font-weight:700;">{header_title}</h1>
        </div>
        <div style="padding:8px 24px 16px;">
          {cards_html}
        </div>
        <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb;">
          <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=stats"
             style="font-size:12px; color:#6b7280; text-decoration:none;">
            ✏️ Manage players at sports-reminder-ui.vercel.app
          </a>
          <div style="margin-top:12px;font-size:12px;color:#999;">
            <a href="https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=unsubscribe" style="color:#999;text-decoration:underline;">Manage preferences / Unsubscribe</a>
          </div>
        </div>
      </div>
    </body></html>
    """


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL SENDING
# ─────────────────────────────────────────────────────────────────────────────

def _send_one_email(gmail_user: str, gmail_pass: str, to: str,
                    subject: str, html: str, plain: str) -> bool:
    """Send a single email. gmail_user/gmail_pass kept for backward compat but ignored."""
    return send_raw_email(to, subject, html, plain)


def _build_plain_text(players: list[dict]) -> str:
    """Build plain-text fallback for a player stats email."""
    lines = []
    for ps in players:
        if ps.get("dnp"):
            lines.append(f"🪑 {ps['player_name']} — DNP ({ps['away']} @ {ps['home']}, {ps['game_date_il']})")
        else:
            result = "W" if ps["won"] else "L"
            pm = ps.get("plus_minus", "0")
            try:
                pm = f"+{pm}" if int(pm) > 0 else str(pm)
            except (ValueError, TypeError):
                pass
            lines.append(
                f"🏀 {ps['player_name']} | {ps['away']} {ps['away_score']}–{ps['home_score']} {ps['home']} ({result}, {ps['game_date_il']})\n"
                f"   {ps['min']} min · {ps['pts']} pts · {ps['reb']} reb · {ps['ast']} ast · {pm}\n"
                f"   FG {ps['fg'].replace('-','/')} · 3PT {ps['three_pt'].replace('-','/')} · FT {ps['ft'].replace('-','/')}"
                f" · {ps['stl']} stl · {ps['blk']} blk · {ps['to']} to · {ps['pf']} pf"
            )
    return "\n\n".join(lines) + "\n\nEdit players: https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=stats
Unsubscribe: https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=unsubscribe"


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATION — main entry point
# ─────────────────────────────────────────────────────────────────────────────

def send_player_stats_emails(doc_id: str, gmail_user: str, gmail_pass: str,
                              target_date: str, send: bool = True) -> None:
    """
    Main entry point for multi-player stats emails.
    target_date: today in Israel (YYYY-MM-DD). Yesterday = target_date - 1 day.
    send: if False, dry-run only (print what would be sent).
    """
    print("\n📊 Multi-player stats mode")

    # 1. Load toggles
    toggles = load_player_email_toggles(doc_id)
    if not any(toggles.values()):
        print("   All player stats email toggles are OFF → skipping.")
        return

    active = [k for k, v in toggles.items() if v]
    print(f"   Email toggles: {', '.join(active)}")

    # 2. Load tracked players
    players = load_tracked_players(doc_id)
    if not players:
        print("   No tracked players found → skipping.")
        return
    print(f"   {len(players)} tracked player(s) enabled")

    # 3. Compute yesterday (Israel time)
    dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
    yesterday_dt = dt - datetime.timedelta(days=1)
    yesterday_il = yesterday_dt.strftime("%Y-%m-%d")
    print(f"   Checking games from: {yesterday_il} (Israel time)")

    # 4. Scoreboard early exit
    if not check_nba_games_yesterday(yesterday_il):
        print("   No NBA games yesterday → skipping all player fetching.")
        return

    # 5. Fetch all player stats
    print(f"   Fetching stats for {len(players)} players (sequential, 0.3s delay)...")
    stats = fetch_all_player_stats(players, yesterday_il, target_date)

    played_count = sum(1 for s in stats if not s.get("dnp"))
    dnp_count    = sum(1 for s in stats if s.get("dnp"))
    print(f"   Results: {played_count} played, {dnp_count} DNP, "
          f"{len(players) - played_count - dnp_count} off day")

    if not stats:
        print("   No players played yesterday → no email sent.")
        return

    # 6. Auto-sync team names (side-effect)
    update_player_teams(doc_id, stats, players)

    # 7. Partition into email buckets
    buckets = partition_players_to_emails(stats, toggles, players)

    # 8. Send emails (most personal first)
    send_order = [
        ("dedicated", buckets["dedicated"]),
        ("israeli",   buckets["israeli"]),
        ("general",   buckets["general"]),
    ]

    emails_sent = 0
    for email_type, bucket_stats in send_order:
        if not bucket_stats:
            continue

        subject = format_subject_line(bucket_stats, email_type)
        html    = build_player_stats_email_html(bucket_stats, email_type)
        plain   = _build_plain_text(bucket_stats)

        names = ", ".join(s["player_name"] for s in bucket_stats)
        print(f"\n   📧 [{email_type}] {len(bucket_stats)} player(s): {names}")
        print(f"      Subject: {subject}")

        if send:
            if emails_sent > 0:
                print(f"      ⏳ Waiting 5s before next email...")
                time.sleep(5)

            ok = _send_one_email(gmail_user, gmail_pass, gmail_user,
                                 subject, html, plain)
            if ok:
                print(f"      ✅ Sent!")
                emails_sent += 1
            else:
                print(f"      ❌ Failed!")
        else:
            print(f"      (dry-run — not sending)")

    print(f"\n   📊 Done: {emails_sent} email(s) sent.")
