"""The search website: a small JSON API plus the page in web/index.html.

Run locally:   python -m airline_searcher.web            (http://localhost:8000)
Fake prices:   SEARCH_PROVIDER=mock python -m airline_searcher.web
On Vercel the same search_api() is served by api/search.py.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from airline_searcher.config import ConfigError, parse_search
from airline_searcher.models import FlightOption
from airline_searcher.providers import ProviderError, get_provider
from airline_searcher.search import SearchResult, run_search

PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"
MAX_COMBINATIONS = int(os.environ.get("MAX_WEB_COMBINATIONS", "14"))
RESULTS_SHOWN = 10


def route_average(result: SearchResult) -> tuple[Optional[float], str]:
    """Average price for the route, and where it came from.

    Prefers Google Flights' typical price range for the route (the middle of it);
    otherwise uses the mean of every fare found in this search.
    """
    mids = {(o.typical_low + o.typical_high) / 2 for o in result.options
            if o.typical_low is not None and o.typical_high is not None}
    if mids:
        return round(statistics.mean(mids), 2), "typical"
    if result.options:
        return round(statistics.mean(o.price for o in result.options), 2), "found"
    return None, "none"


def _fare(o: FlightOption, average: Optional[float]) -> dict:
    return {
        "depart_date": o.depart_date.isoformat(),
        "return_date": o.return_date.isoformat() if o.return_date else None,
        "nights": o.nights,
        "price": o.price,
        "currency": o.currency,
        "airlines": o.airlines,
        "stops": o.stops,
        "duration_minutes": o.duration_minutes,
        "booking_url": o.booking_url,
        "vs_average": None if average is None else ("below" if o.price <= average else "above"),
    }


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
        "nights": int(query.get("nights", "7") or 7),
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

    try:
        provider = get_provider(os.environ.get("SEARCH_PROVIDER", "serpapi"))
    except ProviderError as exc:
        return 503, {"error": f"The search server isn't set up yet: {exc}."}

    result = run_search(cfg, provider, today, max_requests=MAX_COMBINATIONS, workers=8)
    if result.requests_made == 0:
        return 400, {"error": "That window is already in the past. Pick a later start date."}
    if not result.options and len(result.errors) >= result.requests_made:
        return 502, {"error": "The flight search failed. Try again in a minute.",
                     "details": result.errors[:3]}

    average, source = route_average(result)
    fares = sorted(result.options, key=lambda o: o.price)[:RESULTS_SHOWN]
    return 200, {
        "origin": cfg.origin,
        "destination": cfg.destination,
        "currency": cfg.currency,
        "adults": cfg.adults,
        "searched": result.requests_made,
        "skipped": max(result.combinations_total - result.requests_made, 0),
        "average": average,
        "average_source": source,
        "fares": [_fare(o, average) for o in fares],
    }


def query_from_url(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/search":
            status, body = search_api(query_from_url(self.path))
            self._send(status, json.dumps(body).encode(), "application/json")
        else:
            self._send(404, b"Not found", "text/plain")


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    print(f"Fare Watch running at http://localhost:{port}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
