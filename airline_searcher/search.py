"""Turn a search config into date combinations, query the provider and rank the results."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from airline_searcher.config import SearchConfig
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
               max_requests: int = 60) -> SearchResult:
    today = today or date.today()
    pairs = date_combinations(cfg, today)
    result = SearchResult(config=cfg, combinations_total=len(pairs))
    if len(pairs) > max_requests:
        result.errors.append(
            f"Window has {len(pairs)} date combinations but max_requests_per_search is "
            f"{max_requests}; only the first {max_requests} were searched."
        )
        pairs = pairs[:max_requests]

    for depart, ret in pairs:
        request = SearchRequest(
            origin=cfg.origin, destination=cfg.destination, depart_date=depart,
            return_date=ret, adults=cfg.adults, cabin=cfg.cabin, currency=cfg.currency,
            max_stops=cfg.max_stops,
        )
        result.requests_made += 1
        try:
            found = provider.search(request)
        except ProviderError as exc:
            label = f"{depart}" + (f" → {ret}" if ret else "")
            log.warning("%s %s: %s", cfg.name, label, exc)
            result.errors.append(f"{label}: {exc}")
            continue
        result.options.extend(o for o in found if _keep(o, cfg))
        log.info("%s %s%s: %d options", cfg.name, depart, f" → {ret}" if ret else "", len(found))
    return result
