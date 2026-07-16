"""
Welcome Email Cloud Function — Firestore Trigger (2nd gen).

Fires when a users/{uid} document is updated in Firestore.
Sends a one-time welcome email via Resend when:
  1. The user has teams OR tracked players (first save happened)
  2. welcome_email_sent is not yet True

Deployed to: GCP project sports-reminder-55578, region me-west1
"""

import os
import functions_framework
from google.cloud import firestore
from cloudevents.http import CloudEvent

db = firestore.Client()

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "Sports Reminder <noreply@sportsreminder.pro>"


def _has_teams(data: dict) -> bool:
    """Check if user has any tracked teams."""
    teams = data.get("teams", [])
    return isinstance(teams, list) and len(teams) > 0


def _has_players(data: dict) -> bool:
    """Check if user has any tracked players."""
    tp = data.get("tracked_players", {})
    if not isinstance(tp, dict):
        return False
    for v in tp.values():
        if v is True or (isinstance(v, dict) and v.get("enabled")):
            return True
    return False


def _build_welcome_html() -> str:
    """Build the welcome email HTML — Stadium Lights design v6."""
    whatsapp_url = "https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT"
    edit_url = "https://app.sportsreminder.pro/?utm_source=email&utm_medium=welcome&utm_campaign=sports_reminder"
    unsub_url = "https://app.sportsreminder.pro/?utm_source=email&utm_medium=unsubscribe&utm_campaign=sports_reminder"
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background-color:#0f172a; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0f172a;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px; border:1px solid #1e293b; border-radius:10px;">

<!-- Header -->
<tr><td style="padding:20px 24px; text-align:center;">
  <span style="font-size:13px; font-weight:700; color:#ffffff; letter-spacing:2px;">SPORTS REMINDER</span>
</td></tr>

<!-- Hero -->
<tr><td style="padding:24px 28px 20px; text-align:center;">
  <div style="margin-bottom:16px;">
    <span style="display:inline-block; background-color:#f59e0b; color:#0f172a; font-size:13px; font-weight:700; padding:4px 14px; border-radius:20px; letter-spacing:0.5px;">WELCOME</span>
  </div>
  <div style="font-size:22px; font-weight:700; color:#ffffff; margin-bottom:8px;">You're all set!</div>
  <div style="font-size:14px; color:#94a3b8; line-height:1.5;">Your preferences have been saved.<br>Here's what's coming.</div>
</td></tr>

<!-- Feature 1: Daily -->
<tr><td style="padding:0 24px 10px;">
  <div style="background-color:#1e293b; border-radius:10px; padding:16px 18px;">
    <div style="font-size:14px; font-weight:600; color:#f8fafc; margin-bottom:4px;">Daily matches every morning</div>
    <div style="font-size:13px; color:#94a3b8; line-height:1.4;">Your teams' games, with times and add-to-calendar links.</div>
  </div>
</td></tr>

<!-- Feature 2: Stats -->
<tr><td style="padding:0 24px 10px;">
  <div style="background-color:#1e293b; border-radius:10px; padding:16px 18px;">
    <div style="font-size:14px; font-weight:600; color:#f8fafc; margin-bottom:4px;">Player stats recap</div>
    <div style="font-size:13px; color:#94a3b8; line-height:1.4;">NBA box scores &#8212; points, rebounds, assists and more.</div>
  </div>
</td></tr>

<!-- Feature 3: Weekly -->
<tr><td style="padding:0 24px 24px;">
  <div style="background-color:#1e293b; border-radius:10px; padding:16px 18px;">
    <div style="font-size:14px; font-weight:600; color:#f8fafc; margin-bottom:4px;">Weekly preview on Saturday</div>
    <div style="font-size:13px; color:#94a3b8; line-height:1.4;">A 7-day lookahead so you know what's ahead.</div>
  </div>
</td></tr>

<!-- Note -->
<tr><td style="padding:0 24px 20px; text-align:center;">
  <div style="border-top:1px solid #1e293b; padding-top:16px;">
    <div style="font-size:13px; color:#64748b;">No matches? No email.</div>
    <div style="font-size:13px; color:#64748b; margin-top:4px;">We only write when there's something to watch.</div>
  </div>
</td></tr>

<!-- WhatsApp CTA -->
<tr><td style="padding:0 24px 20px;">
  <div style="background-color:#1e293b; border-radius:8px; padding:14px 18px; text-align:center;">
    <a href="{whatsapp_url}" style="font-size:13px; color:#25D366; font-weight:600; text-decoration:none;">Get live updates on WhatsApp &#8594;</a>
  </div>
</td></tr>

<!-- Footer -->
<tr><td style="padding:16px 24px; border-top:1px solid #1e293b;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr>
    <td style="font-size:12px;"><a href="{edit_url}" style="color:#64748b; text-decoration:none;">Edit your teams</a></td>
    <td style="font-size:12px; text-align:right;"><a href="{unsub_url}" style="color:#64748b; text-decoration:none;">Manage preferences</a></td>
  </tr>
  </table>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _build_welcome_plain() -> str:
    """Plain text version of the welcome email — Stadium Lights v6."""
    return """You're all set!

Your preferences have been saved. Here's what's coming:

- Daily matches every morning — your teams' games, with times and add-to-calendar links.
- Player stats recap — NBA box scores: points, rebounds, assists and more.
- Weekly preview on Saturday — a 7-day lookahead so you know what's ahead.

No matches? No email.
We only write when there's something to watch.

Join our WhatsApp group: https://chat.whatsapp.com/CvTdxcgzCWBH2Pifds7odT

---
Sports Reminder
https://app.sportsreminder.pro"""


def _send_welcome_email(to: str) -> bool:
    """Send welcome email via Resend API."""
    try:
        import resend
    except ImportError:
        print("resend package not installed")
        return False

    resend.api_key = RESEND_API_KEY
    try:
        resp = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [to],
            "subject": "Welcome to Sports Reminder!",
            "html": _build_welcome_html(),
            "text": _build_welcome_plain(),
        })
        email_id = getattr(resp, "id", "") or (resp.get("id", "") if isinstance(resp, dict) else "")
        print(f"Welcome email sent to {to} (id={email_id})")

        # Log to Firestore
        import datetime
        db.collection("email_logs").add({
            "to": to,
            "subject": "Welcome to Sports Reminder!",
            "email_type": "welcome",
            "status": "sent",
            "provider": "resend",
            "resend_email_id": email_id,
            "timestamp": datetime.datetime.utcnow(),
        })
        return True
    except Exception as e:
        print(f"Failed to send welcome email to {to}: {e}")
        return False


@functions_framework.cloud_event
def on_user_update(cloud_event: CloudEvent):
    """Firestore trigger — fires on users/{uid} document update."""

    # Extract document path and data
    data = cloud_event.data
    doc_path = data.get("value", {}).get("name", "")
    uid = doc_path.split("/")[-1] if doc_path else ""

    # Get the current document fields
    fields = data.get("value", {}).get("fields", {})

    # Convert Firestore REST fields to simple dict
    def _parse_value(v):
        if "stringValue" in v:
            return v["stringValue"]
        if "booleanValue" in v:
            return v["booleanValue"]
        if "integerValue" in v:
            return int(v["integerValue"])
        if "arrayValue" in v:
            return [_parse_value(el) for el in v.get("arrayValue", {}).get("values", [])]
        if "mapValue" in v:
            return {k: _parse_value(fv) for k, fv in v.get("mapValue", {}).get("fields", {}).items()}
        if "nullValue" in v:
            return None
        return str(v)

    doc = {k: _parse_value(v) for k, v in fields.items()}

    print(f"Trigger fired for user {uid}")

    # Skip if welcome email already sent
    if doc.get("welcome_email_sent"):
        print(f"Welcome email already sent to {uid}, skipping")
        return

    # Skip synthetic users
    if doc.get("synthetic"):
        print(f"Synthetic user {uid}, skipping")
        return

    # Check if user has teams or players (= first save happened)
    has_content = _has_teams(doc) or _has_players(doc)
    if not has_content:
        print(f"User {uid} has no teams or players yet, skipping")
        return

    # Get email address
    email = doc.get("email", "")
    if not email:
        print(f"User {uid} has no email, skipping")
        return

    # Send welcome email
    print(f"Sending welcome email to {email} (uid={uid})")
    success = _send_welcome_email(email)

    if success:
        # Mark as sent to prevent duplicates
        try:
            db.collection("users").document(uid).update({
                "welcome_email_sent": True
            })
            print(f"Marked welcome_email_sent=True for {uid}")
        except Exception as e:
            print(f"Failed to mark welcome_email_sent for {uid}: {e}")
