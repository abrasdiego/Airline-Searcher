"""Data models shared across providers, search logic and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Leg:
    airline: str
    flight_number: str
    from_airport: str
    to_airport: str
    departure: str  # local time as reported by the provider, e.g. "2026-12-01 08:15"
    arrival: str


@dataclass
class FlightOption:
    provider: str
    origin: str
    destination: str
    depart_date: date
    return_date: Optional[date]
    price: float
    currency: str
    airlines: list[str] = field(default_factory=list)
    stops: int = 0
    duration_minutes: Optional[int] = None
    legs: list[Leg] = field(default_factory=list)
    booking_url: Optional[str] = None
    # Typical price range for this route and dates, when the provider reports one (Google Flights does)
    typical_low: Optional[float] = None
    typical_high: Optional[float] = None
    airline_logo: Optional[str] = None
    layovers: list[str] = field(default_factory=list)       # airport codes of connections
    co2_kg: Optional[int] = None                            # this itinerary
    co2_typical_kg: Optional[int] = None                    # typical for the route
    # Route insights for the search this option came from (shared by its options):
    # {"price_level": "low"|"typical"|"high", "lowest_price": ..., "price_history": [[ms, price], ...]}
    insights: Optional[dict] = None

    @property
    def depart_time(self) -> Optional[str]:
        return self.legs[0].departure if self.legs else None

    @property
    def arrive_time(self) -> Optional[str]:
        return self.legs[-1].arrival if self.legs else None

    @property
    def date_key(self) -> str:
        """Identifies the date combination (used to compare prices across runs)."""
        ret = self.return_date.isoformat() if self.return_date else "oneway"
        return f"{self.depart_date.isoformat()}_{ret}"

    @property
    def nights(self) -> Optional[int]:
        if self.return_date is None:
            return None
        return (self.return_date - self.depart_date).days


@dataclass
class SearchRequest:
    origin: str
    destination: str
    depart_date: date
    return_date: Optional[date]
    adults: int = 1
    cabin: str = "economy"
    currency: str = "USD"
    max_stops: Optional[int] = None
