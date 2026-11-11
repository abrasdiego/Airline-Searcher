"""Fake, deterministic prices for trying the tool without an API key."""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from airline_searcher.models import FlightOption, Leg, SearchRequest
from airline_searcher.providers.base import FlightProvider

AIRLINES = ["Skyline Air", "Blue Jet", "Northwind", "Coastal Airways", "Meridian", "Altair"]
HUBS = ["AMS", "MAD", "FRA", "CDG", "LHR", "IST", "DOH", "ATL", "ORD", "PTY"]


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class MockProvider(FlightProvider):
    name = "mock"

    def search(self, request: SearchRequest) -> list[FlightOption]:
        route = random.Random(_seed(request.origin, request.destination))
        base = route.uniform(180, 700) * (1.8 if request.return_date else 1.0)
        rng = random.Random(_seed(request.origin, request.destination, request.depart_date,
                                  request.return_date, request.cabin))
        cabin_x = {"economy": 1, "premium_economy": 1.7, "business": 3.6, "first": 5.5}[request.cabin]
        weekday_x = 1.15 if request.depart_date.weekday() in (4, 6) else 0.92 if request.depart_date.weekday() in (1, 2) else 1

        typical_low, typical_high = round(base * 0.85 * cabin_x), round(base * 1.25 * cabin_x)
        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        history = [[int((now - timedelta(days=d)).timestamp() * 1000),
                    round(base * cabin_x * (1.02 + 0.12 * random.Random(_seed(request.origin, d)).random()
                                            - d / 600))]
                   for d in range(60, -1, -3)]
        origin, destination = request.origin.split(",")[0], request.destination.split(",")[0]
        options = []
        for _ in range(rng.randint(4, 8)):
            stops = rng.choice([0, 0, 1, 1, 1, 2])
            if request.max_stops is not None and stops > request.max_stops:
                continue
            airline = rng.choice(AIRLINES)
            price = round(base * weekday_x * rng.uniform(0.78, 1.4) * (1 - 0.1 * stops) * cabin_x) * request.adults
            minutes = int(base / 1.9 + 70 + stops * rng.randint(80, 240))
            dep = datetime.combine(request.depart_date, datetime.min.time()) + timedelta(
                hours=rng.choice([6, 7, 9, 11, 13, 16, 18, 21]), minutes=rng.choice([0, 15, 30, 45]))
            arr = dep + timedelta(minutes=minutes)
            via = rng.sample(HUBS, stops)
            co2_typical = int(base * 0.9)
            options.append(FlightOption(
                provider=self.name,
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=float(price),
                currency=request.currency,
                airlines=[airline],
                stops=stops,
                duration_minutes=minutes,
                legs=[Leg(airline, f"{airline[:2].upper()} {rng.randint(100, 999)}", origin, destination,
                          dep.strftime("%Y-%m-%d %H:%M"), arr.strftime("%Y-%m-%d %H:%M"))],
                typical_low=typical_low,
                typical_high=typical_high,
                layovers=via,
                co2_kg=int(co2_typical * rng.uniform(0.8, 1.25)),
                co2_typical_kg=co2_typical,
                insights={"price_level": None, "lowest_price": None, "price_history": history},
            ))
        return options
