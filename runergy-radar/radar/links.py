"""The link that goes into replies: one place decides it, so it can be changed at any time.

Today it points to the website; later it can point to an App Store redirect. Changing it in
Settings also updates every reply that has not been posted yet.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MODES = ("when_mentioned", "always", "never")
PLATFORM_TAGS = {"reddit": "reddit", "threads": "threads", "web": "forum"}
_PATH = r"(?:/[^\s<>\"')\]]*)?"
_TRAILING = ".,;:!?"


def reply_link(settings: dict, platform: str = "") -> str:
    """The link for a reply on this platform ('' when no link is set)."""
    base = settings["brand"]["url"]
    if not base:
        return ""
    if not settings["link"]["track"] or platform not in PLATFORM_TAGS:
        return base
    parts = urlparse(base)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("utm_source", "utm_medium", "utm_campaign")]
    query += [("utm_source", PLATFORM_TAGS[platform]), ("utm_medium", "community"),
              ("utm_campaign", "radar")]
    return urlunparse(parts._replace(query=urlencode(query)))


def all_links(settings: dict) -> dict:
    return {platform: reply_link(settings, platform) for platform in PLATFORM_TAGS}


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(host: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@/.\-])(?:https?://)?(?:www\.)?{re.escape(host)}{_PATH}", re.I)


def has_link(draft: str, link: str) -> bool:
    return bool(link) and link in draft


def enforce(draft: str, settings: dict, platform: str) -> str:
    """Make an AI-written reply carry exactly the chosen link, exactly as Settings says."""
    link = reply_link(settings, platform)
    mode = settings["link"]["mode"]
    if not draft.strip() or not link:
        return draft
    pattern = _same_site(_host(link))

    def swap(match: re.Match) -> str:
        found = match.group(0)
        tail = ""
        while found and found[-1] in _TRAILING:
            found, tail = found[:-1], found[-1] + tail
        return ("" if mode == "never" else link) + tail

    draft = pattern.sub(swap, draft)
    if mode == "never":
        return re.sub(r"[ \t]+\n", "\n", draft).strip()
    mentioned = settings["brand"]["name"].lower() in draft.lower()
    if link not in draft and (mode == "always" or mentioned):
        draft = draft.rstrip() + "\n\n" + link
    return draft


def replace_link(draft: str, old: str, new: str) -> str:
    """Swap one exact link for another, leaving longer links that merely start the same."""
    if not old or old == new or old not in draft:
        return draft
    pattern = re.compile(re.escape(old.rstrip("/")) + r"/?(?![\w/?=&%#\-])")
    return pattern.sub(lambda _: new, draft)


def relink_drafts(store, before: dict, after: dict) -> int:
    """After the link changes, update it in every reply that hasn't been posted. Returns how many."""
    olds, news = all_links(before), all_links(after)
    plain_old, plain_new = before["brand"]["url"], after["brand"]["url"]
    if olds == news or not plain_old or not plain_new:
        return 0
    changed = 0
    for thread in store.drafts_containing(_host(plain_old)):
        platform = thread["platform"]
        draft = thread["draft"]
        # the tracked form first (it contains the plain one), then the plain one
        pairs = sorted({(olds.get(platform, plain_old), news.get(platform, plain_new)),
                        (plain_old, news.get(platform, plain_new))}, key=lambda p: -len(p[0]))
        for old, new in pairs:
            draft = replace_link(draft, old, new)
        if draft != thread["draft"]:
            store.update_thread(thread["id"], draft=draft)
            changed += 1
    return changed
