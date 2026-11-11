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
        for _ in range(rng.randint(14, 30)):
            stops = rng.choice([0, 0, 1, 1, 1, 2])
            if request.max_stops is not None and stops > request.max_stops:
                continue
            airline = rng.choice(AIRLINES)
            price = round(base * weekday_x * rng.uniform(0.78, 1.4) * (1 - 0.1 * stops) * cabin_x) * request.adults
            minutes = int(base / 1.9 + 70 + stops * rng.randint(80, 240))
            dep = datetime.combine(request.depart_date, datetime.min.time()) + timedelta(
                hours=rng.choice([6, 7, 9, 11, 13, 16, 18, 21]), minutes=rng.choice([0, 15, 30, 45]))
            via = rng.sample(HUBS, stops)
            legs, lays, t = [], [], dep
            points = [origin] + via + [destination]
            flight_minutes = int(base / 1.9 + 70)
            for k in range(len(points) - 1):
                leg_min = max(60, flight_minutes // (stops + 1) + rng.randint(-30, 30))
                arr_t = t + timedelta(minutes=leg_min)
                legs.append(Leg(airline, f"{airline[:2].upper()} {rng.randint(100, 999)}", points[k], points[k + 1],
                                t.strftime("%Y-%m-%d %H:%M"), arr_t.strftime("%Y-%m-%d %H:%M"),
                                duration_minutes=leg_min, airplane=rng.choice(["Airbus A321", "Boeing 787", "Boeing 737"]),
                                legroom=rng.choice(["30 in", "31 in", "28 in"]), travel_class="Economy",
                                overnight=arr_t.date() != t.date(), extensions=["In-seat USB outlet"]))
                if k < len(points) - 2:
                    wait = rng.randint(60, 300)
                    lays.append({"code": points[k + 1], "name": f"{points[k + 1]} Airport",
                                 "duration_minutes": wait, "overnight": False})
                    arr_t += timedelta(minutes=wait)
                t = arr_t
            minutes = int((t - dep).total_seconds() // 60)
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
                legs=legs,
                layover_details=lays,
                departure_token=f"mock|{rng.random():.6f}" if request.return_date else None,
                typical_low=typical_low,
                typical_high=typical_high,
                layovers=via,
                co2_kg=int(co2_typical * rng.uniform(0.8, 1.25)),
                co2_typical_kg=co2_typical,
                insights={"price_level": None, "lowest_price": None, "price_history": history},
            ))
        return options

    def return_flights(self, request: SearchRequest, departure_token: str) -> list[FlightOption]:
        """Fake return choices: the same search run in the other direction, on the return date."""
        back = SearchRequest(request.destination, request.origin, request.return_date, None,
                             adults=request.adults, cabin=request.cabin, currency=request.currency)
        options = self.search(back)
        for o in options:
            o.origin, o.destination = request.origin, request.destination
            o.depart_date, o.return_date = request.depart_date, request.return_date
            o.departure_token = None
        return options
