#!/usr/bin/env python3
"""
Sports Reminder — Local Admin Server
Run:  python3 server.py
Then open: http://localhost:5000
"""

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Import our reminder logic
import sports_reminder as sr

PORT = 5000

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PERSISTENCE — save/load app password from config.json
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(data: dict):
    try:
        existing = load_config()
        existing.update(data)
        with open(CONFIG_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save config: {e}")

# Load saved password on startup
_cfg = load_config()
if _cfg.get("gmail_app_password"):
    sr.GMAIL_APP_PASSWORD = _cfg["gmail_app_password"]
    os.environ["GMAIL_APP_PASSWORD"] = _cfg["gmail_app_password"]
