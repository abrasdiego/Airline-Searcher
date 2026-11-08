"""Send the report through any SMTP server (Gmail, Outlook, SendGrid, ...)."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from airline_searcher.report import Report


class EmailConfigError(RuntimeError):
    pass


@dataclass
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]

    @classmethod
    def from_env(cls) -> "SmtpSettings":
        missing = [k for k in ("SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_TO") if not os.environ.get(k)]
        if missing:
            raise EmailConfigError(f"Missing environment variables: {', '.join(missing)}")
        username = os.environ["SMTP_USERNAME"]
        return cls(
            host=os.environ.get("SMTP_HOST") or "smtp.gmail.com",
            port=int(os.environ.get("SMTP_PORT") or 587),
            username=username,
            password=os.environ["SMTP_PASSWORD"],
            sender=os.environ.get("EMAIL_FROM") or username,
            recipients=[r.strip() for r in os.environ["EMAIL_TO"].split(",") if r.strip()],
        )


def build_message(report: Report, settings: SmtpSettings) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = report.subject
    msg["From"] = settings.sender
    msg["To"] = ", ".join(settings.recipients)
    msg.set_content(report.text)
    msg.add_alternative(report.html, subtype="html")
    return msg


def send_report(report: Report, settings: SmtpSettings) -> None:
    msg = build_message(report, settings)
    context = ssl.create_default_context()
    if settings.port == 465:
        with smtplib.SMTP_SSL(settings.host, settings.port, context=context, timeout=60) as smtp:
            smtp.login(settings.username, settings.password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.host, settings.port, timeout=60) as smtp:
            smtp.starttls(context=context)
            smtp.login(settings.username, settings.password)
            smtp.send_message(msg)
