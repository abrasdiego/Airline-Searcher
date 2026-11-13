"""Default settings and the cleaning applied to anything saved from the Settings page.

Settings live in the database (so the dashboard can edit them); these are the starting values.
"""

from __future__ import annotations

import copy
from urllib.parse import urlparse

DEFAULT_SETTINGS: dict = {
    "brand": {
        "name": "Runergy Lab",
        "url": "",
        "description": (
            "Runergy Lab is a running app that gives runners a training plan together with "
            "fueling and nutrition guidance for training and race day."
        ),
        "voice": (
            "A friendly, experienced runner talking to another runner. Practical and specific, "
            "never salesy. Short paragraphs, plain words, no hashtags, no emojis."
        ),
        "disclosure": "Full disclosure: I'm part of the team building Runergy Lab.",
    },
    "topics": [
        {"name": "App recommendations", "phrases": [
            "best running app", "running app recommendation", "which running app",
            "running app for beginners", "alternative to Runna", "app for marathon training"]},
        {"name": "Training plans", "phrases": [
            "marathon training plan", "half marathon training plan", "5k training plan",
            "10k training plan", "couch to 5k", "personalized training plan"]},
        {"name": "Fueling & nutrition", "phrases": [
            "marathon fueling", "race day nutrition", "fueling strategy", "how many gels",
            "carb loading", "hitting the wall", "what to eat before a long run"]},
        {"name": "Brand mentions", "phrases": ["Runergy Lab", "Runergy"]},
    ],
    "context_words": [
        "run", "runs", "running", "runner", "runners", "marathon", "half marathon", "5k", "10k",
        "jog", "jogging", "race", "ultra", "trail", "parkrun", "long run"],
    "exclude_words": ["hiring", "job opening", "coupon code", "giveaway", "for sale"],
    "sites": ["reddit.com", "threads.com", "letsrun.com", "quora.com", "runnersworld.com",
              "slowtwitch.com"],
    "feeds": [],
    "max_age_days": 3,
    "serpapi_daily_budget": 30,
    "threads_enabled": True,
    "ai": {"enabled": True, "drafts_per_scan": 12, "min_relevance": 50},
}

MAX_TOPICS = 20
MAX_PHRASES = 40
MAX_FEEDS = 40


def defaults() -> dict:
    return copy.deepcopy(DEFAULT_SETTINGS)


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _long_text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _words(value, limit: int, each: int = 80) -> list[str]:
    """A list of short strings from a list, or from text split on commas and new lines."""
    if isinstance(value, str):
        value = value.replace("\n", ",").split(",")
    if not isinstance(value, (list, tuple)):
        return []
    out, seen = [], set()
    for item in value:
        word = _text(item, each).strip("\"'“”")
        if word and word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return out[:limit]


def _number(value, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _site(value: str) -> str:
    """'https://www.Reddit.com/r/running' -> 'reddit.com/r/running'."""
    site = value.strip().lower()
    if "://" in site:
        site = site.split("://", 1)[1]
    if site.startswith("www."):
        site = site[4:]
    return site.strip("/ ")


def clean_settings(raw) -> dict:
    """Settings that are safe to store: every field present, the right type, sensibly sized.

    Raises ValueError (with a message for the person) for things they should fix.
    """
    base = defaults()
    if not isinstance(raw, dict):
        return base
    out = base

    brand = raw.get("brand") if isinstance(raw.get("brand"), dict) else {}
    out["brand"] = {
        "name": _text(brand.get("name", base["brand"]["name"]), 80) or base["brand"]["name"],
        "url": _text(brand.get("url", ""), 300),
        "description": _long_text(brand.get("description", base["brand"]["description"]), 3000),
        "voice": _long_text(brand.get("voice", base["brand"]["voice"]), 1500),
        "disclosure": _long_text(brand.get("disclosure", base["brand"]["disclosure"]), 300),
    }
    if out["brand"]["url"] and urlparse(out["brand"]["url"]).scheme not in ("http", "https"):
        raise ValueError("The app link must start with https://")

    if "topics" in raw:
        topics = []
        for topic in raw["topics"] if isinstance(raw["topics"], list) else []:
            if not isinstance(topic, dict):
                continue
            phrases = _words(topic.get("phrases"), MAX_PHRASES, 100)
            name = _text(topic.get("name"), 60)
            if not name and not phrases:
                continue
            if not name:
                raise ValueError("Every topic needs a name.")
            if name.lower() in {t["name"].lower() for t in topics}:
                raise ValueError(f"Two topics are both called “{name}”. Give each a different name.")
            topics.append({"name": name, "phrases": phrases})
        out["topics"] = topics[:MAX_TOPICS]

    for key, limit in (("context_words", 80), ("exclude_words", 80)):
        if key in raw:
            out[key] = _words(raw[key], limit)
    if "sites" in raw:
        sites = [_site(s) for s in _words(raw["sites"], 12, 120)]
        for site in sites:
            if " " in site or "." not in site:
                raise ValueError(f"“{site}” doesn't look like a website. Use something like reddit.com")
        out["sites"] = [s for s in dict.fromkeys(sites) if s]

    if "feeds" in raw:
        feeds = []
        for feed in raw["feeds"] if isinstance(raw["feeds"], list) else []:
            if not isinstance(feed, dict):
                continue
            url = str(feed.get("url") or "").strip()[:1000]
            if not url:
                continue
            parts = urlparse(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"The feed link “{url[:60]}” must start with https://")
            if url not in {f["url"] for f in feeds}:
                feeds.append({"name": _text(feed.get("name"), 60) or parts.netloc, "url": url})
        out["feeds"] = feeds[:MAX_FEEDS]

    out["max_age_days"] = _number(raw.get("max_age_days"), 1, 30, base["max_age_days"])
    out["serpapi_daily_budget"] = _number(raw.get("serpapi_daily_budget"), 0, 2000,
                                          base["serpapi_daily_budget"])
    out["threads_enabled"] = bool(raw.get("threads_enabled", True))
    ai = raw.get("ai") if isinstance(raw.get("ai"), dict) else {}
    out["ai"] = {
        "enabled": bool(ai.get("enabled", True)),
        "drafts_per_scan": _number(ai.get("drafts_per_scan"), 0, 40, base["ai"]["drafts_per_scan"]),
        "min_relevance": _number(ai.get("min_relevance"), 0, 100, base["ai"]["min_relevance"]),
    }
    return out


def all_phrases(settings: dict) -> list[tuple[str, str]]:
    """(phrase, topic name) for every trigger phrase, in the order they are listed."""
    return [(phrase, topic["name"]) for topic in settings["topics"] for phrase in topic["phrases"]]
