"""
email_sender.py — Email abstraction layer for SportsReminder.

All email sending goes through send_raw_email(). Swap the provider
here without touching any business logic.

Provider selection (automatic):
  - If RESEND_API_KEY is set  → Resend API  (preferred)
  - If GMAIL_APP_PASSWORD set → Gmail SMTP  (legacy fallback)
"""

import os

# ── Config ───────────────────────────────────────────────────────────────────────────────

# Resend (preferred)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = os.environ.get("RESEND_FROM", "Sports Reminder <noreply@sportsreminder.pro>")

# Gmail SMTP (legacy fallback)
GMAIL_SENDER       = "ronen6213@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def _send_via_resend(to: str, subject: str, html: str, plain: str) -> bool:
    """Send email via Resend API."""
    try:
        import resend
    except ImportError:
        print("❌  resend package not installed. Run: pip install resend")
        return False

    resend.api_key = RESEND_API_KEY
    params = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": plain,
        "headers": {
            "List-Unsubscribe": "<https://sports-reminder-ui.vercel.app?utm_source=email&utm_medium=unsubscribe>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"
        },
    }
    try:
        resend.Emails.send(params)
        print(f"✅  Email sent to {to} (Resend)")
        return True
    except Exception as e:
        print(f"❌  Email failed ({to}, Resend): {e}")
        return False


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


def send_raw_email(to: str, subject: str, html: str, plain: str) -> bool:
    """
    Send a single email.

    Provider auto-selected:
      - RESEND_API_KEY set → Resend API
      - GMAIL_APP_PASSWORD set → Gmail SMTP
      - Neither → error

    Args:
        to:      recipient email address
        subject: email subject (may contain Unicode / emoji)
        html:    HTML body
        plain:   plain-text fallback body

    Returns:
        True on success, False on failure.
    """
    if RESEND_API_KEY:
        return _send_via_resend(to, subject, html, plain)

    if GMAIL_APP_PASSWORD:
        return _send_via_gmail(to, subject, html, plain)

    print("❌  No email provider configured.")
    print("    Set RESEND_API_KEY (preferred) or GMAIL_APP_PASSWORD (legacy).")
    return False
