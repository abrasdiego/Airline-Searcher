"""Turn a search config into date combinations, query the provider and rank the results."""

from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from airline_searcher.config import SearchConfig
from airline_searcher.links import google_flights_link
from airline_searcher.models import FlightOption, SearchRequest
from airline_searcher.providers.base import FlightProvider, ProviderError

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    config: SearchConfig
    options: list[FlightOption] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requests_made: int = 0
    combinations_total: int = 0

    @property
    def top(self) -> list[FlightOption]:
        return sorted(self.options, key=lambda o: o.price)[: self.config.top_n]

    @property
    def best(self) -> Optional[FlightOption]:
        return min(self.options, key=lambda o: o.price) if self.options else None

    def route_average(self) -> tuple[Optional[float], str]:
        """The usual price for this route, and where it came from.

        Prefers the provider's typical price range for the route (Google Flights reports one;
        the middle of it is used). Otherwise, the mean of every fare found in this search.
        """
        mids = {(o.typical_low + o.typical_high) / 2 for o in self.options
                if o.typical_low is not None and o.typical_high is not None}
        if mids:
            return round(statistics.mean(mids), 2), "typical"
        if self.options:
            return round(statistics.mean(o.price for o in self.options), 2), "found"
        return None, "none"

    def cheapest_by_dates(self) -> list[FlightOption]:
        """Cheapest option for every date combination, ordered by date."""
        best: dict[str, FlightOption] = {}
        for opt in self.options:
            if opt.date_key not in best or opt.price < best[opt.date_key].price:
                best[opt.date_key] = opt
        return [best[k] for k in sorted(best)]


def date_combinations(cfg: SearchConfig, today: date) -> list[tuple[date, Optional[date]]]:
    """All (depart, return) pairs inside the window. Past departure dates are skipped."""
    first = max(cfg.window_start, today)
    pairs: list[tuple[date, Optional[date]]] = []
    day = first
    while day <= cfg.window_end:
        if cfg.trip_type == "one_way":
            pairs.append((day, None))
        else:
            for nights in range(cfg.min_nights, cfg.max_nights + 1):
                ret = day + timedelta(days=nights)
                if cfg.return_within_window and ret > cfg.window_end:
                    continue
                pairs.append((day, ret))
        day += timedelta(days=1)
    return pairs


def _keep(opt: FlightOption, cfg: SearchConfig) -> bool:
    if cfg.max_stops is not None and opt.stops > cfg.max_stops:
        return False
    if cfg.max_price is not None and opt.price > cfg.max_price:
        return False
    return True


def run_search(cfg: SearchConfig, provider: FlightProvider, today: Optional[date] = None,
               max_requests: int = 60, workers: int = 1) -> SearchResult:
    """Search every date combination. workers > 1 runs that many requests at once."""
    today = today or date.today()
    pairs = date_combinations(cfg, today)
    result = SearchResult(config=cfg, combinations_total=len(pairs))
    if len(pairs) > max_requests:
        result.errors.append(
            f"Window has {len(pairs)} date combinations but the limit is {max_requests}; "
            f"only the first {max_requests} were searched."
        )
        pairs = pairs[:max_requests]

    requests = [
        SearchRequest(
            origin=cfg.origin, destination=cfg.destination, depart_date=depart,
            return_date=ret, adults=cfg.adults, cabin=cfg.cabin, currency=cfg.currency,
            max_stops=cfg.max_stops,
        )
        for depart, ret in pairs
    ]

    def one(request: SearchRequest):
        try:
            return request, provider.search(request), None
        except ProviderError as exc:
            return request, [], exc

    if workers > 1 and len(requests) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(one, requests))
    else:
        outcomes = [one(r) for r in requests]

    for request, found, exc in outcomes:
        result.requests_made += 1
        label = f"{request.depart_date}" + (f" → {request.return_date}" if request.return_date else "")
        if exc is not None:
            log.warning("%s %s: %s", cfg.name, label, exc)
            result.errors.append(f"{label}: {exc}")
            continue
        for opt in found:
            if not opt.booking_url:
                opt.booking_url = google_flights_link(request)
        result.options.extend(o for o in found if _keep(o, cfg))
        log.info("%s %s: %d options", cfg.name, label, len(found))
    return result
