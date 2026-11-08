"""Google Flights results via SerpApi (https://serpapi.com/google-flights-api).

Needs SERPAPI_API_KEY. Each date combination costs one SerpApi search.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

from airline_searcher.models import FlightOption, Leg, SearchRequest
from airline_searcher.providers.base import FlightProvider, ProviderError

ENDPOINT = "https://serpapi.com/search.json"
TRAVEL_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}


def _stops_param(max_stops: Optional[int]) -> int:
    # SerpApi: 0 = any, 1 = nonstop only, 2 = 1 stop or fewer, 3 = 2 stops or fewer
    if max_stops is None or max_stops > 2:
        return 0
    return max_stops + 1


class SerpApiProvider(FlightProvider):
    name = "serpapi"

    def __init__(self, api_key: Optional[str] = None, session: Optional[requests.Session] = None,
                 timeout: float = 60):
        self.api_key = api_key or os.environ.get("SERPAPI_API_KEY")
        if not self.api_key:
            raise ProviderError("SERPAPI_API_KEY is not set")
        self.session = session or requests.Session()
        self.timeout = timeout

    def build_params(self, req: SearchRequest) -> dict:
        params = {
            "engine": "google_flights",
            "departure_id": req.origin,
            "arrival_id": req.destination,
            "outbound_date": req.depart_date.isoformat(),
            "type": 1 if req.return_date else 2,
            "currency": req.currency,
            "hl": "en",
            "adults": req.adults,
            "travel_class": TRAVEL_CLASS[req.cabin],
            "stops": _stops_param(req.max_stops),
            "api_key": self.api_key,
        }
        if req.return_date:
            params["return_date"] = req.return_date.isoformat()
        return params

    def search(self, request: SearchRequest) -> list[FlightOption]:
        try:
            resp = self.session.get(ENDPOINT, params=self.build_params(request), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"SerpApi request failed: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"SerpApi returned non-JSON (HTTP {resp.status_code})") from exc
        if resp.status_code != 200 or "error" in payload:
            # "no results" is reported as an error by SerpApi; treat it as an empty result.
            message = str(payload.get("error", f"HTTP {resp.status_code}"))
            if "hasn't returned any results" in message:
                return []
            raise ProviderError(f"SerpApi error: {message}")
        return self.parse(payload, request)

    def parse(self, payload: dict, req: SearchRequest) -> list[FlightOption]:
        booking_url = (payload.get("search_metadata") or {}).get("google_flights_url")
        options: list[FlightOption] = []
        for group in ("best_flights", "other_flights"):
            for item in payload.get(group) or []:
                price = item.get("price")
                if price is None:
                    continue
                legs = [
                    Leg(
                        airline=f.get("airline", ""),
                        flight_number=f.get("flight_number", ""),
                        from_airport=(f.get("departure_airport") or {}).get("id", ""),
                        to_airport=(f.get("arrival_airport") or {}).get("id", ""),
                        departure=(f.get("departure_airport") or {}).get("time", ""),
                        arrival=(f.get("arrival_airport") or {}).get("time", ""),
                    )
                    for f in item.get("flights") or []
                ]
                airlines = list(dict.fromkeys(leg.airline for leg in legs if leg.airline))
                options.append(
                    FlightOption(
                        provider=self.name,
                        origin=req.origin,
                        destination=req.destination,
                        depart_date=req.depart_date,
                        return_date=req.return_date,
                        price=float(price),
                        currency=req.currency,
                        airlines=airlines,
                        stops=max(len(legs) - 1, 0),
                        duration_minutes=item.get("total_duration"),
                        legs=legs,
                        booking_url=booking_url,
                    )
                )
        return options
