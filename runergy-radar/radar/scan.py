"""One scan: ask every source for conversations, keep the new relevant ones, draft replies.

A scan is safe to run as often as you like: it never stores a conversation twice, it rations paid
searches, it stops before its time budget runs out, and whatever it could not finish is picked up
by the next scan.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Callable, Optional

from radar import ai, alerts, context, insights
from radar.defaults import all_phrases
from radar.sources import SourceError, bluesky, facebook, feeds, serpapi, threads
from radar.store import Store
from radar.text import Found, evaluate, now_utc, stamp

LOCK = "scan"
LOG_LENGTH = 30


class ScanBusy(Exception):
    pass


def _rotate(items: list, cursor: int, count: int) -> tuple[list, int]:
    """`count` items starting at `cursor`, wrapping round; and the next cursor."""
    if not items or count <= 0:
        return [], cursor
    count = min(count, len(items))
    start = cursor % len(items)
    picked = [items[(start + i) % len(items)] for i in range(count)]
    return picked, (start + count) % len(items)


def _health(ok: bool, now: datetime, found: int = 0, message: str = "", previous: Optional[dict] = None) -> dict:
    return {"ok": ok, "at": stamp(now), "found": found, "message": message,
            "last_ok": stamp(now) if ok else (previous or {}).get("last_ok", "")}


def serpapi_usage(store: Store, now: datetime) -> dict:
    usage = store.get_meta("serpapi_usage", {})
    today = now.strftime("%Y-%m-%d")
    return usage if usage.get("date") == today else {"date": today, "count": 0}


def threads_token(store: Store, now: datetime, notes: list[str]) -> str:
    """The Threads token to use: the one from the environment, kept fresh in the database."""
    base = threads.access_token()
    if not base:
        return ""
    mark = hashlib.sha256(base.encode()).hexdigest()[:16]
    saved = store.get_meta("threads_token", {})
    if saved.get("base") != mark:
        saved = {"base": mark, "token": base, "refreshed": stamp(now)}
        store.set_meta("threads_token", saved)
    refreshed = saved.get("refreshed", "")
    if refreshed < stamp(now - timedelta(days=7)):
        try:
            saved = {"base": mark, "token": threads.refresh(saved["token"]), "refreshed": stamp(now)}
        except SourceError as exc:
            saved["refreshed"] = stamp(now - timedelta(days=6))     # try again tomorrow
            notes.append(f"Couldn't renew the Threads token: {exc}")
        store.set_meta("threads_token", saved)
    return saved["token"]


def gather(store: Store, settings: dict, now: datetime, deadline: float) -> tuple[list[Found], dict, list[str]]:
    """Ask every source. Returns what was found, each source's health, and problems."""
    health_before = store.get_meta("sources", {})
    health, errors, found = {}, [], []
    phrases = [phrase for phrase, _ in all_phrases(settings)]
    jobs: list[tuple[str, Callable[[], list[Found]]]] = []

    # Google search through SerpApi (covers Reddit, Threads and forums).
    key = serpapi.api_key()
    usage = serpapi_usage(store, now)
    if key and phrases and settings["serpapi_daily_budget"] > 0:
        allowed = serpapi.allowance(settings["serpapi_daily_budget"], usage["count"], now)
        # Paid searches go where they pay off: phrases that keep finding nothing worth a reply
        # sit out every second round.
        weak = insights.weak_phrases(store, settings, now) if settings["smart_search"] else set()
        schedule = phrases + ([p for p in phrases if p.lower() not in weak] or phrases)
        picked, cursor = _rotate(schedule, int(store.get_meta("serpapi_cursor", 0)), allowed)
        if picked:
            # Counted before searching so a crash can never overspend the budget.
            store.set_meta("serpapi_usage", {"date": usage["date"], "count": usage["count"] + len(picked)})
            store.set_meta("serpapi_cursor", cursor)
            for phrase in picked:
                jobs.append(("search", lambda p=phrase: serpapi.search(
                    p, settings["sites"], settings["max_age_days"], key, now)))
        else:
            health["search"] = {**health_before.get("search", {}), "idle": True}

    # Threads keyword search.
    notes: list[str] = []
    if settings["threads_enabled"] and phrases:
        token = threads_token(store, now, notes)
        if token:
            picked, cursor = _rotate(phrases, int(store.get_meta("threads_cursor", 0)),
                                     threads.PER_SCAN_CAP)
            store.set_meta("threads_cursor", cursor)
            for phrase in picked:
                jobs.append(("threads", lambda p=phrase: threads.search(p, token, now)))

    # Bluesky search (free).
    if settings["bluesky_enabled"] and phrases:
        picked, cursor = _rotate(phrases, int(store.get_meta("bluesky_cursor", 0)), bluesky.PER_SCAN_CAP)
        store.set_meta("bluesky_cursor", cursor)
        try:
            session = bluesky.sign_in() if all(bluesky.credentials()) else ""
            for phrase in picked:
                jobs.append(("bluesky", lambda p=phrase: bluesky.search(p, session, now)))
        except SourceError as exc:
            jobs.append(("bluesky", lambda e=exc: (_ for _ in ()).throw(e)))

    # Comments on your own Facebook Page.
    page_id, page_token = facebook.page()
    if page_id and page_token:
        jobs.append(("facebook", lambda: facebook.comments(page_id, page_token, now)))

    # Feeds (Google Alerts and anything else added in Settings).
    for feed in settings["feeds"]:
        jobs.append((f"feed:{feed['url']}", lambda f=feed: feeds.read_feed(f, now)))

    results: dict[str, dict] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            futures = {pool.submit(job): name for name, job in jobs}
            try:
                for future in as_completed(futures, timeout=max(1.0, deadline - time.monotonic())):
                    entry = results.setdefault(futures[future], {"found": 0, "errors": []})
                    try:
                        items = future.result()
                        found.extend(items)
                        entry["found"] += len(items)
                        entry["ok"] = True
                    except SourceError as exc:
                        entry["errors"].append(str(exc))
                    except Exception as exc:        # a bug in one source must not stop the others
                        entry["errors"].append(f"Unexpected problem ({type(exc).__name__}).")
            except TimeoutError:
                for future, name in futures.items():
                    if not future.done():
                        future.cancel()
                        results.setdefault(name, {"found": 0, "errors": []})["errors"].append(
                            "Didn't answer in time; it will be tried again next scan.")
    for name, entry in results.items():
        message = entry["errors"][0] if entry["errors"] else ""
        ok = bool(entry.get("ok")) if not entry["errors"] else False
        health[name] = _health(ok, now, entry["found"], message, health_before.get(name))
        if message:
            errors.append(f"{label(name, settings)}: {message}")
    for note in notes:
        errors.append(note)
    # Keep the last known health of sources that didn't run this time (still configured ones only).
    wanted = {"search", "threads", "bluesky", "facebook"} | {f"feed:{feed['url']}" for feed in settings["feeds"]}
    for name, entry in health_before.items():
        if name in wanted and name not in health:
            health[name] = entry
    return found, health, errors


def label(name: str, settings: dict) -> str:
    if name == "search":
        return "Google search"
    if name == "threads":
        return "Threads"
    if name == "bluesky":
        return "Bluesky"
    if name == "facebook":
        return "Facebook Page"
    if name.startswith("feed:"):
        url = name[5:]
        return next((f["name"] for f in settings["feeds"] if f["url"] == url), "Feed")
    return name


def apply_assessment(store: Store, thread: dict, verdict: dict, settings: dict) -> dict:
    """Save what the AI said about one conversation."""
    changes = {"ai_score": verdict["relevance"], "intent": verdict["intent"],
               "ai_reason": verdict["reason"], "draft_error": ""}
    worth = verdict["should_reply"] and verdict["relevance"] >= settings["ai"]["min_relevance"]
    if worth:
        changes.update(draft=verdict["draft"], draft_state="ready")
    else:
        changes.update(draft_state="skip")
        if verdict["should_reply"]:
            changes["draft"] = verdict["draft"]
    updated = store.update_thread(thread["id"], **changes)
    current = store.get_thread(thread["id"])
    if not worth and current and current["status"] == "new":
        updated = store.set_status(thread["id"], "filtered")
    return updated


def needs_page(thread: dict, settings: dict) -> bool:
    """Search results and feeds only carry a snippet; posts from Threads/Bluesky/Facebook are whole."""
    return (settings["ai"]["read_page"] and not thread.get("context_state")
            and thread["platform"] in ("web", "reddit"))


def read_page(url: str, timeout: float = 8) -> str:
    return context.read_page(url, timeout=timeout)


def draft_waiting(store: Store, settings: dict, now: datetime, deadline: float,
                  assess: Callable = ai.assess) -> tuple[int, int, list[str]]:
    """Have the AI read conversations that are waiting. Returns (read, drafted, problems)."""
    limit = settings["ai"]["drafts_per_scan"]
    if not settings["ai"]["enabled"] or limit <= 0 or not ai.is_configured():
        return 0, 0, []
    waiting = store.waiting_for_ai(limit, stamp(now - timedelta(days=settings["max_age_days"] + 2)))
    if not waiting:
        return 0, 0, []

    examples = store.recent_replies(8) if settings["ai"]["learn"] else []

    def work(thread: dict):
        remaining = deadline - time.monotonic()
        if remaining < 12:
            return thread, None, None, None     # no time left: stays waiting for the next scan
        page = None
        if needs_page(thread, settings):
            page = read_page(thread["url"], timeout=min(8.0, remaining / 4))
        try:
            remaining = deadline - time.monotonic()
            verdict = assess(thread, settings, timeout=min(40.0, max(8.0, remaining - 3)),
                             examples=examples, page_text=page or thread.get("context") or "")
            return thread, verdict, None, page
        except ai.AIError as exc:
            return thread, None, str(exc), page
        except Exception as exc:
            return thread, None, f"Unexpected problem ({type(exc).__name__}).", page

    read = drafted = 0
    problems: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for thread, verdict, problem, page in pool.map(work, waiting):
            if page is not None:
                store.update_thread(thread["id"], context=page, context_state="read" if page else "none")
            if verdict is not None:
                read += 1
                updated = apply_assessment(store, thread, verdict, settings)
                drafted += 1 if updated and updated["draft_state"] == "ready" else 0
            elif problem:
                store.update_thread(thread["id"], draft_state="error", draft_error=problem[:300])
                if problem not in problems:
                    problems.append(problem)
    return read, drafted, [f"AI drafts: {p}" for p in problems[:3]]


def run_scan(store: Store, time_budget: float = 50.0, now: Optional[datetime] = None,
             assess: Callable = ai.assess) -> dict:
    """Run one scan and return its summary (also saved for the dashboard)."""
    started = time.monotonic()
    deadline = started + time_budget
    now = now or now_utc()
    if not store.try_lock(LOCK, int(time_budget) + 30):
        raise ScanBusy("A scan is already running. Give it a minute.")
    try:
        settings = store.settings()
        found, health, errors = gather(store, settings, now, started + time_budget * 0.45)
        store.set_meta("sources", health)

        threads_found = [t for t in (evaluate(item, settings, now) for item in found) if t]
        use_ai = settings["ai"]["enabled"] and ai.is_configured()
        for thread in threads_found:
            thread["draft_state"] = "pending" if use_ai else "none"
        added = store.add_threads(threads_found)

        read, drafted, ai_errors = draft_waiting(store, settings, now, deadline, assess)
        try:
            alerted, alert_errors = alerts.notify(store, settings, now, use_ai)
        except Exception as exc:             # an alert problem must never fail the scan
            alerted, alert_errors = 0, [f"Alerts: unexpected problem ({type(exc).__name__})."]
        summary = {
            "at": stamp(now),
            "seconds": round(time.monotonic() - started, 1),
            "seen": len(found),
            "new": len(added),
            "read_by_ai": read,
            "drafted": drafted,
            "alerted": alerted,
            "errors": (errors + ai_errors + alert_errors)[:10],
        }
        store.set_meta("last_scan", summary)
        store.set_meta("scan_log", ([summary] + store.get_meta("scan_log", []))[:LOG_LENGTH])
        return summary
    finally:
        store.unlock(LOCK)
