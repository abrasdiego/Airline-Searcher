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
CACHE_SECONDS = int(os.environ.get("SEARCH_CACHE_SECONDS", "900"))
FARES_RETURNED = int(os.environ.get("MAX_WEB_FARES", "400"))  # every fare found, up to this many

_cache: dict[tuple, tuple[float, int, dict]] = {}
_cache_lock = threading.Lock()


def provider_name() -> str:
    return os.environ.get("SEARCH_PROVIDER", "serpapi").lower()


def health(check: bool = False) -> dict:
    """Whether this server can search real prices (shown as Live / Demo on the page).

    check=True also asks SerpApi whether the key works and how many searches are left
    (SerpApi's account API, which doesn't use a search).
    """
    from airline_searcher.providers.serpapi import find_api_key, similar_env_names

    name = provider_name()
    configured = {
        "serpapi": bool(find_api_key()),
        "duffel": bool(os.environ.get("DUFFEL_ACCESS_TOKEN")),
        "mock": True,
    }.get(name, False)
    body = {"provider": name, "configured": configured, "live": configured and name != "mock"}
    if check and name == "serpapi" and configured:
        from airline_searcher.providers.serpapi import account_status
        account = account_status(find_api_key())
        body["account"] = account
        if not account["ok"]:
            body["live"] = False
            from airline_searcher.providers.serpapi import describe_key
            body["help"] = (f"SerpApi didn't accept the key ({account['error'].rstrip('.')}); "
                            f"{describe_key(find_api_key())}. Copy it again from serpapi.com/manage-api-key, "
                            "update SERPAPI_API_KEY in Vercel and redeploy.")
        elif account.get("searches_left") == 0:
            body["live"] = False
            body["help"] = "Your SerpApi account has no searches left this month."
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
        "legs": [{
            "airline": leg.airline, "flight_number": leg.flight_number,
            "from": leg.from_airport, "from_name": leg.from_name, "to": leg.to_airport, "to_name": leg.to_name,
            "departure": leg.departure, "arrival": leg.arrival, "duration_minutes": leg.duration_minutes,
            "airplane": leg.airplane, "legroom": leg.legroom, "travel_class": leg.travel_class,
            "overnight": leg.overnight, "often_delayed": leg.often_delayed,
            "operated_by": leg.operated_by, "extensions": leg.extensions,
        } for leg in o.legs],
        "layover_details": o.layover_details,
        "departure_token": o.departure_token,
        "booking_token": o.booking_token,
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


def friendly_error(details: list[str]) -> str:
    """Explain why every search failed, in plain words."""
    text = " ".join(details).lower()
    if "run out of searches" in text or "plan" in text and "searches" in text:
        return "Your SerpApi account has run out of searches for this month. Check serpapi.com/dashboard."
    if "invalid api key" in text or "api key" in text and "invalid" in text:
        return "SerpApi rejected the API key. Check SERPAPI_API_KEY in Vercel's settings."
    if "timed out" in text:
        return "The flight search took too long to answer. Try again, or search fewer airports or dates."
    if "departure_id" in text or "arrival_id" in text:
        return "Google Flights didn't accept one of the airports. Try turning off nearby airports."
    return "The flight search failed. Try again in a minute."


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

    country = _country(query)
    fresh = query.get("fresh") == "1"
    key = (cfg.origin, cfg.destination, cfg.window_start, cfg.window_end, cfg.trip_type,
           cfg.min_nights, cfg.adults, cfg.cabin, cfg.currency, cfg.max_stops, country, provider_name(), today)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < CACHE_SECONDS:
            return hit[1], {**hit[2], "cached": True}

    try:
        provider = get_provider(provider_name())
    except ProviderError as exc:
        return 503, {"error": f"Live search isn't set up on this server yet ({exc})."}

    result = run_search(cfg, provider, today, max_requests=MAX_COMBINATIONS, workers=8,
                        country=country, fresh=fresh)
    if result.requests_made == 0:
        return 400, {"error": "That window is already in the past. Pick a later start date."}
    if not result.options and len(result.errors) >= result.requests_made:
        from airline_searcher.providers.serpapi import redact
        details = [redact(e) for e in result.errors if "limit is" not in e]
        return 502, {"error": friendly_error(details), "details": details[:3]}

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


def _country(query: dict[str, str]) -> Optional[str]:
    """Two-letter country the traveller shops from (Google prices vary by country)."""
    gl = (query.get("gl") or os.environ.get("SEARCH_COUNTRY") or "").strip().lower()
    return gl if len(gl) == 2 and gl.isalpha() else None


def _token_request(query: dict[str, str], token_name: str):
    """Shared parsing for the return-flights and booking-options lookups."""
    from airline_searcher.models import SearchRequest
    token = query.get(token_name, "") or query.get("token", "")
    if not token or len(token) > 6000:
        return None, (400, {"error": "Missing flight."})
    try:
        depart = date.fromisoformat(query.get("depart", ""))
        ret = date.fromisoformat(query["return"]) if query.get("return") else None
        cfg = parse_search({"origin": query.get("from", ""), "destination": query.get("to", ""),
                            "window_start": depart.isoformat(), "window_end": (ret or depart).isoformat(),
                            "trip_type": "round_trip" if ret else "one_way",
                            "nights": max((ret - depart).days, 1) if ret else 1, "adults": query.get("adults", "1"),
                            "currency": query.get("currency", "USD"), "cabin": query.get("cabin", "economy")},
                           0, depart)
    except (ConfigError, ValueError) as exc:
        return None, (400, {"error": str(exc).replace("searches[0].", "")})
    req = SearchRequest(cfg.origin, cfg.destination, depart, ret, adults=cfg.adults, cabin=cfg.cabin,
                        currency=cfg.currency, country=_country(query))
    try:
        provider = get_provider(provider_name())
    except ProviderError as exc:
        return None, (503, {"error": f"Live search isn't set up on this server yet ({exc})."})
    return (provider, req, token), None


def _cached(key, compute):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1], hit[2]
    status, body = compute()
    if status == 200:
        with _cache_lock:
            _cache[key] = (time.time(), status, body)
    return status, body


def returns_api(query: dict[str, str]) -> tuple[int, dict]:
    """Return flights for one chosen outbound (Google Flights' departure_token). Uses one search."""
    parsed, error = _token_request(query, "token")
    if error:
        return error
    provider, req, token = parsed
    if not req.return_date:
        return 400, {"error": "Return flights need a return date."}
    if not hasattr(provider, "return_flights"):
        return 501, {"error": "Return flights aren't available from this data source."}

    def compute():
        try:
            options = provider.return_flights(req, token)
        except ProviderError as exc:
            return 502, {"error": f"Couldn't load return flights: {exc}"}
        return 200, {"currency": req.currency, "fares": [_fare(o, None) for o in _dedupe(options)]}
    return _cached(("returns", token, req.currency, req.country), compute)


def booking_api(query: dict[str, str]) -> tuple[int, dict]:
    """Sellers and exact prices for one itinerary (Google Flights' booking_token). Uses one search."""
    parsed, error = _token_request(query, "booking_token")
    if error:
        return error
    provider, req, token = parsed
    if not hasattr(provider, "booking_options"):
        return 501, {"error": "Booking options aren't available from this data source."}

    def compute():
        try:
            options = provider.booking_options(req, token)
        except ProviderError as exc:
            return 502, {"error": f"Couldn't load booking options: {exc}"}
        return 200, {"currency": req.currency, "options": options}
    return _cached(("booking", token, req.currency, req.country), compute)


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
        elif path in ("/api/returns", "/api/booking"):
            api = returns_api if path == "/api/returns" else booking_api
            status, body = api(query_from_url(self.path))
            self._send(status, json.dumps(body).encode(), "application/json")
        elif path == "/api/health":
            check = query_from_url(self.path).get("check") == "1"
            self._send(200, json.dumps(health(check)).encode(), "application/json")
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
