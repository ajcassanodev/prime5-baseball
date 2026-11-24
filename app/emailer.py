"""Email via SMTP (stdlib only), mirroring the football app's emailer.

Config from the environment — same variable names as the football app
so the server's existing env works as-is:
    RAVIX_SMTP_HOST   (default: smtpout.secureserver.net)
    RAVIX_SMTP_PORT   (default: 465, implicit TLS)
    RAVIX_SMTP_USER   (mailbox login, e.g. info@ravixfs.com)
    RAVIX_SMTP_PASS   (mailbox password)
    RAVIX_EMAIL_FROM  (default: info@ravixfs.com)
    RAVIX_SMTP_STARTTLS=1 for explicit TLS (e.g. Resend on port 587)

send_email() never raises: it returns False when unconfigured or when
the send fails, so approving a registration never breaks because mail
isn't set up — the caller logs the pick link instead.
"""
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("prime5bb.emailer")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(raw) -> str | None:
    """Trim and sanity-check an email address. Returns the cleaned address
    or None when unusable."""
    addr = (raw or "").strip()
    if len(addr) > 254 or not EMAIL_RE.match(addr):
        return None
    return addr


def _config():
    return (
        os.environ.get("RAVIX_SMTP_HOST", "smtpout.secureserver.net"),
        int(os.environ.get("RAVIX_SMTP_PORT", "465")),
        os.environ.get("RAVIX_SMTP_USER", ""),
        os.environ.get("RAVIX_SMTP_PASS", ""),
        os.environ.get("RAVIX_EMAIL_FROM", "info@ravixfs.com"),
        os.environ.get("RAVIX_SMTP_STARTTLS", "") == "1",
    )


def send_email(to: str, subject: str, body: str) -> bool:
    """Send one email via SMTP. Returns True on success.

    Uses implicit SSL by default; set RAVIX_SMTP_STARTTLS=1 for
    explicit TLS (STARTTLS), e.g. on port 587.
    Never raises: returns False when unconfigured or when the send fails.
    """
    host, port, user, pw, sender, starttls = _config()
    if not (user and pw):
        log.warning(
            "Email not configured (missing RAVIX_SMTP_USER/PASS); "
            "skipping send to %s", to
        )
        return False
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if starttls:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=ctx)
                smtp.login(user, pw)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as smtp:
                smtp.login(user, pw)
                smtp.send_message(msg)
        log.info("Email sent to %s", to)
        return True
    except Exception as exc:  # network, auth, bad address -- log and move on
        log.warning("Email send failed for %s: %s", to, exc)
        return False
