"""Tells you about hot matches without you opening the dashboard: Telegram and/or email.

Telegram needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Email needs SMTP_USERNAME, SMTP_PASSWORD
and EMAIL_TO (SMTP_HOST / SMTP_PORT / EMAIL_FROM optional; Gmail by default), the same names the
flight searcher uses.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

import requests

from radar.text import stamp


class AlertError(Exception):
    pass


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def channels() -> list[str]:
    found = []
    if _env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"):
        found.append("telegram")
    if _env("SMTP_USERNAME") and _env("SMTP_PASSWORD") and _env("EMAIL_TO"):
        found.append("email")
    return found


def send_telegram(text: str) -> None:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{_env('TELEGRAM_BOT_TOKEN')}/sendMessage", timeout=15,
            json={"chat_id": _env("TELEGRAM_CHAT_ID"), "text": text[:4000],
                  "disable_web_page_preview": True})
    except requests.RequestException as exc:       # never include the address: it holds the token
        raise AlertError(f"Telegram couldn't be reached ({type(exc).__name__}).") from None
    if response.status_code != 200:
        try:
            reason = str(response.json().get("description") or "")[:120]
        except ValueError:
            reason = ""
        raise AlertError(f"Telegram refused the message (HTTP {response.status_code}"
                         + (f": {reason}" if reason else "") + "). Check the bot token and chat id.")


def send_email(subject: str, text: str) -> None:
    username = _env("SMTP_USERNAME")
    host, port = _env("SMTP_HOST") or "smtp.gmail.com", int(_env("SMTP_PORT") or 587)
    message = EmailMessage()
    message["Subject"] = subject[:200]
    message["From"] = _env("EMAIL_FROM") or username
    recipients = [r.strip() for r in _env("EMAIL_TO").split(",") if r.strip()]
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
                smtp.login(username, _env("SMTP_PASSWORD"))
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=context)
                smtp.login(username, _env("SMTP_PASSWORD"))
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise AlertError("The email server didn't accept the username or password "
                         "(Gmail needs an App Password).") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise AlertError(f"The email couldn't be sent ({type(exc).__name__}).") from None


def send(subject: str, text: str) -> tuple[list[str], list[str]]:
    """Send on every configured channel. Returns (channels that worked, problems)."""
    sent, problems = [], []
    for channel in channels():
        try:
            if channel == "telegram":
                send_telegram(f"{subject}\n\n{text}")
            else:
                send_email(subject, text)
            sent.append(channel)
        except AlertError as exc:
            problems.append(str(exc))
    return sent, problems


def compose(threads: list[dict], followups: int, base: str) -> tuple[str, str]:
    lines = []
    for thread in threads:
        lines.append(f"{thread['score']}  {thread['title']}")
        lines.append(f"    {thread['community']}" + (f" · {thread['ai_reason']}" if thread["ai_reason"] else ""))
        lines.append(f"    {thread['url']}")
        lines.append("")
    if followups:
        lines.append(f"{followups} repl{'y' if followups == 1 else 'ies'} to check back on: "
                     "see whether anyone answered you.")
        lines.append("")
    if base:
        lines.append(f"Open Radar: {base}")
    if threads:
        subject = (f"Radar: {len(threads)} hot conversation{'s' if len(threads) != 1 else ''} to reply to"
                   if len(threads) > 1 else f"Radar: reply now? {threads[0]['title'][:80]}")
    else:
        subject = f"Radar: {followups} repl{'y' if followups == 1 else 'ies'} to check back on"
    return subject, "\n".join(lines).strip()


def notify(store, settings: dict, now: datetime, ai_on: bool) -> tuple[int, list[str]]:
    """Alert about hot new conversations (once each) and due follow-ups (once a day)."""
    if not settings["alerts"]["enabled"] or not channels():
        return 0, []
    hot = store.hot_unalerted(stamp(now - timedelta(hours=36)), settings["alerts"]["min_score"], ai_on)
    today = now.strftime("%Y-%m-%d")
    followups = store.followups_due(stamp(now)) if store.get_meta("followup_alert_day") != today else 0
    if not hot and not followups:
        return 0, []
    subject, text = compose(hot, followups, settings.get("_base", ""))
    sent, problems = send(subject, text)
    if sent:                                   # only mark as told once somebody was actually told
        for thread in hot:
            store.update_thread(thread["id"], alerted=stamp(now))
        if followups:
            store.set_meta("followup_alert_day", today)
    store.set_meta("alerts_health", {"ok": not problems, "at": stamp(now), "sent": sent,
                                     "message": problems[0] if problems else ""})
    return (len(hot) if sent else 0), [f"Alerts: {p}" for p in problems]
