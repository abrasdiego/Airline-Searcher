"""Text helpers: tidy links, recognise the platform, read dates, match trigger phrases, score."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse

STAMP = "%Y-%m-%dT%H:%M:%SZ"
_TRACKING = re.compile(r"^(utm_.*|ref|ref_src|ref_source|share_id|rdt|igshid|fbclid|gclid|xmt|slof|"
                       r"context|share|sh|si|s|st|t_ref)$", re.I)
_REDDIT_THREAD = re.compile(r"^/r/([^/]+)/comments/([a-z0-9]+)", re.I)
_REDDIT_SUB = re.compile(r"^/r/([^/]+)", re.I)
_THREADS_POST = re.compile(r"^/@([^/]+)/post/([^/?#]+)", re.I)
_TAG = re.compile(r"<[^>]+>")
_QUESTION = re.compile(
    r"\?|\b(recommend\w*|suggestion\w*|looking for|any advice|advice on|which (app|plan|one)|"
    r"what (app|plan)|anyone (use|used|tried|know)|how (do|should|many|much|to)|help me|"
    r"need help|tips for|thoughts on|worth it)\b", re.I)


@dataclass
class Found:
    """One conversation as a source found it, before it is scored and stored."""
    url: str
    title: str = ""
    body: str = ""
    author: str = ""
    created_at: Optional[datetime] = None
    source: str = ""
    phrase: str = ""        # the trigger phrase whose search found it, if any
    trusted: bool = False   # found by a search for a trigger phrase (so no literal match needed)
    extra: dict = field(default_factory=dict)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp(when: Optional[datetime]) -> str:
    return when.astimezone(timezone.utc).strftime(STAMP) if when else ""


def unstamp(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text, STAMP).replace(tzinfo=timezone.utc) if text else None
    except ValueError:
        return None


def clean_text(value: Optional[str]) -> str:
    """Plain text from a snippet that may contain HTML tags and entities."""
    if not value:
        return ""
    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", value, flags=re.I)
    text = html.unescape(_TAG.sub("", text)).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    for prefix in ("www.", "old.", "new.", "np.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def canonical_url(url: str) -> str:
    """One spelling per conversation, so the same thread found twice is stored once."""
    parts = urlparse(url.strip())
    host = _host(parts.netloc)
    path = re.sub(r"/+", "/", parts.path or "/")
    if host == "reddit.com" or host.endswith(".reddit.com"):
        match = _REDDIT_THREAD.match(path)
        if match:
            return f"https://reddit.com/r/{match.group(1).lower()}/comments/{match.group(2).lower()}"
        host = "reddit.com"
    if host in ("threads.net", "threads.com"):
        host = "threads.com"
        match = _THREADS_POST.match(path)
        if match:
            return f"https://threads.com/@{match.group(1).lower()}/post/{match.group(2)}"
    if host == "bsky.app":
        return f"https://bsky.app{path.rstrip('/').lower()}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not _TRACKING.match(k))
    path = path.rstrip("/") or "/"
    return f"https://{host}{path}" + (f"?{urlencode(query)}" if query else "")


def thread_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:20]


def is_web_url(url: str) -> bool:
    parts = urlparse(url or "")
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def platform_of(url: str) -> tuple[str, str]:
    """('reddit', 'r/running'), ('threads', '@someone'), or ('web', 'letsrun.com')."""
    parts = urlparse(url)
    host = _host(parts.netloc)
    if host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it":
        match = _REDDIT_SUB.match(parts.path or "")
        return "reddit", (f"r/{match.group(1)}" if match else "Reddit")
    if host in ("threads.net", "threads.com"):
        match = re.match(r"^/@([^/]+)", parts.path or "")
        return "threads", (f"@{match.group(1)}" if match else "Threads")
    if host == "bsky.app":
        match = re.match(r"^/profile/([^/]+)", parts.path or "")
        return "bluesky", (f"@{match.group(1)}" if match else "Bluesky")
    if host in ("facebook.com", "fb.com") or host.endswith(".facebook.com"):
        return "facebook", "Your Facebook Page"
    return "web", host


_RELATIVE = re.compile(r"(\d+|an?)\s*\+?\s*(sec|second|min|minute|hr|hour|day|week|wk|month|mo|year|yr)s?\b",
                       re.I)
_UNIT_SECONDS = {"sec": 1, "second": 1, "min": 60, "minute": 60, "hr": 3600, "hour": 3600,
                 "day": 86400, "week": 604800, "wk": 604800, "month": 2592000, "mo": 2592000,
                 "year": 31536000, "yr": 31536000}
_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y")


def parse_when(value, now: Optional[datetime] = None) -> Optional[datetime]:
    """A datetime from the many ways sources write one ('3 days ago', RFC 822, ISO 8601, ...)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = " ".join(str(value or "").split())
    if not text:
        return None
    now = now or now_utc()
    low = text.lower()
    if low in ("just now", "now", "today"):
        return now
    if low == "yesterday":
        return now - timedelta(days=1)
    if "ago" in low:
        match = _RELATIVE.search(low)
        if match:
            count = 1 if match.group(1) in ("a", "an") else int(match.group(1))
            return now - timedelta(seconds=count * _UNIT_SECONDS[match.group(2).lower()])
    iso = text.replace("Z", "+00:00")
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)   # +0000 -> +00:00
    try:
        parsed = datetime.fromisoformat(iso)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def phrase_pattern(phrase: str) -> Optional[re.Pattern]:
    """Matches the phrase as whole words, ignoring case and the exact spacing or hyphens."""
    words = re.findall(r"[^\s\-_/]+", phrase.strip().strip("\"'“”"))
    if not words:
        return None
    body = r"[\s\-_/]+".join(re.escape(word) for word in words)
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.I)


def has_phrase(text: str, phrase: str) -> bool:
    pattern = phrase_pattern(phrase)
    return bool(pattern and pattern.search(text))


def evaluate(found: Found, settings: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """The stored form of a conversation with its keyword score, or None if it should be skipped."""
    if not is_web_url(found.url):
        return None
    now = now or now_utc()
    title, body = clean_text(found.title), clean_text(found.body)
    if not title and not body:
        return None
    if found.created_at and found.created_at < now - timedelta(days=settings["max_age_days"]):
        return None
    text = f"{title}\n{body}"
    if any(has_phrase(text, word) for word in settings["exclude_words"]):
        return None

    matched, topics = [], []
    for topic in settings["topics"]:
        for phrase in topic["phrases"]:
            if has_phrase(text, phrase):
                matched.append(phrase)
                if topic["name"] not in topics:
                    topics.append(topic["name"])
    if not matched and not found.trusted:
        return None
    literal = len(matched)
    if found.phrase:
        for topic in settings["topics"]:
            if any(found.phrase.lower() == p.lower() for p in topic["phrases"]):
                if topic["name"] not in topics:
                    topics.append(topic["name"])
        if found.phrase.lower() not in (m.lower() for m in matched):
            matched.append(found.phrase)

    platform, community = platform_of(found.url)
    context = settings["context_words"]
    on_topic = not context or any(has_phrase(f"{text}\n{community} {found.url}", w) for w in context)
    if not on_topic and not found.trusted:
        return None
    if found.extra.get("own_page"):
        on_topic = True

    score = 45 + 10 * min(literal, 3)
    if any(has_phrase(title, phrase) for phrase in matched):
        score += 10
    if _QUESTION.search(text):
        score += 10
    if found.created_at and found.created_at > now - timedelta(hours=24):
        score += 5
    if not on_topic:
        score -= 25
    if found.extra.get("own_page"):
        score += 20

    if not title:
        first = body.splitlines()[0]
        title = first if len(first) <= 110 else first[:107].rsplit(" ", 1)[0] + "…"
    return {
        "id": thread_id(found.url),
        "platform": platform,
        "community": community[:120],
        "title": title[:300],
        "body": body[:4000],
        "url": found.url[:1000],
        "author": clean_text(found.author)[:120],
        "created_at": stamp(found.created_at),
        "found_at": stamp(now),
        "source": found.source[:80],
        "matched": matched[:12],
        "topics": topics[:8],
        "kw_score": max(5, min(99, score)),
    }
