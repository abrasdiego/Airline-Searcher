"""Comments on a Facebook Page you manage (Meta's Graph API).

Facebook does not let any tool search public posts or groups, so this covers the one place it
allows: people commenting on your own Page. Needs FACEBOOK_PAGE_ID and FACEBOOK_PAGE_TOKEN (a Page
access token with pages_read_engagement and pages_read_user_content).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from radar.sources import SourceError, env, fetch
from radar.text import Found, parse_when

API = "https://graph.facebook.com"
FIELDS = ("permalink_url,message,created_time,"
          "comments.limit(25).order(reverse_chronological){id,message,created_time,permalink_url,from}")


def page() -> tuple[str, str]:
    return env("FACEBOOK_PAGE_ID"), env("FACEBOOK_PAGE_TOKEN")


def comments(page_id: str, token: str, now: Optional[datetime] = None) -> list[Found]:
    response = fetch(f"{API}/{page_id}/feed", what="Facebook",
                     params={"fields": FIELDS, "limit": 15, "access_token": token})
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceError(f"Facebook sent an unreadable answer (HTTP {response.status_code}).") from exc
    error = data.get("error") if isinstance(data, dict) else {"message": "unexpected answer"}
    if error:
        message = str(error.get("message") if isinstance(error, dict) else error)[:180]
        if isinstance(error, dict) and error.get("code") == 190:
            raise SourceError("Facebook's Page token has expired or was revoked. Create a new one "
                              "and update FACEBOOK_PAGE_TOKEN.")
        raise SourceError(f"Facebook: {message}")
    found = []
    for post in data.get("data") or []:
        about = (post.get("message") or "").strip()
        for comment in (post.get("comments") or {}).get("data") or []:
            author = comment.get("from") or {}
            link, text = comment.get("permalink_url") or post.get("permalink_url"), comment.get("message")
            if not link or not text or str(author.get("id")) == str(page_id):
                continue                                   # skip the Page's own replies
            found.append(Found(
                url=link, title=text if len(text) <= 110 else "", body=text if len(text) > 110 else
                (f"Comment on your post: {about[:300]}" if about else ""),
                author=author.get("name") or "", created_at=parse_when(comment.get("created_time"), now),
                source="Facebook Page", trusted=True, extra={"own_page": True}))
    return found
