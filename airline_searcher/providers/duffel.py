"""Airline offers via the Duffel API (https://duffel.com/docs/api/offer-requests).

Needs DUFFEL_ACCESS_TOKEN. Prices are returned in the currency Duffel chooses
(usually your account's currency), not necessarily the one in the config.
"""

from __future__ import annotations

import os
import re
from typing import Optional

import requests

from airline_searcher.models import FlightOption, Leg, SearchRequest
from airline_searcher.providers.base import FlightProvider, ProviderError

ENDPOINT = "https://api.duffel.com/air/offer_requests"
_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?")


def parse_iso_duration(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = _DURATION_RE.fullmatch(value)
    if not match:
        return None
    days, hours, minutes = (int(x) if x else 0 for x in match.groups())
    return days * 1440 + hours * 60 + minutes


class DuffelProvider(FlightProvider):
    name = "duffel"

    def __init__(self, token: Optional[str] = None, session: Optional[requests.Session] = None,
                 timeout: float = 90):
        self.token = token or os.environ.get("DUFFEL_ACCESS_TOKEN")
        if not self.token:
            raise ProviderError("DUFFEL_ACCESS_TOKEN is not set")
        self.session = session or requests.Session()
        self.timeout = timeout

    def build_body(self, req: SearchRequest) -> dict:
        slices = [{"origin": req.origin, "destination": req.destination,
                   "departure_date": req.depart_date.isoformat()}]
        if req.return_date:
            slices.append({"origin": req.destination, "destination": req.origin,
                           "departure_date": req.return_date.isoformat()})
        data = {
            "slices": slices,
            "passengers": [{"type": "adult"} for _ in range(req.adults)],
            "cabin_class": req.cabin,
        }
        if req.max_stops is not None:
            data["max_connections"] = min(req.max_stops, 2)
        return {"data": data}

    def search(self, request: SearchRequest) -> list[FlightOption]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Duffel-Version": "v2",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            resp = self.session.post(ENDPOINT, params={"return_offers": "true"},
                                     json=self.build_body(request), headers=headers,
                                     timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"Duffel request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"Duffel error HTTP {resp.status_code}: {resp.text[:300]}")
        return self.parse(resp.json(), request)

    def parse(self, payload: dict, req: SearchRequest) -> list[FlightOption]:
        options: list[FlightOption] = []
        for offer in (payload.get("data") or {}).get("offers") or []:
            legs: list[Leg] = []
            duration = 0
            max_stops = 0
            for sl in offer.get("slices") or []:
                segments = sl.get("segments") or []
                max_stops = max(max_stops, len(segments) - 1)
                duration += parse_iso_duration(sl.get("duration")) or 0
                for seg in segments:
                    carrier = seg.get("marketing_carrier") or {}
                    legs.append(Leg(
                        airline=carrier.get("name", ""),
                        flight_number=f"{carrier.get('iata_code', '')} {seg.get('marketing_carrier_flight_number', '')}".strip(),
                        from_airport=(seg.get("origin") or {}).get("iata_code", ""),
                        to_airport=(seg.get("destination") or {}).get("iata_code", ""),
                        departure=(seg.get("departing_at") or "").replace("T", " ")[:16],
                        arrival=(seg.get("arriving_at") or "").replace("T", " ")[:16],
                    ))
            airlines = list(dict.fromkeys(leg.airline for leg in legs if leg.airline))
            if not airlines and offer.get("owner"):
                airlines = [offer["owner"].get("name", "")]
            options.append(FlightOption(
                provider=self.name,
                origin=req.origin,
                destination=req.destination,
                depart_date=req.depart_date,
                return_date=req.return_date,
                price=float(offer["total_amount"]),
                currency=offer.get("total_currency", req.currency),
                airlines=airlines,
                stops=max_stops,
                duration_minutes=duration or None,
                legs=legs,
            ))
        return options
