"""Threads keyword search (Meta's official Threads API).

Needs THREADS_ACCESS_TOKEN from a Meta app with the threads_keyword_search permission. Until Meta
approves that permission for the app (App Review), the search only returns your own posts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from radar.sources import SourceError, env, fetch
from radar.text import Found, parse_when

API = "https://graph.threads.net"
PER_SCAN_CAP = 5
FIELDS = "id,text,permalink,timestamp,username"


def access_token() -> str:
    return env("THREADS_ACCESS_TOKEN")


def _json(response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceError(f"Threads sent an unreadable answer (HTTP {response.status_code}).") from exc
    error = data.get("error") if isinstance(data, dict) else {"message": "unexpected answer"}
    if error:
        message = str(error.get("message") if isinstance(error, dict) else error)[:180]
        if response.status_code in (401, 403) or (isinstance(error, dict) and error.get("code") == 190):
            raise SourceError(f"Threads didn't accept the access token ({message}). "
                              "Create a new one and update THREADS_ACCESS_TOKEN.")
        raise SourceError(f"Threads: {message}")
    if response.status_code >= 400:
        raise SourceError(f"Threads answered with HTTP {response.status_code}.")
    return data


def search(phrase: str, token: str, now: Optional[datetime] = None) -> list[Found]:
    data = _json(fetch(f"{API}/v1.0/keyword_search", what="Threads", params={
        "q": phrase, "search_type": "RECENT", "fields": FIELDS, "access_token": token}))
    found = []
    for post in data.get("data") or []:
        link, text = post.get("permalink"), post.get("text")
        if not link or not text:
            continue
        found.append(Found(
            url=link, body=text, author=post.get("username") or "",
            created_at=parse_when(post.get("timestamp"), now), source="Threads search",
            phrase=phrase, trusted=True))
    return found


def refresh(token: str) -> str:
    """A long-lived token lasts 60 days; refreshing it gives another 60."""
    data = _json(fetch(f"{API}/refresh_access_token", what="Threads", params={
        "grant_type": "th_refresh_token", "access_token": token}))
    fresh = data.get("access_token")
    if not fresh:
        raise SourceError("Threads didn't return a refreshed token.")
    return fresh
