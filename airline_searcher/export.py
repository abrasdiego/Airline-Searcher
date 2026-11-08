"""Export the daily results as JSON so the web page can show the same data as the email."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from airline_searcher.history import History
from airline_searcher.models import FlightOption
from airline_searcher.search import SearchResult

SCHEMA_VERSION = 1


def _option(opt: FlightOption, previous_price: Optional[float] = None) -> dict:
    data = {
        "depart_date": opt.depart_date.isoformat(),
        "return_date": opt.return_date.isoformat() if opt.return_date else None,
        "nights": opt.nights,
        "price": opt.price,
        "currency": opt.currency,
        "airlines": opt.airlines,
        "stops": opt.stops,
        "duration_minutes": opt.duration_minutes,
        "flight_numbers": [leg.flight_number for leg in opt.legs if leg.flight_number],
        "booking_url": opt.booking_url,
        "provider": opt.provider,
    }
    if previous_price is not None:
        data["previous_price"] = previous_price
    return data


def _search(result: SearchResult, history: History, today: date) -> dict:
    c = result.config
    best = result.best
    entry = history.data.get(history.key(result), {})
    runs = [r for r in entry.get("runs", []) if r["date"] != today.isoformat()]
    if best is not None:
        runs.append({"date": today.isoformat(), "best_price": best.price, "currency": best.currency})
    return {
        "name": c.name,
        "origin": c.origin,
        "destination": c.destination,
        "window_start": c.window_start.isoformat(),
        "window_end": c.window_end.isoformat(),
        "trip_type": c.trip_type,
        "min_nights": c.min_nights,
        "max_nights": c.max_nights,
        "adults": c.adults,
        "cabin": c.cabin,
        "currency": c.currency,
        "max_stops": c.max_stops,
        "requests_made": result.requests_made,
        "combinations_total": result.combinations_total,
        "best": _option(best) if best else None,
        "previous_best": history.previous_best(result, today),
        "lowest_seen": history.lowest_seen(result),
        "top": [_option(o) for o in result.top],
        "by_date": [_option(o, history.previous_price_for(result, o.date_key, today))
                    for o in result.cheapest_by_dates()],
        "best_price_history": runs[-90:],
        "errors": result.errors[:20],
    }


def build_payload(results: list[SearchResult], history: History, provider: str,
                  today: Optional[date] = None, now: Optional[datetime] = None) -> dict:
    """Must be called before history.record() so 'previous' values refer to the last run."""
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "provider": provider,
        "searches": [_search(r, history, today) for r in results],
    }


def write_json(payload: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
