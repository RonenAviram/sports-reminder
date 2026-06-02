"""
email_sender.py — Email abstraction layer for SportsReminder.

All email sending goes through send_raw_email(). Swap the provider
here without touching any business logic.

Current provider: Gmail SMTP (App Password).
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# ── Config ──────────────────────────────────────────────────────────────────────────
GMAIL_SENDER       = "ronen6213@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def send_raw_email(to: str, subject: str, html: str, plain: str) -> bool:
    """
    Send a single email.

    Args:
        to:      recipient email address
        subject: email subject (may contain Unicode / emoji)
        html:    HTML body
        plain:   plain-text fallback body

    Returns:
        True on success, False on failure.
    """
    if not GMAIL_APP_PASSWORD:
        print("\u274c  GMAIL_APP_PASSWORD not set. Export it as an env variable:")
        print("    export GMAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx'")
        return False

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
        print("\u2705  Email sent to " + to)
        return True
    except Exception as e:
        print("\u274c  Email failed (" + to + "): " + str(e))
        return False
