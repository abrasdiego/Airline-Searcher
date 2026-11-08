"""Load and validate the YAML configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

CABINS = ("economy", "premium_economy", "business", "first")
TRIP_TYPES = ("one_way", "round_trip")


class ConfigError(ValueError):
    pass


@dataclass
class SearchConfig:
    name: str
    origin: str
    destination: str
    window_start: date
    window_end: date  # inclusive
    trip_type: str = "round_trip"
    min_nights: int = 7
    max_nights: int = 7
    return_within_window: bool = True
    adults: int = 1
    cabin: str = "economy"
    currency: str = "USD"
    max_stops: Optional[int] = None
    max_price: Optional[float] = None
    top_n: int = 5


@dataclass
class Settings:
    provider: str = "serpapi"
    max_requests_per_search: int = 60
    email_subject: str = "Daily flight prices"
    history_file: str = "data/history.json"


@dataclass
class Config:
    settings: Settings
    searches: list[SearchConfig] = field(default_factory=list)


def _parse_date(value: Any, field_name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{field_name}: expected a YYYY-MM-DD date, got {value!r}") from exc


def _iata(value: Any, field_name: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ConfigError(f"{field_name}: expected a 3-letter IATA airport/city code, got {value!r}")
    return code


def _parse_window(raw: dict, label: str, today: date) -> tuple[date, date]:
    """Supports an absolute window (window_start) or a rolling one (window_starts_in_days)."""
    if "window_start" in raw:
        start = _parse_date(raw["window_start"], f"{label}.window_start")
    elif "window_starts_in_days" in raw:
        start = today + timedelta(days=int(raw["window_starts_in_days"]))
    else:
        raise ConfigError(f"{label}: set either window_start or window_starts_in_days")

    if "window_end" in raw:
        end = _parse_date(raw["window_end"], f"{label}.window_end")
    else:
        days = int(raw.get("window_days", 14))
        if days < 1:
            raise ConfigError(f"{label}.window_days must be >= 1")
        end = start + timedelta(days=days - 1)

    if end < start:
        raise ConfigError(f"{label}: window_end is before window_start")
    return start, end


def parse_search(raw: dict, index: int, today: date) -> SearchConfig:
    label = f"searches[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{label}: expected a mapping")

    origin = _iata(raw.get("origin"), f"{label}.origin")
    destination = _iata(raw.get("destination"), f"{label}.destination")
    start, end = _parse_window(raw, label, today)

    trip_type = str(raw.get("trip_type", "round_trip"))
    if trip_type not in TRIP_TYPES:
        raise ConfigError(f"{label}.trip_type must be one of {TRIP_TYPES}")

    nights = raw.get("nights")
    if isinstance(nights, int):
        min_nights = max_nights = nights
    elif isinstance(nights, dict):
        min_nights = int(nights.get("min", 1))
        max_nights = int(nights.get("max", min_nights))
    elif nights is None:
        min_nights = max_nights = 7
    else:
        raise ConfigError(f"{label}.nights must be a number or {{min: X, max: Y}}")
    if trip_type == "round_trip" and not (1 <= min_nights <= max_nights):
        raise ConfigError(f"{label}.nights: need 1 <= min <= max")

    cabin = str(raw.get("cabin", "economy"))
    if cabin not in CABINS:
        raise ConfigError(f"{label}.cabin must be one of {CABINS}")

    max_stops = raw.get("max_stops")
    if max_stops is not None:
        max_stops = int(max_stops)
        if max_stops < 0:
            raise ConfigError(f"{label}.max_stops must be >= 0")

    max_price = raw.get("max_price")

    return SearchConfig(
        name=str(raw.get("name") or f"{origin} → {destination}"),
        origin=origin,
        destination=destination,
        window_start=start,
        window_end=end,
        trip_type=trip_type,
        min_nights=min_nights,
        max_nights=max_nights,
        return_within_window=bool(raw.get("return_within_window", True)),
        adults=int(raw.get("adults", 1)),
        cabin=cabin,
        currency=str(raw.get("currency", "USD")).upper(),
        max_stops=max_stops,
        max_price=float(max_price) if max_price is not None else None,
        top_n=int(raw.get("top_n", 5)),
    )


def parse_config(data: dict, today: Optional[date] = None) -> Config:
    today = today or date.today()
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")

    raw_settings = data.get("settings") or {}
    settings = Settings(
        provider=str(raw_settings.get("provider", "serpapi")),
        max_requests_per_search=int(raw_settings.get("max_requests_per_search", 60)),
        email_subject=str(raw_settings.get("email_subject", "Daily flight prices")),
        history_file=str(raw_settings.get("history_file", "data/history.json")),
    )

    raw_searches = data.get("searches") or []
    if not raw_searches:
        raise ConfigError("config needs at least one entry under 'searches'")
    searches = [parse_search(raw, i, today) for i, raw in enumerate(raw_searches)]
    return Config(settings=settings, searches=searches)


def load_config(path: str | Path, today: Optional[date] = None) -> Config:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_config(data, today)
