"""Default settings and the cleaning applied to anything saved from the Settings page.

Settings live in the database (so the dashboard can edit them); these are the starting values.
"""

from __future__ import annotations

import copy
from urllib.parse import urlparse

DEFAULT_SETTINGS: dict = {
    "brand": {
        "name": "Runergy Lab",
        "url": "https://runergylab.com",
        "description": (
            "Runergy Lab is an AI-powered performance and training intelligence platform for "
            "runners. It turns the training data runners already collect on their watch into "
            "clear insights and personalized training guidance, so they know what to do next "
            "instead of guessing. It connects to Garmin (more wearables are being explored). "
            "It is for recreational, intermediate and advanced runners preparing for anything "
            "from a 5K to a marathon, and focuses on individual progression, consistency and "
            "smarter training decisions. Tagline: Stop guessing. Start running smarter."
        ),
        "voice": (
            "A friendly, experienced runner talking to another runner. Practical and specific, "
            "never salesy. Short paragraphs, plain words, no hashtags, no emojis."
        ),
        "disclosure": "Full disclosure: I'm part of the team building Runergy Lab.",
    },
    # Where replies send people. "when_mentioned": added whenever a reply mentions the app.
    # via_radar: links go through this dashboard (/go/...), which counts clicks and keeps old
    # replies pointing at whatever the link is today. base: the address people see for those links.
    "link": {"mode": "when_mentioned", "track": False, "via_radar": False, "base": ""},
    "topics": [
        {"name": "App recommendations", "phrases": [
            "best running app", "running app recommendation", "which running app",
            "running app for beginners", "alternative to Runna", "app for marathon training"]},
        {"name": "Training plans", "phrases": [
            "marathon training plan", "half marathon training plan", "5k training plan",
            "10k training plan", "couch to 5k", "personalized training plan"]},
        {"name": "Training data & wearables", "phrases": [
            "understand my Garmin data", "Garmin training status", "training load",
            "training readiness", "what do my running stats mean", "Garmin Coach alternative",
            "too much running data"]},
        {"name": "AI coaching", "phrases": [
            "AI running coach", "AI training plan", "adaptive training plan",
            "am I overtraining", "how to train smarter"]},
        {"name": "Brand mentions", "phrases": ["Runergy Lab", "Runergy"]},
    ],
    "context_words": [
        "run", "runs", "running", "runner", "runners", "marathon", "half marathon", "5k", "10k",
        "jog", "jogging", "race", "ultra", "trail", "parkrun", "long run", "Garmin", "pace",
        "VO2 max"],
    "exclude_words": ["hiring", "job opening", "coupon code", "giveaway", "for sale"],
    "sites": ["reddit.com", "threads.com", "letsrun.com", "quora.com", "runnersworld.com",
              "slowtwitch.com"],
    "feeds": [],
    "max_age_days": 3,
    "serpapi_daily_budget": 30,
    "threads_enabled": True,
    "bluesky_enabled": True,
    "smart_search": True,          # search phrases that never produce good matches half as often
    "alerts": {"enabled": True, "min_score": 85},
    "followup_hours": 24,          # after replying, remind to check back (0 = never)
    "community_weekly_limit": 3,   # warn after this many replies in one community in 7 days (0 = off)
    "communities": [],             # [{"name": "r/running", "notes": "...", "no_links": False}]
    "ai": {"enabled": True, "drafts_per_scan": 12, "min_relevance": 50, "learn": True,
           "read_page": True},
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
    url = _text(brand.get("url", base["brand"]["url"]), 300)
    if url and "://" not in url and "." in url and " " not in url:
        url = "https://" + url                     # "runergylab.com" is what people type
    out["brand"] = {
        "name": _text(brand.get("name", base["brand"]["name"]), 80) or base["brand"]["name"],
        "url": url,
        "description": _long_text(brand.get("description", base["brand"]["description"]), 3000),
        "voice": _long_text(brand.get("voice", base["brand"]["voice"]), 1500),
        "disclosure": _long_text(brand.get("disclosure", base["brand"]["disclosure"]), 300),
    }
    if out["brand"]["url"]:
        parts = urlparse(out["brand"]["url"])
        if parts.scheme not in ("http", "https") or "." not in parts.netloc or " " in out["brand"]["url"]:
            raise ValueError("The link for replies must be a web address, like https://runergylab.com")
    link = raw.get("link") if isinstance(raw.get("link"), dict) else {}
    out["link"] = {
        "mode": link.get("mode") if link.get("mode") in ("when_mentioned", "always", "never")
        else base["link"]["mode"],
        "track": bool(link.get("track", False)),
        "via_radar": bool(link.get("via_radar", False)),
        "base": _text(link.get("base", ""), 200).rstrip("/"),
    }
    if out["link"]["base"]:
        if "://" not in out["link"]["base"]:
            out["link"]["base"] = "https://" + out["link"]["base"]
        parts = urlparse(out["link"]["base"])
        if parts.scheme not in ("http", "https") or "." not in parts.netloc or parts.path.strip("/"):
            raise ValueError("The address for counted links must be a plain web address, "
                             "like https://go.runergylab.com")

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
    out["bluesky_enabled"] = bool(raw.get("bluesky_enabled", True))
    out["smart_search"] = bool(raw.get("smart_search", True))
    alerts = raw.get("alerts") if isinstance(raw.get("alerts"), dict) else {}
    out["alerts"] = {"enabled": bool(alerts.get("enabled", True)),
                     "min_score": _number(alerts.get("min_score"), 1, 100, base["alerts"]["min_score"])}
    out["followup_hours"] = _number(raw.get("followup_hours"), 0, 336, base["followup_hours"])
    out["community_weekly_limit"] = _number(raw.get("community_weekly_limit"), 0, 50,
                                            base["community_weekly_limit"])
    if "communities" in raw:
        communities = []
        for item in raw["communities"] if isinstance(raw["communities"], list) else []:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"), 80)
            if not name:
                continue
            if name.lower() in {c["name"].lower() for c in communities}:
                raise ValueError(f"“{name}” is listed twice under Communities.")
            communities.append({"name": name, "notes": _long_text(item.get("notes"), 600),
                                "no_links": bool(item.get("no_links", False))})
        out["communities"] = communities[:60]
    ai = raw.get("ai") if isinstance(raw.get("ai"), dict) else {}
    out["ai"] = {
        "enabled": bool(ai.get("enabled", True)),
        "drafts_per_scan": _number(ai.get("drafts_per_scan"), 0, 40, base["ai"]["drafts_per_scan"]),
        "min_relevance": _number(ai.get("min_relevance"), 0, 100, base["ai"]["min_relevance"]),
        "learn": bool(ai.get("learn", True)),
        "read_page": bool(ai.get("read_page", True)),
    }
    return out


def community_rules(settings: dict, community: str) -> dict:
    """Your notes for one community ({} when there are none)."""
    wanted = (community or "").strip().lower()
    return next((c for c in settings["communities"] if c["name"].lower() == wanted), {})


def all_phrases(settings: dict) -> list[tuple[str, str]]:
    """(phrase, topic name) for every trigger phrase, in the order they are listed."""
    return [(phrase, topic["name"]) for topic in settings["topics"] for phrase in topic["phrases"]]
