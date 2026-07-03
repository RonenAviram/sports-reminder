"""Firestore helper functions for SportsReminder.

Database connection, document loading, and field parsing utilities.
"""

import logging

from config import USERS_COLLECTION

logger = logging.getLogger(__name__)

_firestore_db_sr = None

__all__ = [
    "_get_db",
    "_firestore_get_doc",
    "_firestore_bool",
    "_firestore_string",
    "load_global_config",
    "load_user_doc",
    "load_all_users",
    "load_avdija_stats_flag",
    "load_weekly_digest_flag",
    "load_world_cup_mode_flag",
    "load_tracked_teams",
]

def _get_db():
    global _firestore_db_sr
    if _firestore_db_sr is not None:
        return _firestore_db_sr
    from google.cloud import firestore
    _firestore_db_sr = firestore.Client()
    return _firestore_db_sr

def _firestore_get_doc(collection: str, doc_id: str) -> dict:
    """Fetch a single Firestore document via Admin SDK. Returns native dict or {}."""
    try:
        doc = _get_db().collection(collection).document(doc_id).get()
        return doc.to_dict() if doc.exists else {}
    except Exception as e:
        logger.warning("Could not read Firestore %s/%s: %s", collection, doc_id, e)
        return {}

def _firestore_bool(fields: dict, key: str, default: bool = False) -> bool:
    """Read a boolean from a native Firestore dict."""
    val = fields.get(key, default)
    if isinstance(val, bool):
        return val
    return default

def _firestore_string(fields: dict, key: str, default: str = "") -> str:
    """Read a string from a native Firestore dict."""
    val = fields.get(key, default)
    if isinstance(val, str):
        return val
    return default

def load_global_config() -> dict:
    """Load global config (config/global). Returns dict with world_cup_mode etc."""
    fields = _firestore_get_doc("config", "global")
    return {
        "world_cup_mode": _firestore_bool(fields, "world_cup_mode", False),
        "world_cup_end_date": _firestore_string(fields, "world_cup_end_date", ""),
    }


def load_user_doc(doc_id: str) -> dict:
    """Load a full user document from users/{doc_id}. Single read via Admin SDK."""
    fields = _firestore_get_doc(USERS_COLLECTION, doc_id)
    if not fields:
        return {}

    # Admin SDK returns native Python types — teams is a plain list of dicts
    teams_raw = fields.get("teams", [])
    teams = []
    if isinstance(teams_raw, list):
        for t in teams_raw:
            if not isinstance(t, dict):
                continue
            enabled = t.get("enabled", True)
            if isinstance(enabled, bool) and not enabled:
                continue
            teams.append({
                "name":     t.get("name", ""),
                "sport":    t.get("sport", ""),
                "leagueId": t.get("leagueId", ""),
                "league":   t.get("league", ""),
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
        "israeli_players_email": _firestore_bool(fields, "israeli_players_email", False),
        "player_stats_email":   _firestore_bool(fields, "player_stats_email", False),
        "emails_paused":        _firestore_bool(fields, "emails_paused", False),
        "synthetic":            _firestore_bool(fields, "synthetic", False),
    }

def load_all_users() -> list[dict]:
    """Load all active users from users/ collection via Admin SDK."""
    try:
        docs = _get_db().collection(USERS_COLLECTION).stream()
    except Exception as e:
        logger.warning("Could not list users: %s", e)
        return []

    users = []
    for doc in docs:
        doc_id = doc.id
        fields = doc.to_dict() or {}
        status = fields.get("status", "active")
        if not isinstance(status, str):
            status = "active"
        if status != "active":
            logger.info("   ⭏️  Skipping user %s (status=%s)", doc_id, status)
            continue
        user = load_user_doc(doc_id)
        if user:
            users.append(user)
    return users

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

def load_tracked_teams(doc_id: str, enabled_only: bool = True) -> list[dict]:
    """
    Returns list of dicts: [{name, sport, leagueId, league, enabled}, ...]
    Uses Firestore Admin SDK.

    enabled_only=True  = skip teams where enabled=false
    enabled_only=False = return ALL teams regardless of enabled flag
    If a team has no "enabled" field it is treated as enabled=True.
    """
    fields = _firestore_get_doc("configs", doc_id)
    if not fields:
        return []

    teams_raw = fields.get("teams", [])
    teams = []
    if isinstance(teams_raw, list):
        for t in teams_raw:
            if not isinstance(t, dict):
                continue
            enabled = t.get("enabled", True)
            if isinstance(enabled, bool) and enabled_only and not enabled:
                continue
            elif not isinstance(enabled, bool):
                enabled = True
                if enabled_only:
                    pass  # treat as enabled
            teams.append({
                "name":     t.get("name", ""),
                "sport":    t.get("sport", ""),
                "leagueId": t.get("leagueId", ""),
                "league":   t.get("league", ""),
                "enabled":  enabled if isinstance(enabled, bool) else True,
            })
    return teams

