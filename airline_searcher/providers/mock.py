"""Fake, deterministic prices for trying the tool without an API key."""

from __future__ import annotations

import hashlib
import random

from airline_searcher.models import FlightOption, Leg, SearchRequest
from airline_searcher.providers.base import FlightProvider

AIRLINES = ["Skyline Air", "Blue Jet", "Northwind", "Coastal Airways"]


class MockProvider(FlightProvider):
    name = "mock"

    def search(self, request: SearchRequest) -> list[FlightOption]:
        seed = f"{request.origin}{request.destination}{request.depart_date}{request.return_date}"
        rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16))
        options = []
        for _ in range(rng.randint(2, 5)):
            stops = rng.choice([0, 0, 1, 1, 2])
            if request.max_stops is not None and stops > request.max_stops:
                continue
            airline = rng.choice(AIRLINES)
            base = rng.uniform(180, 900) * (1.8 if request.return_date else 1.0)
            options.append(FlightOption(
                provider=self.name,
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=round(base * (1 - 0.12 * stops), 2) * request.adults,
                currency=request.currency,
                airlines=[airline],
                stops=stops,
                duration_minutes=rng.randint(90, 360) + stops * 120,
                legs=[Leg(airline, f"XX {rng.randint(100, 999)}", request.origin,
                          request.destination, f"{request.depart_date} 08:00",
                          f"{request.depart_date} 13:00")],
                booking_url=None,
            ))
        return options
