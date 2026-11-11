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


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.upper() if ch.isalnum())


ACCEPTED_NAMES = {"SERPAPIAPIKEY", "SERPAPIKEY", "SERPAPI"}


def find_api_key() -> Optional[str]:
    """SERPAPI_API_KEY, also accepting close variants (SERPAPI_KEY, SERP_API_KEY, stray spaces)."""
    exact = os.environ.get("SERPAPI_API_KEY", "").strip()
    if exact:
        return exact
    for name, value in os.environ.items():
        if _normalize(name) in ACCEPTED_NAMES and value.strip():
            return value.strip()
    return None


def similar_env_names() -> list[str]:
    """Names (never values) of settings that look like a SerpApi key, to help fix typos."""
    return sorted(name for name in os.environ if "SERP" in name.upper())


def _kg(grams) -> Optional[int]:
    return round(grams / 1000) if isinstance(grams, (int, float)) else None


class SerpApiProvider(FlightProvider):
    name = "serpapi"

    def __init__(self, api_key: Optional[str] = None, session: Optional[requests.Session] = None,
                 timeout: float = 60):
        self.api_key = api_key or find_api_key()
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
        price_insights = payload.get("price_insights") or {}
        typical = price_insights.get("typical_price_range") or [None, None]
        typical_low, typical_high = (list(typical) + [None, None])[:2]
        insights = {
            "price_level": price_insights.get("price_level"),
            "lowest_price": price_insights.get("lowest_price"),
            "price_history": price_insights.get("price_history") or [],
        } if price_insights else None
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
                        typical_low=typical_low,
                        typical_high=typical_high,
                        airline_logo=item.get("airline_logo")
                        or next((f.get("airline_logo") for f in item.get("flights") or []
                                 if f.get("airline_logo")), None),
                        layovers=[lay.get("id", "") for lay in item.get("layovers") or [] if lay.get("id")],
                        co2_kg=_kg((item.get("carbon_emissions") or {}).get("this_flight")),
                        co2_typical_kg=_kg((item.get("carbon_emissions") or {}).get("typical_for_this_route")),
                        insights=insights,
                    )
                )
        return options
