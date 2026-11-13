"""Storage: SQLite on your own computer, Postgres (DATABASE_URL) when hosted.

Everything goes through `Store`, which speaks the small common subset of SQL both understand.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from radar.defaults import clean_settings
from radar.text import now_utc, stamp

STATUSES = ("new", "saved", "replied", "dismissed", "filtered")
DRAFT_STATES = ("none", "pending", "ready", "skip", "error")

_COLUMNS = ("id", "platform", "community", "title", "body", "url", "author", "created_at",
            "found_at", "source", "matched", "topics", "kw_score", "ai_score", "intent",
            "ai_reason", "draft", "draft_state", "draft_error", "status", "status_at", "reply_text")
_JSON_COLUMNS = ("matched", "topics")
_EDITABLE = {"ai_score", "intent", "ai_reason", "draft", "draft_state", "draft_error", "status",
             "status_at", "reply_text"}

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS radar_threads (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        community TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        found_at TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        matched TEXT NOT NULL DEFAULT '[]',
        topics TEXT NOT NULL DEFAULT '[]',
        kw_score INTEGER NOT NULL DEFAULT 0,
        ai_score INTEGER,
        intent TEXT NOT NULL DEFAULT '',
        ai_reason TEXT NOT NULL DEFAULT '',
        draft TEXT NOT NULL DEFAULT '',
        draft_state TEXT NOT NULL DEFAULT 'none',
        draft_error TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new',
        status_at TEXT NOT NULL DEFAULT '',
        reply_text TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS radar_threads_status ON radar_threads (status, found_at)",
    "CREATE TABLE IF NOT EXISTS radar_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS radar_locks (name TEXT PRIMARY KEY, until_ts BIGINT NOT NULL)",
)


class StoreError(Exception):
    pass


def database_url() -> str:
    for name in ("RADAR_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def sqlite_path() -> str:
    explicit = (os.environ.get("RADAR_DB") or "").strip()
    if explicit:
        return explicit
    if os.environ.get("VERCEL"):
        return "/tmp/radar.db"      # only place a hosted function can write; it does not last
    return str(Path(__file__).resolve().parent.parent / "data" / "radar.db")


def is_persistent() -> bool:
    """False when hosted without a database: anything saved would vanish."""
    return bool(database_url()) or not os.environ.get("VERCEL")


class Store:
    def __init__(self, url: Optional[str] = None, path: Optional[str] = None):
        self.url = database_url() if url is None and path is None else (url or "")
        self.path = path or sqlite_path()
        self.postgres = bool(self.url)
        self._lock = threading.RLock()
        self._conn = None
        self._ready = False

    # -- connection --------------------------------------------------------------------------
    def _connect(self):
        if self.postgres:
            try:
                import psycopg
            except ImportError as exc:      # pragma: no cover
                raise StoreError("The Postgres driver is missing: pip install 'psycopg[binary]'") from exc
            try:
                return psycopg.connect(self.url, autocommit=True, connect_timeout=10,
                                       prepare_threshold=None)
            except psycopg.Error as exc:
                raise StoreError(f"Couldn't connect to the database: {str(exc).strip()[:200]}") from exc
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _run(self, sql: str, params: Iterable = (), fetch: bool = False):
        """Run one statement; returns rows (fetch=True) or the number of rows changed."""
        params = tuple(params)
        if self.postgres:
            sql = sql.replace("?", "%s")
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = self._connect()
                    cursor = self._conn.execute(sql, params)
                    return cursor.fetchall() if fetch else cursor.rowcount
                except StoreError:
                    raise
                except Exception as exc:     # a dropped connection: reconnect once
                    dropped = type(exc).__name__ in ("OperationalError", "InterfaceError")
                    try:
                        if self._conn is not None:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                    if attempt == 2 or not dropped:
                        raise StoreError(f"Database error: {str(exc).strip()[:200]}") from exc
        return None

    def ready(self) -> "Store":
        if not self._ready:
            with self._lock:
                for statement in _SCHEMA:
                    self._run(statement)
                self._ready = True
        return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- small values (settings, source health, counters) -------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        rows = self.ready()._run("SELECT value FROM radar_meta WHERE key = ?", (key,), fetch=True)
        if not rows:
            return default
        try:
            return json.loads(rows[0][0])
        except ValueError:
            return default

    def set_meta(self, key: str, value: Any) -> None:
        self.ready()._run(
            "INSERT INTO radar_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)))

    def settings(self) -> dict:
        return clean_settings(self.get_meta("settings"))

    def save_settings(self, raw) -> dict:
        cleaned = clean_settings(raw)
        self.set_meta("settings", cleaned)
        return cleaned

    # -- one scan at a time --------------------------------------------------------------------
    def try_lock(self, name: str, seconds: int) -> bool:
        now = int(time.time())
        changed = self.ready()._run(
            "INSERT INTO radar_locks (name, until_ts) VALUES (?, ?) "
            "ON CONFLICT (name) DO UPDATE SET until_ts = excluded.until_ts "
            "WHERE radar_locks.until_ts < ?",
            (name, now + seconds, now))
        return changed == 1

    def unlock(self, name: str) -> None:
        self.ready()._run("DELETE FROM radar_locks WHERE name = ?", (name,))

    # -- conversations ---------------------------------------------------------------------------
    @staticmethod
    def _thread(row) -> dict:
        item = dict(zip(_COLUMNS, row))
        for key in _JSON_COLUMNS:
            try:
                item[key] = json.loads(item[key] or "[]")
            except ValueError:
                item[key] = []
        item["score"] = item["ai_score"] if item["ai_score"] is not None else item["kw_score"]
        return item

    def add_threads(self, threads: list[dict]) -> list[dict]:
        """Store the conversations not seen before; returns the ones that were new."""
        self.ready()
        unique = list({t["id"]: t for t in threads}.values())
        added = []
        for start in range(0, len(unique), 200):
            chunk = unique[start:start + 200]
            marks = ",".join("?" * len(chunk))
            known = {row[0] for row in self._run(
                f"SELECT id FROM radar_threads WHERE id IN ({marks})", [t["id"] for t in chunk],
                fetch=True)}
            for thread in chunk:
                if thread["id"] in known:
                    continue
                row = {"draft_state": "none", "status": "new", **thread}
                row["matched"] = json.dumps(row.get("matched", []))
                row["topics"] = json.dumps(row.get("topics", []))
                names = [c for c in _COLUMNS if c in row]
                changed = self._run(
                    f"INSERT INTO radar_threads ({', '.join(names)}) "
                    f"VALUES ({', '.join('?' * len(names))}) ON CONFLICT (id) DO NOTHING",
                    [row[n] for n in names])
                if changed == 1:
                    added.append(thread)
        return added

    def get_thread(self, thread_id: str) -> Optional[dict]:
        rows = self.ready()._run(
            f"SELECT {', '.join(_COLUMNS)} FROM radar_threads WHERE id = ?", (thread_id,), fetch=True)
        return self._thread(rows[0]) if rows else None

    def update_thread(self, thread_id: str, **changes) -> Optional[dict]:
        unknown = set(changes) - _EDITABLE
        if unknown:
            raise StoreError(f"Can't change {', '.join(sorted(unknown))}")
        if changes.get("status") is not None and changes["status"] not in STATUSES:
            raise StoreError("Unknown status")
        if changes.get("draft_state") is not None and changes["draft_state"] not in DRAFT_STATES:
            raise StoreError("Unknown draft state")
        if changes:
            names = list(changes)
            self.ready()._run(
                f"UPDATE radar_threads SET {', '.join(f'{n} = ?' for n in names)} WHERE id = ?",
                [changes[n] for n in names] + [thread_id])
        return self.get_thread(thread_id)

    def set_status(self, thread_id: str, status: str, reply_text: Optional[str] = None) -> Optional[dict]:
        changes = {"status": status, "status_at": stamp(now_utc())}
        if reply_text is not None:
            changes["reply_text"] = reply_text
        return self.update_thread(thread_id, **changes)

    def list_threads(self, status: str = "new", platform: str = "", topic: str = "", query: str = "",
                     sort: str = "best", limit: int = 30, offset: int = 0) -> tuple[list[dict], int]:
        where, params = ["status = ?"], [status]
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if topic:
            where.append("topics LIKE ? ESCAPE '!'")
            params.append("%" + _like(json.dumps(topic)) + "%")
        for word in query.split()[:6]:
            where.append("(LOWER(title) LIKE ? ESCAPE '!' OR LOWER(body) LIKE ? ESCAPE '!' "
                         "OR LOWER(community) LIKE ? ESCAPE '!')")
            params += ["%" + _like(word.lower()) + "%"] * 3
        order = {
            "best": "COALESCE(ai_score, kw_score) DESC, found_at DESC, id",
            "newest": "CASE WHEN created_at = '' THEN found_at ELSE created_at END DESC, id",
            "recent": "status_at DESC, found_at DESC, id",
        }.get(sort, "COALESCE(ai_score, kw_score) DESC, found_at DESC, id")
        clause = " AND ".join(where)
        self.ready()
        total = self._run(f"SELECT COUNT(*) FROM radar_threads WHERE {clause}", params, fetch=True)[0][0]
        rows = self._run(
            f"SELECT {', '.join(_COLUMNS)} FROM radar_threads WHERE {clause} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            params + [max(1, min(100, limit)), max(0, offset)], fetch=True)
        return [self._thread(row) for row in rows], int(total)

    def counts(self) -> dict:
        rows = self.ready()._run(
            "SELECT status, COUNT(*) FROM radar_threads GROUP BY status", fetch=True)
        out = {status: 0 for status in STATUSES}
        out.update({status: int(count) for status, count in rows if status in out})
        return out

    def platform_counts(self, status: str) -> dict:
        rows = self.ready()._run(
            "SELECT platform, COUNT(*) FROM radar_threads WHERE status = ? GROUP BY platform",
            (status,), fetch=True)
        return {platform: int(count) for platform, count in rows}

    def replied_since(self, since: str) -> int:
        rows = self.ready()._run(
            "SELECT COUNT(*) FROM radar_threads WHERE status = 'replied' AND status_at >= ?",
            (since,), fetch=True)
        return int(rows[0][0])

    def waiting_for_ai(self, limit: int, since: str) -> list[dict]:
        """New conversations that have not been read by the AI yet, best keyword match first."""
        rows = self.ready()._run(
            f"SELECT {', '.join(_COLUMNS)} FROM radar_threads "
            "WHERE status = 'new' AND draft_state IN ('pending', 'none') AND found_at >= ? "
            "ORDER BY kw_score DESC, found_at DESC, id LIMIT ?", (since, limit), fetch=True)
        return [self._thread(row) for row in rows]

    def drafts_containing(self, text: str) -> list[dict]:
        """Replies not posted yet whose text contains `text`."""
        rows = self.ready()._run(
            f"SELECT {', '.join(_COLUMNS)} FROM radar_threads "
            "WHERE status IN ('new', 'saved', 'filtered', 'dismissed') AND LOWER(draft) LIKE ? ESCAPE '!'",
            ("%" + _like(text.lower()) + "%",), fetch=True)
        return [self._thread(row) for row in rows]

    def clear_threads(self) -> None:
        self.ready()._run("DELETE FROM radar_threads")


def _like(text: str) -> str:
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


_shared: Optional[Store] = None
_shared_lock = threading.Lock()


def shared_store() -> Store:
    """One store per process (hosted functions reuse it between requests)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Store()
        return _shared


def reset_shared_store() -> None:
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
        _shared = None
