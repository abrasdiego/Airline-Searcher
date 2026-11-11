"""The search website: a small JSON API plus the page in web/.

Run locally:   SERPAPI_API_KEY=... python -m airline_searcher.web     (http://localhost:8000)
Fake prices:   SEARCH_PROVIDER=mock python -m airline_searcher.web
On Vercel the same functions are served by api/search.py and api/health.py.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from airline_searcher.config import ConfigError, parse_search
from airline_searcher.models import FlightOption
from airline_searcher.providers import ProviderError, get_provider
from airline_searcher.search import run_search

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_COMBINATIONS = int(os.environ.get("MAX_WEB_COMBINATIONS", "14"))
CACHE_SECONDS = int(os.environ.get("SEARCH_CACHE_SECONDS", "1800"))
FARES_RETURNED = 30

_cache: dict[tuple, tuple[float, int, dict]] = {}
_cache_lock = threading.Lock()


def provider_name() -> str:
    return os.environ.get("SEARCH_PROVIDER", "serpapi").lower()


def health() -> dict:
    """Whether this server can search real prices (shown as Live / Demo on the page)."""
    from airline_searcher.providers.serpapi import find_api_key, similar_env_names

    name = provider_name()
    configured = {
        "serpapi": bool(find_api_key()),
        "duffel": bool(os.environ.get("DUFFEL_ACCESS_TOKEN")),
        "mock": True,
    }.get(name, False)
    body = {"provider": name, "configured": configured, "live": configured and name != "mock"}
    if name == "serpapi" and not configured:
        seen = similar_env_names()
        body["help"] = (f"Found settings named {', '.join(seen)}, but none has a value. "
                        "Check the value and that Production is ticked, then redeploy."
                        if seen else
                        "No SERPAPI_API_KEY setting reached this deployment. Add it in the project's "
                        "Environment Variables with Production ticked, then redeploy.")
    return body


def _clock(stamp: Optional[str]) -> Optional[str]:
    return stamp[11:16] if stamp and len(stamp) >= 16 else None


def _day_offset(start: Optional[str], end: Optional[str]) -> int:
    try:
        return (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days
    except (TypeError, ValueError):
        return 0


def _fare(o: FlightOption, average: Optional[float]) -> dict:
    return {
        "depart_date": o.depart_date.isoformat(),
        "return_date": o.return_date.isoformat() if o.return_date else None,
        "nights": o.nights,
        "price": o.price,
        "currency": o.currency,
        "airlines": o.airlines,
        "airline_logo": o.airline_logo,
        "stops": o.stops,
        "layovers": o.layovers,
        "duration_minutes": o.duration_minutes,
        "depart_time": _clock(o.depart_time),
        "arrive_time": _clock(o.arrive_time),
        "arrive_day_offset": _day_offset(o.depart_time, o.arrive_time),
        "from_airport": o.legs[0].from_airport if o.legs else o.origin.split(",")[0],
        "to_airport": o.legs[-1].to_airport if o.legs else o.destination.split(",")[0],
        "co2_kg": o.co2_kg,
        "co2_typical_kg": o.co2_typical_kg,
        "booking_url": o.booking_url,
        "vs_average": None if average is None else ("below" if o.price <= average else "above"),
        "diff_from_average": None if average is None else round(o.price - average, 2),
    }


def _insights(best: FlightOption) -> dict:
    raw = best.insights or {}
    level = raw.get("price_level")
    if not level and best.typical_low is not None and best.typical_high is not None:
        level = "low" if best.price < best.typical_low else "high" if best.price > best.typical_high else "typical"
    history: dict[str, float] = {}
    for point in raw.get("price_history") or []:
        try:
            stamp, price = point[0], float(point[1])
            day = datetime.fromtimestamp(stamp / 1000 if stamp > 1e11 else stamp, timezone.utc).date()
        except (TypeError, ValueError, IndexError, OSError):
            continue
        history[day.isoformat()] = price
    return {
        "price_level": level,
        "typical_low": best.typical_low,
        "typical_high": best.typical_high,
        "history": [[d, p] for d, p in sorted(history.items())][-90:],
    }


def _dedupe(options: list[FlightOption]) -> list[FlightOption]:
    seen, out = set(), []
    for o in sorted(options, key=lambda o: o.price):
        key = (o.date_key, tuple(o.airlines), o.price, o.depart_time, o.arrive_time)
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def search_api(query: dict[str, str], today: Optional[date] = None) -> tuple[int, dict]:
    """Handle one search from the page. Returns (HTTP status, JSON body)."""
    today = today or date.today()
    trip = query.get("trip", "round_trip")
    raw = {
        "origin": query.get("from", ""),
        "destination": query.get("to", ""),
        "window_start": query.get("start", ""),
        "window_days": query.get("days", "14"),
        "trip_type": trip,
        "nights": int(query.get("nights") or 7),
        "adults": query.get("adults", "1"),
        "currency": query.get("currency", "USD"),
        "cabin": query.get("cabin", "economy"),
    }
    if query.get("stops", "") != "":
        raw["max_stops"] = query["stops"]
    try:
        cfg = parse_search(raw, 0, today)
    except (ConfigError, ValueError) as exc:
        return 400, {"error": str(exc).replace("searches[0].", "")}
    if not 1 <= cfg.adults <= 9:
        return 400, {"error": "Adults must be between 1 and 9."}
    if (cfg.window_end - cfg.window_start).days > 30:
        return 400, {"error": "Use a travel window of 31 days or less."}

    key = (cfg.origin, cfg.destination, cfg.window_start, cfg.window_end, cfg.trip_type,
           cfg.min_nights, cfg.adults, cfg.cabin, cfg.currency, cfg.max_stops, provider_name(), today)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1], {**hit[2], "cached": True}

    try:
        provider = get_provider(provider_name())
    except ProviderError as exc:
        return 503, {"error": f"Live search isn't set up on this server yet ({exc})."}

    result = run_search(cfg, provider, today, max_requests=MAX_COMBINATIONS, workers=8)
    if result.requests_made == 0:
        return 400, {"error": "That window is already in the past. Pick a later start date."}
    if not result.options and len(result.errors) >= result.requests_made:
        return 502, {"error": "The flight search didn't respond. Try again in a minute.",
                     "details": result.errors[:3]}

    average, source = result.route_average()
    fares = _dedupe(result.options)
    best = fares[0] if fares else None
    body = {
        "live": provider_name() != "mock",
        "provider": provider_name(),
        "origin": cfg.origin,
        "destination": cfg.destination,
        "trip_type": cfg.trip_type,
        "nights": cfg.min_nights if cfg.trip_type == "round_trip" else None,
        "adults": cfg.adults,
        "cabin": cfg.cabin,
        "currency": cfg.currency,
        "window_start": cfg.window_start.isoformat(),
        "window_end": cfg.window_end.isoformat(),
        "searched": result.requests_made,
        "skipped": max(result.combinations_total - result.requests_made, 0),
        "failed": len([e for e in result.errors if "limit is" not in e]),
        "average": average,
        "average_source": source,
        "insights": _insights(best) if best else None,
        "calendar": [
            {"depart_date": o.depart_date.isoformat(),
             "return_date": o.return_date.isoformat() if o.return_date else None,
             "price": o.price,
             "vs_average": None if average is None else ("below" if o.price <= average else "above")}
            for o in result.cheapest_by_dates()
        ],
        "fares": [_fare(o, average) for o in fares[:FARES_RETURNED]],
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cached": False,
    }
    with _cache_lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = (now, 200, body)
    return 200, body


def query_from_url(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


STATIC = {"/": "index.html", "/index.html": "index.html", "/airports.json": "airports.json",
          "/web/index.html": "index.html", "/web/airports.json": "airports.json"}
TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path in STATIC:
            file = WEB_DIR / STATIC[path]
            self._send(200, file.read_bytes(), TYPES[file.suffix],
                       "public, max-age=86400" if file.suffix == ".json" else "no-store")
        elif path == "/api/search":
            status, body = search_api(query_from_url(self.path))
            self._send(status, json.dumps(body).encode(), "application/json")
        elif path == "/api/health":
            self._send(200, json.dumps(health()).encode(), "application/json")
        else:
            self._send(404, b"Not found", "text/plain")

    def log_message(self, fmt, *args):  # keep the console quiet apart from searches
        if "/api/search" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    state = "live prices" if health()["live"] else "demo prices (set SERPAPI_API_KEY for live)"
    print(f"Fare Watch running at http://localhost:{port} with {state}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
