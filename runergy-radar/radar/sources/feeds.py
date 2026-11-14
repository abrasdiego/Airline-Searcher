"""RSS and Atom feeds: Google Alerts (free, covers Reddit, forums and the wider web) and any
forum or blog feed you add on the Settings page."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlparse

from radar.sources import SourceError, fetch
from radar.text import Found, is_web_url, parse_when


def is_alert_feed(url: str) -> bool:
    parts = urlparse(url)
    return parts.netloc.lower().endswith("google.com") and parts.path.startswith("/alerts/feeds")


def unwrap(link: str) -> str:
    """Google Alerts links go through google.com/url?...&url=<the real page>."""
    parts = urlparse(link)
    if parts.netloc.lower().endswith("google.com") and parts.path == "/url":
        for key in ("url", "q"):
            target = parse_qs(parts.query).get(key, [""])[0]
            if is_web_url(target):
                return target
    return link


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child_text(node, *names: str) -> str:
    for child in node:
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _link(node) -> str:
    fallback = ""
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:                                         # Atom
            if child.attrib.get("rel", "alternate") == "alternate":
                return href.strip()
            fallback = fallback or href.strip()
        elif (child.text or "").strip():                 # RSS
            return child.text.strip()
    return fallback or _child_text(node, "guid", "id")


def parse_feed(content: bytes, name: str, trusted: bool, now: Optional[datetime] = None) -> list[Found]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SourceError(f"“{name}” isn't a readable RSS or Atom feed.") from exc
    entries = [node for node in root.iter() if _local(node.tag) in ("item", "entry")]
    if not entries and _local(root.tag) not in ("rss", "feed", "rdf"):
        raise SourceError(f"“{name}” isn't an RSS or Atom feed (is it the feed link, not the page?).")
    found = []
    for node in entries[:100]:
        link = unwrap(_link(node))
        if not is_web_url(link):
            continue
        author = ""
        for child in node:
            if _local(child.tag) in ("author", "creator"):
                author = _child_text(child, "name") or "".join(child.itertext()).strip()
                break
        found.append(Found(
            url=link,
            title=_child_text(node, "title"),
            body=_child_text(node, "encoded", "content", "summary", "description"),
            author=author,
            created_at=parse_when(_child_text(node, "published", "pubdate", "date", "updated"), now),
            source=name, trusted=trusted))
    return found


def read_feed(feed: dict, now: Optional[datetime] = None) -> list[Found]:
    name = feed.get("name") or feed["url"]
    response = fetch(feed["url"], what=f"“{name}”")
    if response.status_code >= 400:
        raise SourceError(f"“{name}” answered with HTTP {response.status_code}.")
    return parse_feed(response.content, name, trusted=is_alert_feed(feed["url"]), now=now)
