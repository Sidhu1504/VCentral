"""Email (SMTP) and Google Chat delivery. Every attempt is written to notification_log."""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .config import get_settings
from .models import NotificationLog

log = logging.getLogger(__name__)
TIMEOUT = 20


def email_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def chat_configured() -> bool:
    return bool(get_settings().google_chat_webhook)


def _record(db: Session, channel: str, recipient: str, kind: str, subject: str, vm_ids, error: str | None) -> bool:
    db.add(NotificationLog(channel=channel, recipient=recipient[:512], kind=kind, subject=subject[:512],
                           vm_ids=list(vm_ids or []), success=error is None, error=error))
    if error:
        log.warning("%s to %s failed: %s", channel, recipient, error)
    return error is None


def send_email(db: Session, to: list[str], subject: str, text: str, html: str | None = None,
               kind: str = "reminder", vm_ids=None) -> bool:
    s = get_settings()
    recipient = ", ".join(to)
    if not email_configured():
        return _record(db, "email", recipient, kind, subject, vm_ids, "Email is not configured (VCENTRAL_SMTP_*)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, recipient, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=context)
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        error = None
    except Exception as exc:  # network, auth, refused recipient
        error = f"{type(exc).__name__}: {exc}"
    return _record(db, "email", recipient, kind, subject, vm_ids, error)


def send_chat(db: Session, text: str, subject: str, kind: str = "escalation", vm_ids=None) -> bool:
    url = get_settings().google_chat_webhook
    if not url:
        return _record(db, "chat", "Google Chat", kind, subject, vm_ids, "Google Chat webhook is not configured")
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                 headers={"Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read()
        error = None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return _record(db, "chat", "Google Chat", kind, subject, vm_ids, error)
