"""
health_check.py — API Health Check for SportsReminder.

Runs before the daily email (08:30 IL) and validates that every
data source returns a well-formed response with expected fields.

Results are written to Firestore collection 'api_health_checks'.
If any API fails, an alert email is sent to the admin.

Usage:
    python3 health_check.py              # run all checks, log to Firestore
    python3 health_check.py --dry-run    # run checks, print results, no Firestore/email
"""

import json
import sys
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import traceback

# ── Config ───────────────────────────────────────────────────────────────────

ADMIN_EMAIL = "ronen6213@gmail.com"

# ESPN endpoints to check (league_id → URL)
ESPN_CHECKS = {
    "nba":              "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "premier_league":   "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "la_liga":          "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "bundesliga":       "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard",
    "serie_a":          "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "ligue_1":          "https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/scoreboard",
    "champions_league": "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard",
    "europa_league":    "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/scoreboard",
    "mls":              "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
    "fifa_world_cup":   "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
}

# EuroLeague endpoints
EUROLEAGUE_CHECKS = {
    "euroleague": ("E", "E2025"),
    "eurocup":    ("U", "U2025"),
}

# TheSportsDB endpoints
TSDB_FREE_KEY = "3"
TSDB_CHECKS = {
    "israeli_pl_basketball": "Israeli Basketball Premier League",
    "israeli_pl_soccer":     "Israeli Premier League",
}

# ESPN Player Stats endpoint (sample player — LeBron)
ESPN_PLAYER_STATS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/players/1966/gamelog"

# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 15) -> dict:
    """Fetch URL and parse JSON. Raises on any error."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
        "Referer": "https://www.espn.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _fetch_xml(url: str, timeout: int = 15) -> ET.Element:
    """Fetch URL and parse XML. Raises on any error."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/xml,text/xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.euroleague.net",
        "Referer": "https://www.euroleague.net/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return ET.fromstring(data)


# ── Check functions ──────────────────────────────────────────────────────────

def check_espn(league_id: str, url: str) -> dict:
    """
    Check an ESPN endpoint.
    Validates: HTTP 200, JSON parseable, has 'events' list,
    each event has 'competitions' with 'competitors'.
    """
    result = {
        "api": "espn",
        "league": league_id,
        "url": url,
        "status": "ok",
        "error": "",
        "details": {},
    }
    try:
        today = datetime.datetime.utcnow().strftime("%Y%m%d")
        check_url = f"{url}?dates={today}"
        data = _fetch_json(check_url)

        # Validate structure
        if "events" not in data:
            result["status"] = "structure_error"
            result["error"] = "Missing 'events' key in response"
            return result

        events = data["events"]
        result["details"]["event_count"] = len(events)

        # Spot-check first event structure (if any exist)
        if events:
            ev = events[0]
            if "competitions" not in ev:
                result["status"] = "structure_error"
                result["error"] = "Event missing 'competitions' key"
                return result
            comps = ev["competitions"]
            if comps:
                comp = comps[0]
                if "competitors" not in comp:
                    result["status"] = "structure_error"
                    result["error"] = "Competition missing 'competitors' key"
                    return result
                competitors = comp["competitors"]
                if len(competitors) < 2:
                    result["status"] = "structure_error"
                    result["error"] = f"Expected 2+ competitors, got {len(competitors)}"
                    return result
                # Check competitor has team info
                c = competitors[0]
                if "team" not in c:
                    result["status"] = "structure_error"
                    result["error"] = "Competitor missing 'team' key"
                    return result

        result["details"]["sample_valid"] = True

    except urllib.error.HTTPError as e:
        result["status"] = "http_error"
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        result["status"] = "connection_error"
        result["error"] = f"Connection failed: {e.reason}"
    except json.JSONDecodeError as e:
        result["status"] = "parse_error"
        result["error"] = f"JSON parse failed: {e}"
    except Exception as e:
        result["status"] = "unknown_error"
        result["error"] = str(e)

    return result


def check_espn_player_stats() -> dict:
    """Check ESPN player stats/gamelog endpoint.
    NBA offseason: 404 is expected (no recent games) \u2192 marked as
    'expected_failure' so it is logged but does NOT trigger an alert email.
    """
    result = {
        "api": "espn_player_stats",
        "league": "nba",
        "url": ESPN_PLAYER_STATS_URL,
        "status": "ok",
        "error": "",
        "details": {},
    }
    try:
        data = _fetch_json(ESPN_PLAYER_STATS_URL)

        # Validate structure — gamelog has different shape
        # Expected: data with player info or categories/labels/events
        has_valid_shape = False
        if isinstance(data, dict):
            # Gamelog can have 'events' or 'categories' or 'seasonTypes'
            for key in ("events", "categories", "seasonTypes", "labels"):
                if key in data:
                    has_valid_shape = True
                    result["details"]["found_key"] = key
                    break
            # Also check nested structure
            if not has_valid_shape and "player" in data:
                has_valid_shape = True
                result["details"]["found_key"] = "player"

        if not has_valid_shape:
            result["status"] = "structure_error"
            result["error"] = f"Unexpected response keys: {list(data.keys())[:5]}"

    except urllib.error.HTTPError as e:
        result["status"] = "http_error"
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        result["status"] = "unknown_error"
        result["error"] = str(e)

    return result


def check_euroleague(league_id: str, comp_code: str, season_code: str) -> dict:
    """
    Check EuroLeague/EuroCup API.
    Validates: HTTP 200, XML parseable, has <item> elements.
    """
    url = f"https://api-live.euroleague.net/v1/schedules?seasonCode={season_code}"
    result = {
        "api": "euroleague",
        "league": league_id,
        "url": url,
        "status": "ok",
        "error": "",
        "details": {},
    }
    try:
        root = _fetch_xml(url)

        items = root.findall("item")
        result["details"]["item_count"] = len(items)

        if not items:
            # Could be offseason — not necessarily broken
            result["details"]["note"] = "No items found (may be offseason)"
        else:
            # Spot-check first item
            item = items[0]
            home = item.findtext("hometeam")
            away = item.findtext("awayteam")
            if home is None or away is None:
                result["status"] = "structure_error"
                result["error"] = "Item missing 'hometeam' or 'awayteam'"
                return result
            result["details"]["sample_home"] = home.strip()
            result["details"]["sample_valid"] = True

    except urllib.error.HTTPError as e:
        result["status"] = "http_error"
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        result["status"] = "connection_error"
        result["error"] = f"Connection failed: {e.reason}"
    except ET.ParseError as e:
        result["status"] = "parse_error"
        result["error"] = f"XML parse failed: {e}"
    except Exception as e:
        result["status"] = "unknown_error"
        result["error"] = str(e)

    return result


def check_thesportsdb(league_id: str, league_name: str) -> dict:
    """
    Check TheSportsDB API.
    Validates: HTTP 200, JSON parseable, response has valid structure.
    """
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    url = (f"https://www.thesportsdb.com/api/v1/json/{TSDB_FREE_KEY}"
           f"/eventsday.php?d={today}&l={urllib.parse.quote(league_name)}")
    result = {
        "api": "thesportsdb",
        "league": league_id,
        "url": url,
        "status": "ok",
        "error": "",
        "details": {},
    }
    try:
        data = _fetch_json(url, timeout=15)

        # TheSportsDB returns {"events": [...]} or {"events": null}
        if "events" not in data:
            result["status"] = "structure_error"
            result["error"] = "Missing 'events' key in response"
            return result

        events = data["events"] or []
        result["details"]["event_count"] = len(events)

        # Spot-check first event
        if events:
            ev = events[0]
            for field in ("strHomeTeam", "strAwayTeam"):
                if field not in ev:
                    result["status"] = "structure_error"
                    result["error"] = f"Event missing '{field}'"
                    return result
            result["details"]["sample_valid"] = True

    except urllib.error.HTTPError as e:
        result["status"] = "http_error"
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        result["status"] = "unknown_error"
        result["error"] = str(e)

    return result


# ── Retry logic ──────────────────────────────────────────────────────────────

def run_check_with_retry(check_fn, *args, retries: int = 3, delay: float = 5.0) -> dict:
    """Run a check function with retries on connection/HTTP errors."""
    import time
    last_result = None
    for attempt in range(retries):
        result = check_fn(*args)
        if result["status"] == "ok":
            return result
        last_result = result
        # Only retry on transient errors
        if result["status"] in ("connection_error", "http_error"):
            if attempt < retries - 1:
                print(f"  ⚠️  {result['league']} failed (attempt {attempt+1}/{retries}), retrying in {delay}s...")
                time.sleep(delay)
            continue
        # Structure/parse errors — don't retry
        break
    return last_result


# ── Main orchestrator ────────────────────────────────────────────────────────

def run_all_checks() -> list[dict]:
    """Run all API health checks. Returns list of result dicts."""
    results = []
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    # ESPN scoreboard endpoints
    print("🔍 Checking ESPN endpoints...")
    for league_id, url in ESPN_CHECKS.items():
        print(f"  → {league_id}...", end=" ", flush=True)
        r = run_check_with_retry(check_espn, league_id, url)
        r["timestamp"] = timestamp
        results.append(r)
        status_icon = "✅" if r["status"] == "ok" else "❌"
        print(f"{status_icon} {r['status']}", end="")
        if r.get("details", {}).get("event_count") is not None:
            print(f" ({r['details']['event_count']} events)", end="")
        if r["error"]:
            print(f" — {r['error']}", end="")
        print()

    # ESPN player stats
    print("  → player_stats...", end=" ", flush=True)
    r = run_check_with_retry(check_espn_player_stats)
    r["timestamp"] = timestamp
    results.append(r)
    status_icon = "✅" if r["status"] == "ok" else "❌"
    print(f"{status_icon} {r['status']}")

    # EuroLeague / EuroCup
    print("🔍 Checking EuroLeague endpoints...")
    for league_id, (comp, season) in EUROLEAGUE_CHECKS.items():
        print(f"  → {league_id}...", end=" ", flush=True)
        r = run_check_with_retry(check_euroleague, league_id, comp, season)
        r["timestamp"] = timestamp
        results.append(r)
        status_icon = "✅" if r["status"] == "ok" else "❌"
        print(f"{status_icon} {r['status']}", end="")
        if r.get("details", {}).get("item_count") is not None:
            print(f" ({r['details']['item_count']} items)", end="")
        print()

    # TheSportsDB
    print("🔍 Checking TheSportsDB endpoints...")
    for league_id, league_name in TSDB_CHECKS.items():
        print(f"  → {league_id}...", end=" ", flush=True)
        r = run_check_with_retry(check_thesportsdb, league_id, league_name)
        r["timestamp"] = timestamp
        results.append(r)
        status_icon = "✅" if r["status"] == "ok" else "❌"
        print(f"{status_icon} {r['status']}")

    return results


# ── Firestore logging ────────────────────────────────────────────────────────

def save_to_firestore(results: list[dict]):
    """Save health check results to Firestore."""
    try:
        from google.cloud import firestore
        db = firestore.Client()
    except Exception as e:
        print(f"⚠️  Firestore not available: {e}")
        return

    timestamp = datetime.datetime.utcnow()
    batch = db.batch()

    # Write each result as a separate doc
    for r in results:
        doc_ref = db.collection("api_health_checks").document()
        doc = {
            "api": r["api"],
            "league": r["league"],
            "status": r["status"],
            "error": r["error"],
            "details": r.get("details", {}),
            "timestamp": timestamp,
        }
        batch.set(doc_ref, doc)

    # Also write a summary doc (latest status per API)
    summary_ref = db.collection("api_health_checks").document("_latest")
    summary = {
        "timestamp": timestamp,
        "checks": {},
        "all_ok": all(r["status"] in ("ok", "expected_failure") for r in results),
        "failed_count": sum(1 for r in results if r["status"] not in ("ok", "expected_failure")),
    }
    for r in results:
        key = f"{r['api']}_{r['league']}"
        summary["checks"][key] = {
            "status": r["status"],
            "error": r["error"],
        }
    batch.set(summary_ref, summary)

    batch.commit()
    print(f"📝 Saved {len(results)} check results + summary to Firestore")


# ── Alert email ──────────────────────────────────────────────────────────────

def send_alert_email(results: list[dict]):
    """Send alert email if any API check failed (excluding expected_failure)."""
    failed = [r for r in results if r["status"] not in ("ok", "expected_failure")]
    if not failed:
        return

    from email_sender import send_raw_email

    subject = f"⚠️ API Health Alert — {len(failed)} check(s) failed"

    # Build HTML
    rows = ""
    for r in failed:
        rows += f"""
        <tr>
            <td style="padding:8px;border:1px solid #ddd;font-weight:bold">{r['api']}</td>
            <td style="padding:8px;border:1px solid #ddd">{r['league']}</td>
            <td style="padding:8px;border:1px solid #ddd;color:#dc2626">{r['status']}</td>
            <td style="padding:8px;border:1px solid #ddd;font-size:13px">{r['error']}</td>
        </tr>"""

    ok_count = sum(1 for r in results if r["status"] == "ok")
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;padding:20px">
        <h2 style="color:#dc2626;margin-bottom:4px">⚠️ API Health Check Failed</h2>
        <p style="color:#666;margin-top:0">{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
        <p><strong>{ok_count}</strong> passed, <strong style="color:#dc2626">{len(failed)}</strong> failed out of {len(results)} total checks.</p>
        <table style="border-collapse:collapse;width:100%;margin:16px 0">
            <tr style="background:#f9fafb">
                <th style="padding:8px;border:1px solid #ddd;text-align:left">API</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left">League</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left">Status</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left">Error</th>
            </tr>
            {rows}
        </table>
        <p style="color:#666;font-size:13px">This alert was sent by the SportsReminder health check system.<br>
        Check the Admin tab for full details: <a href="https://sports-reminder-ui.vercel.app">Admin Dashboard</a></p>
    </div>"""

    plain_lines = [f"⚠️ API Health Check Failed — {len(failed)} check(s) failed", ""]
    for r in failed:
        plain_lines.append(f"❌ {r['api']} / {r['league']}: {r['status']} — {r['error']}")
    plain = "\n".join(plain_lines)

    ok = send_raw_email(ADMIN_EMAIL, subject, html, plain, email_type="health_alert")
    if ok:
        print(f"\xf0\x9f\x93\xa7 Alert email sent to {ADMIN_EMAIL}")
    else:
        print(f"\xe2\x9d\x8c Alert email FAILED for {ADMIN_EMAIL}")


# ── CLI entrypoint ───────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print("🏥 SportsReminder API Health Check")
    print(f"   {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    results = run_all_checks()

    # Summary
    ok = sum(1 for r in results if r["status"] in ("ok", "expected_failure"))
    fail = len(results) - ok
    expected = sum(1 for r in results if r["status"] == "expected_failure")
    print()
    print(f"📊 Summary: {ok}/{len(results)} passed", end="")
    if expected:
        print(f" ({expected} expected failure)", end="")
    if fail:
        print(f", {fail} FAILED ❌")
    else:
        print(" \u2014 all healthy ✅")

    if dry_run:
        print("\n🏃 Dry run — skipping Firestore + email")
        return

    # Save to Firestore
    save_to_firestore(results)

    # Send alert if needed
    send_alert_email(results)

    # Exit with error code if any check failed (for Cloud Run monitoring)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
