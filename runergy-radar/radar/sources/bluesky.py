"""Bluesky post search. Free. Works without an account where Bluesky allows it; if Bluesky asks
for a sign-in, set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD (an app password, not your real one)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests

from radar.sources import USER_AGENT, SourceError, env, fetch
from radar.text import Found, parse_when

PUBLIC = "https://public.api.bsky.app"
SIGNED_IN = "https://bsky.social"
PER_SCAN_CAP = 4


def credentials() -> tuple[str, str]:
    return env("BLUESKY_HANDLE").lstrip("@"), env("BLUESKY_APP_PASSWORD")


def sign_in() -> str:
    """An access token for the account in BLUESKY_HANDLE / BLUESKY_APP_PASSWORD."""
    handle, password = credentials()
    try:
        response = requests.post(f"{SIGNED_IN}/xrpc/com.atproto.server.createSession", timeout=15,
                                 json={"identifier": handle, "password": password},
                                 headers={"User-Agent": USER_AGENT})
        data = response.json()
    except (requests.RequestException, ValueError):
        raise SourceError("Bluesky sign-in couldn't be reached.") from None
    if response.status_code != 200 or not data.get("accessJwt"):
        raise SourceError("Bluesky didn't accept the handle or app password "
                          "(BLUESKY_HANDLE / BLUESKY_APP_PASSWORD).")
    return data["accessJwt"]


def post_url(post: dict) -> str:
    handle = (post.get("author") or {}).get("handle") or ""
    key = str(post.get("uri") or "").rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{key}" if handle and key else ""


def search(phrase: str, token: str = "", now: Optional[datetime] = None) -> list[Found]:
    base = SIGNED_IN if token else PUBLIC
    response = fetch(f"{base}/xrpc/app.bsky.feed.searchPosts", what="Bluesky",
                     params={"q": phrase, "sort": "latest", "limit": 25},
                     headers={"Authorization": f"Bearer {token}"} if token else None)
    if response.status_code in (401, 403):
        raise SourceError("Bluesky wants a sign-in for search. Add BLUESKY_HANDLE and "
                          "BLUESKY_APP_PASSWORD (free; create an app password in Bluesky's settings)."
                          if not token else "Bluesky rejected the sign-in. Check the app password.")
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceError(f"Bluesky sent an unreadable answer (HTTP {response.status_code}).") from exc
    if response.status_code >= 400:
        raise SourceError(f"Bluesky: {str(data.get('message') or response.status_code)[:140]}")
    found = []
    for post in data.get("posts") or []:
        record = post.get("record") or {}
        link, text = post_url(post), record.get("text")
        if not link or not text:
            continue
        author = post.get("author") or {}
        found.append(Found(url=link, body=text, author=author.get("displayName") or author.get("handle") or "",
                           created_at=parse_when(record.get("createdAt") or post.get("indexedAt"), now),
                           source="Bluesky search", phrase=phrase, trusted=True))
    return found
