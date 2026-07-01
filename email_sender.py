"""
email_sender.py — Email abstraction layer for SportsReminder.

All email sending goes through send_raw_email(). Swap the provider
here without touching any business logic.

Provider selection (automatic):
  - If RESEND_API_KEY is set  → Resend API  (preferred)
  - If GMAIL_APP_PASSWORD set → Gmail SMTP  (legacy fallback)

Email logging:
  Every send attempt is logged to Firestore collection 'email_logs'
  with timestamp, recipient, type, status, subject, and provider.
  Logging is best-effort — failures are printed but never block sending.
"""

import os
import datetime

# ── Config ───────────────────────────────────────────────────────────────────────────────

# Resend (preferred)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = os.environ.get("RESEND_FROM", "Sports Reminder <noreply@sportsreminder.pro>")

# Gmail SMTP (legacy fallback)
GMAIL_SENDER       = "ronen6213@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def _send_via_resend(to: str, subject: str, html: str, plain: str) -> tuple:
    """Send email via Resend API. Returns (success, resend_email_id)."""
    try:
        import resend
    except ImportError:
        print("❌  resend package not installed. Run: pip install resend")
        return False, ""

    resend.api_key = RESEND_API_KEY
    params = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": plain,
    }
    try:
        resp = resend.Emails.send(params)
        email_id = getattr(resp, "id", "") or (resp.get("id", "") if isinstance(resp, dict) else "")
        print(f"✅  Email sent to {to} (Resend, id={email_id})")
        return True, email_id
    except Exception as e:
        print(f"❌  Email failed ({to}, Resend): {e}")
        return False, ""


def _send_via_gmail(to: str, subject: str, html: str, plain: str) -> bool:
    """Send email via Gmail SMTP (legacy fallback)."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.header import Header

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = to
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to, msg.as_string())
        print(f"✅  Email sent to {to} (Gmail)")
        return True
    except Exception as e:
        print(f"❌  Email failed ({to}, Gmail): {e}")
        return False


# ── Firestore logging (best-effort) ──────────────────────────────────────────────

_firestore_db = None

def _get_firestore_db():
    """Lazy-init Firestore client. Returns None if unavailable."""
    global _firestore_db
    if _firestore_db is not None:
        return _firestore_db
    try:
        from google.cloud import firestore as _fs
        _firestore_db = _fs.Client()
        return _firestore_db
    except Exception as e:
        print(f"⚠️  Firestore not available for email logging: {e}")
        return None

def _log_email(to: str, subject: str, email_type: str, status: str,
               provider: str, error: str = "", resend_email_id: str = "",
               synthetic: bool = False):
    """Log email send attempt to Firestore. Best-effort — never raises."""
    try:
        db = _get_firestore_db()
        if db is None:
            return
        doc = {
            "to": to,
            "subject": subject,
            "email_type": email_type,
            "status": status,
            "provider": provider,
            "synthetic": synthetic,
            "error": error,
            "timestamp": datetime.datetime.utcnow(),
        }
        if resend_email_id:
            doc["resend_email_id"] = resend_email_id
        db.collection("email_logs").add(doc)
    except Exception as e:
        print(f"⚠️  Email log write failed: {e}")


def send_raw_email(to: str, subject: str, html: str, plain: str,
                   email_type: str = "unknown", synthetic: bool = False) -> bool:
    # Auto-detect synthetic from email address
    if "+synthetic" in to:
        synthetic = True
    if synthetic:
        subject = "SportsReminder Synthetic Test"
    """
    Send a single email.

    Provider auto-selected:
      - RESEND_API_KEY set → Resend API
      - GMAIL_APP_PASSWORD set → Gmail SMTP
      - Neither → error

    Args:
        to:         recipient email address
        subject:    email subject (may contain Unicode / emoji)
        html:       HTML body
        plain:      plain-text fallback body
        email_type: label for logging — 'morning', 'stats', 'weekly', etc.

    Returns:
        True on success, False on failure.
    """
    if RESEND_API_KEY:
        ok, resend_id = _send_via_resend(to, subject, html, plain)
        _log_email(to, subject, email_type, "sent" if ok else "failed", "resend",
                   "" if ok else "send failed", resend_email_id=resend_id, synthetic=synthetic)
        return ok

    if GMAIL_APP_PASSWORD:
        ok = _send_via_gmail(to, subject, html, plain)
        _log_email(to, subject, email_type, "sent" if ok else "failed", "gmail",
                   "" if ok else "send failed", synthetic=synthetic)
        return ok

    print("❌  No email provider configured.")
    print("    Set RESEND_API_KEY (preferred) or GMAIL_APP_PASSWORD (legacy).")
    _log_email(to, subject, email_type, "failed", "none", "no provider configured", synthetic=synthetic)
    return False
