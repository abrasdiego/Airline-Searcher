"""Remember previous runs so the email can show whether prices went up or down."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from airline_searcher.search import SearchResult

MAX_RUNS_KEPT = 90


class History:
    def __init__(self, path: Optional[str | Path]):
        """path=None keeps history in memory only (nothing is read or saved)."""
        self.path = Path(path) if path else None
        self.data: dict = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    @staticmethod
    def key(result: SearchResult) -> str:
        c = result.config
        return (f"{c.origin}-{c.destination}|{c.trip_type}|{c.window_start}|{c.window_end}|"
                f"{c.min_nights}-{c.max_nights}|{c.cabin}|{c.adults}|{c.currency}")

    def _entry(self, result: SearchResult) -> dict:
        return self.data.get(self.key(result), {})

    def previous_best(self, result: SearchResult, today: date) -> Optional[float]:
        runs = [r for r in self._entry(result).get("runs", []) if r["date"] != today.isoformat()]
        return runs[-1]["best_price"] if runs else None

    def lowest_seen(self, result: SearchResult) -> Optional[float]:
        prices = [r["best_price"] for r in self._entry(result).get("runs", [])]
        return min(prices) if prices else None

    def previous_price_for(self, result: SearchResult, date_key: str, today: date) -> Optional[float]:
        entry = self._entry(result)
        # If we already ran today, compare against the run before that.
        field = "prev_prices" if entry.get("last_prices_date") == today.isoformat() else "last_prices"
        return entry.get(field, {}).get(date_key)

    def record(self, result: SearchResult, today: date) -> None:
        best = result.best
        if best is None:
            return
        entry = self.data.setdefault(self.key(result), {"runs": []})
        runs = [r for r in entry["runs"] if r["date"] != today.isoformat()]
        runs.append({"date": today.isoformat(), "best_price": best.price, "currency": best.currency})
        entry["runs"] = runs[-MAX_RUNS_KEPT:]
        if entry.get("last_prices_date") != today.isoformat():
            entry["prev_prices"] = entry.get("last_prices", {})
        entry["last_prices"] = {o.date_key: o.price for o in result.cheapest_by_dates()}
        entry["last_prices_date"] = today.isoformat()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
