"""What the replies are producing: which platforms, communities, topics and trigger phrases find
good conversations and bring clicks."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from radar.defaults import all_phrases
from radar.text import stamp

WEAK_AFTER = 10         # a phrase needs this many finds before it can be judged
WEAK_SHARE = 0.1        # ...and fewer than this share worth replying to


def worthwhile(thread: dict, minimum: int) -> bool:
    if thread["status"] in ("replied", "saved"):
        return True
    if thread["status"] == "filtered":
        return False
    return thread["ai_score"] is not None and thread["ai_score"] >= minimum


def phrase_stats(threads: list[dict], settings: dict, clicks: dict | None = None) -> dict:
    """{phrase (lower-case): {'found', 'good', 'replied', 'clicks', 'weak'}}"""
    minimum, clicks = settings["ai"]["min_relevance"], clicks or {}
    stats: dict = defaultdict(lambda: {"found": 0, "good": 0, "replied": 0, "clicks": 0, "judged": 0})
    for thread in threads:
        judged = thread["ai_score"] is not None or thread["status"] in ("replied", "saved", "dismissed", "filtered")
        for phrase in thread["matched"]:
            row = stats[phrase.lower()]
            row["found"] += 1
            row["judged"] += 1 if judged else 0
            row["good"] += 1 if worthwhile(thread, minimum) else 0
            row["replied"] += 1 if thread["status"] == "replied" else 0
            row["clicks"] += clicks.get(thread["id"], 0)
    for row in stats.values():
        row["weak"] = row["judged"] >= WEAK_AFTER and row["good"] / row["judged"] < WEAK_SHARE
    return dict(stats)


def weak_phrases(store, settings: dict, now: datetime) -> set[str]:
    threads = store.threads_since(stamp(now - timedelta(days=60)))
    return {phrase for phrase, row in phrase_stats(threads, settings).items() if row["weak"]}


def _table(rows: dict, limit: int = 12) -> list[dict]:
    listed = [{"name": name, **row} for name, row in rows.items()]
    listed.sort(key=lambda r: (-r["clicks"], -r["replied"], -r["good"], -r["found"], r["name"]))
    return listed[:limit]


def build(store, settings: dict, now: datetime, days: int = 30) -> dict:
    since = now - timedelta(days=days)
    threads = store.threads_since(stamp(since))
    clicks = store.clicks_since(since.strftime("%Y-%m-%d"))
    minimum = settings["ai"]["min_relevance"]

    def blank():
        return {"found": 0, "good": 0, "replied": 0, "clicks": 0}
    platforms, communities, topics = defaultdict(blank), defaultdict(blank), defaultdict(blank)
    totals = blank()
    for thread in threads:
        good, replied = worthwhile(thread, minimum), thread["status"] == "replied"
        got = clicks.get(thread["id"], 0)
        groups = [platforms[thread["platform"]], communities[thread["community"] or "unknown"], totals]
        groups += [topics[name] for name in thread["topics"]]
        for row in groups:
            row["found"] += 1
            row["good"] += 1 if good else 0
            row["replied"] += 1 if replied else 0
            row["clicks"] += got
    stats = phrase_stats(threads, settings, clicks)
    phrases = {phrase: stats.get(phrase.lower(), {**blank(), "weak": False})
               for phrase, _ in all_phrases(settings)}
    best = sorted((t for t in threads if t["status"] == "replied"),
                  key=lambda t: (-clicks.get(t["id"], 0), t["status_at"]), reverse=False)[:8]
    return {
        "days": days,
        "counted": bool(settings["link"]["via_radar"]),
        "totals": totals,
        "platforms": _table(platforms),
        "communities": _table(communities, 10),
        "topics": _table(topics),
        "phrases": _table({p: {k: row[k] for k in ("found", "good", "replied", "clicks", "weak")}
                           for p, row in phrases.items()}, 60),
        "replies": [{"id": t["id"], "title": t["title"], "community": t["community"], "url": t["url"],
                     "clicks": clicks.get(t["id"], 0), "at": t["status_at"]} for t in best],
    }
