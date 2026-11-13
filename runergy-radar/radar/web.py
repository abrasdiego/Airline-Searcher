"""The dashboard: one page plus a small JSON API.

Run locally:  python -m radar serve      (http://localhost:8010)
On Vercel the same Handler is served by api/index.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from radar import __version__, ai
from radar.scan import ScanBusy, run_scan, serpapi_usage
from radar.sources import serpapi, threads
from radar.store import STATUSES, StoreError, is_persistent, shared_store
from radar.text import now_utc, stamp

STATIC = Path(__file__).resolve().parent / "static"
PAGE = STATIC / "index.html"
IMAGES = ("/logo-64.png", "/logo-192.png")
COOKIE = "radar_session"
SESSION_DAYS = 30
MAX_BODY = 200_000
LOGIN_WINDOW = 600
LOGIN_ATTEMPTS = 10
_THREAD_PATH = re.compile(r"^/api/threads/([0-9a-f]{20})(/draft)?$")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# -- sign-in -------------------------------------------------------------------------------------
def password() -> str:
    return (os.environ.get("RADAR_PASSWORD") or "").strip()


def cron_secret() -> str:
    return (os.environ.get("CRON_SECRET") or os.environ.get("RADAR_CRON_SECRET") or "").strip()


def hosted() -> bool:
    return bool(os.environ.get("VERCEL"))


def _sign(expires: int) -> str:
    key = hashlib.sha256(("radar-session|" + password()).encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def make_session(now: Optional[float] = None) -> str:
    expires = int((now or time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(expires)}"


def valid_session(value: str, now: Optional[float] = None) -> bool:
    if not password() or not value or "." not in value:
        return False
    expires, _, signature = value.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    return hmac.compare_digest(signature, _sign(int(expires)))


def check_password(store, attempt: str, now: Optional[float] = None) -> bool:
    """True if the password is right. Too many wrong tries pause sign-in for a few minutes."""
    now = now or time.time()
    fails = store.get_meta("login_fails", {"since": 0, "count": 0})
    if now - fails.get("since", 0) > LOGIN_WINDOW:
        fails = {"since": now, "count": 0}
    if fails["count"] >= LOGIN_ATTEMPTS:
        raise ApiError(429, "Too many wrong passwords. Wait ten minutes and try again.")
    if password() and hmac.compare_digest(attempt.encode(), password().encode()):
        return True
    fails["count"] += 1
    store.set_meta("login_fails", fails)
    return False


# -- what the page shows ---------------------------------------------------------------------------
def setup_status() -> list[dict]:
    """The checklist on the dashboard: what is connected and what still needs doing."""
    return [
        {"key": "database", "ok": is_persistent(), "name": "Database",
         "help": "Connect a Postgres database (DATABASE_URL). Without it nothing is kept between visits."},
        {"key": "ai", "ok": ai.is_configured(), "name": "AI reply drafts",
         "help": "Add ANTHROPIC_API_KEY so each conversation gets a relevance check and a draft reply."},
        {"key": "search", "ok": bool(serpapi.api_key()), "name": "Google search (Reddit, Threads, forums)",
         "help": "Add SERPAPI_API_KEY to find new threads on Reddit, Threads and forums."},
        {"key": "cron", "ok": bool(cron_secret()) or not hosted(), "name": "Automatic scans",
         "help": "Add CRON_SECRET (and the same value in GitHub) so scans run by themselves."},
    ]


def sources_view(store, settings: dict) -> list[dict]:
    health = store.get_meta("sources", {})
    now = now_utc()
    usage = serpapi_usage(store, now)
    rows = []

    def row(key: str, name: str, kind: str, configured: bool, hint: str, detail: str = "") -> None:
        entry = health.get(key, {})
        if not configured:
            state = "off"
        elif not entry.get("at"):
            state = "waiting"
        else:
            state = "ok" if entry.get("ok") else "error"
        rows.append({"key": key, "name": name, "kind": kind, "state": state, "hint": hint,
                     "detail": detail, "at": entry.get("at", ""), "last_ok": entry.get("last_ok", ""),
                     "found": entry.get("found", 0), "message": entry.get("message", "")})

    budget = settings["serpapi_daily_budget"]
    row("search", "Google search", "Reddit, Threads, forums",
        bool(serpapi.api_key()) and budget > 0,
        "Needs SERPAPI_API_KEY and a daily search budget above 0.",
        f"{usage['count']} of {budget} searches used today")
    row("threads", "Threads search", "Threads",
        bool(threads.access_token()) and settings["threads_enabled"],
        "Optional. Needs THREADS_ACCESS_TOKEN from a Meta app approved for keyword search.")
    for feed in settings["feeds"]:
        row(f"feed:{feed['url']}", feed["name"], "Feed", True, "")
    if not settings["feeds"]:
        rows.append({"key": "feeds", "name": "Google Alerts & feeds", "kind": "Feed", "state": "off",
                     "hint": "Free. Add Google Alerts feeds in Settings.", "detail": "", "at": "",
                     "last_ok": "", "found": 0, "message": ""})
    return rows


def state_view(store) -> dict:
    settings = store.settings()
    week_ago = stamp(now_utc() - timedelta(days=7))
    return {
        "version": __version__,
        "brand": settings["brand"]["name"],
        "counts": store.counts(),
        "replied_this_week": store.replied_since(week_ago),
        "last_scan": store.get_meta("last_scan"),
        "scan_log": store.get_meta("scan_log", [])[:12],
        "setup": setup_status(),
        "sources": sources_view(store, settings),
        "topics": [topic["name"] for topic in settings["topics"]],
        "ai": ai.is_configured() and settings["ai"]["enabled"],
        "password_set": bool(password()),
    }


# -- API -----------------------------------------------------------------------------------------
def list_threads_api(store, query: dict) -> dict:
    status = query.get("status", "new")
    if status not in STATUSES:
        raise ApiError(400, "Unknown tab.")
    try:
        offset = max(0, int(query.get("offset", "0")))
    except ValueError:
        offset = 0
    default_sort = "best" if status in ("new", "saved", "filtered") else "recent"
    items, total = store.list_threads(
        status=status, platform=query.get("platform", ""), topic=query.get("topic", ""),
        query=query.get("q", "")[:100], sort=query.get("sort") or default_sort,
        limit=30, offset=offset)
    return {"threads": items, "total": total, "offset": offset,
            "platforms": store.platform_counts(status)}


def update_thread_api(store, thread_id: str, body: dict) -> dict:
    thread = store.get_thread(thread_id)
    if not thread:
        raise ApiError(404, "That conversation is no longer here.")
    if "draft" in body:
        draft = str(body.get("draft") or "")[:6000]
        thread = store.update_thread(
            thread_id, draft=draft,
            draft_state="ready" if draft.strip() else ("none" if thread["draft_state"] == "ready"
                                                       else thread["draft_state"]))
    if "status" in body:
        status = body.get("status")
        if status not in STATUSES:
            raise ApiError(400, "Unknown status.")
        reply = None
        if status == "replied":
            reply = str(body.get("reply_text") if "reply_text" in body else thread["draft"])[:6000]
        thread = store.set_status(thread_id, status, reply)
    return {"thread": thread, "counts": store.counts()}


def draft_api(store, thread_id: str, body: dict) -> dict:
    thread = store.get_thread(thread_id)
    if not thread:
        raise ApiError(404, "That conversation is no longer here.")
    settings = store.settings()
    if not ai.is_configured():
        raise ApiError(400, "AI drafts need ANTHROPIC_API_KEY. Add it, then try again.")
    if "draft" in body:
        thread["draft"] = str(body.get("draft") or "")[:6000]
    instruction = " ".join(str(body.get("instruction") or "").split())[:500]
    try:
        verdict = ai.assess(thread, settings, instruction=instruction, timeout=45)
    except ai.AIError as exc:
        store.update_thread(thread_id, draft_error=str(exc)[:300],
                            draft_state="error" if not thread["draft"].strip() else thread["draft_state"])
        raise ApiError(502, str(exc)) from exc
    if not verdict["draft"]:
        store.update_thread(thread_id, ai_score=verdict["relevance"], intent=verdict["intent"],
                            ai_reason=verdict["reason"], draft_error="")
        raise ApiError(409, "The AI recommends not replying here: "
                       + (verdict["reason"] or "it isn't a good fit."))
    updated = store.update_thread(
        thread_id, ai_score=verdict["relevance"], intent=verdict["intent"],
        ai_reason=verdict["reason"], draft=verdict["draft"], draft_state="ready", draft_error="")
    return {"thread": updated}


def scan_api(store) -> dict:
    budget = float(os.environ.get("RADAR_SCAN_SECONDS") or 50)
    try:
        return {"scan": run_scan(store, time_budget=budget), "counts": store.counts()}
    except ScanBusy as exc:
        raise ApiError(409, str(exc)) from exc


class Handler(BaseHTTPRequestHandler):
    server_version = "RunergyRadar"

    # -- plumbing --
    def _send(self, status: int, body: bytes, content_type: str, headers: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _cookie(self) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    def _secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip() == "https"

    def _set_cookie(self, value: str, max_age: int) -> dict:
        flags = "; Secure" if self._secure() else ""
        return {"Set-Cookie": f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{flags}"}

    def _signed_in(self) -> bool:
        if not password():
            return not hosted()             # no password: fine on your own computer, never online
        return valid_session(self._cookie())

    def _cron(self) -> bool:
        secret = cron_secret()
        given = self.headers.get("Authorization") or ""
        return bool(secret) and hmac.compare_digest(given.encode(), f"Bearer {secret}".encode())

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            raise ApiError(413, "That is too much text.")
        if not length:
            return {}
        if "json" not in (self.headers.get("Content-Type") or "").lower():
            raise ApiError(415, "Send JSON.")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(400, "That request couldn't be read.") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "That request couldn't be read.")
        return data

    def _require_sign_in(self) -> None:
        if self._signed_in():
            return
        if not password():
            raise ApiError(503, "Set RADAR_PASSWORD in the project's environment variables, then redeploy.")
        raise ApiError(401, "Please sign in.")

    # -- routes --
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self._route("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        # On Vercel every URL is rewritten to this function; the rewrite passes the page asked
        # for as __path in case the host hands us the function's own path instead.
        asked = query.pop("__path", "") or url.path
        if asked.rstrip("/") == "/api/index":
            asked = "/"
        path = "/" + asked.strip("/")
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "Not allowed.")
                if path in ("/", "/index.html"):
                    return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8", {
                        "Content-Security-Policy":
                            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                            "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"})
                if path in IMAGES:
                    return self._send(200, (STATIC / path[1:]).read_bytes(), "image/png",
                                      {"Cache-Control": "public, max-age=86400"})
                raise ApiError(404, "Not found.")
            self._json(200, self._api(method, path, query))
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message, "password_set": bool(password())})
        except ValueError as exc:            # settings the person should fix
            self._json(400, {"error": str(exc)})
        except StoreError as exc:
            self._json(503, {"error": str(exc)})
        except Exception as exc:             # never show a stack trace to the browser
            self.log_error("Unexpected error on %s: %r", path, exc)
            self._json(500, {"error": "Something went wrong on the server. Please try again."})

    def _api(self, method: str, path: str, query: dict) -> dict:
        if path == "/api/health":
            return {"ok": True, "version": __version__}
        store = shared_store()

        if path == "/api/scan":
            # GET is for Vercel's cron, which can only send its secret; people use POST.
            if not self._cron():
                if method != "POST":
                    raise ApiError(401, "Scans by link need the cron secret.")
                self._require_sign_in()
            return scan_api(store)

        if path == "/api/login" and method == "POST":
            if not password():
                self._require_sign_in()
                return {"ok": True}
            if not check_password(store, str(self._body().get("password") or "")):
                raise ApiError(401, "That password isn't right.")
            self._session_headers = self._set_cookie(make_session(), SESSION_DAYS * 86400)
            return {"ok": True}
        if path == "/api/logout" and method == "POST":
            self._session_headers = self._set_cookie("", 0)
            return {"ok": True}

        self._require_sign_in()
        if path == "/api/state" and method == "GET":
            return state_view(store)
        if path == "/api/threads" and method == "GET":
            return list_threads_api(store, query)
        if path == "/api/settings":
            if method == "GET":
                return {"settings": store.settings()}
            saved = store.save_settings(self._body().get("settings"))
            return {"settings": saved, "topics": [t["name"] for t in saved["topics"]]}
        match = _THREAD_PATH.match(path)
        if match and method == "POST":
            if match.group(2):
                return draft_api(store, match.group(1), self._body())
            return update_thread_api(store, match.group(1), self._body())
        raise ApiError(404, "Not found.")

    def _json(self, status: int, payload: dict, headers: Optional[dict] = None) -> None:
        extra = dict(headers or {})
        extra.update(getattr(self, "_session_headers", {}) or {})
        self._session_headers = {}
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8", extra)

    def log_message(self, fmt, *args):       # quiet console; errors still go through log_error
        pass

    def log_error(self, fmt, *args):
        import sys
        sys.stderr.write("radar: " + (fmt % args) + "\n")


def serve(port: int = 8010) -> None:
    shared_store().ready()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Runergy Radar is running at http://localhost:{port}")
    if not password():
        print("No RADAR_PASSWORD set: fine on this computer, required before putting it online.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
