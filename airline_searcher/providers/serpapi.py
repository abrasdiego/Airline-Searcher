"""Google Flights results via SerpApi (https://serpapi.com/google-flights-api).

Needs SERPAPI_API_KEY. Each date combination costs one SerpApi search.
"""

from __future__ import annotations

import os
import re
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


def clean_key(value: str) -> str:
    """Undo common copy-paste mistakes: spaces, quotes, an 'api_key=' prefix or a whole URL."""
    value = value.strip().strip("'\"`").strip()
    match = re.search(r"api_key=([A-Za-z0-9]+)", value)
    if match:
        return match.group(1)
    hexes = re.findall(r"[0-9a-fA-F]{64}", value)
    if len(hexes) == 1:
        return hexes[0]
    return value


def describe_key(value: Optional[str]) -> str:
    """A safe description of a key (never the key itself), to help spot a bad paste."""
    if not value:
        return "no key is set"
    shape = "64 letters and digits, like a SerpApi key" if re.fullmatch(r"[0-9a-fA-F]{64}", value) else (
        f"{len(value)} characters long; SerpApi keys are 64 letters and digits")
    return f"the saved key is {shape}"


def find_api_key() -> Optional[str]:
    """SERPAPI_API_KEY, also accepting close variants (SERPAPI_KEY, SERP_API_KEY, stray spaces)."""
    exact = clean_key(os.environ.get("SERPAPI_API_KEY", ""))
    if exact:
        return exact
    for name, value in os.environ.items():
        if _normalize(name) in ACCEPTED_NAMES and clean_key(value):
            return clean_key(value)
    return None


def similar_env_names() -> list[str]:
    """Names (never values) of settings that look like a SerpApi key, to help fix typos."""
    return sorted(name for name in os.environ if "SERP" in name.upper())


ACCOUNT_ENDPOINT = "https://serpapi.com/account.json"


def account_status(api_key: str, timeout: float = 15) -> dict:
    """Check the key with SerpApi's account API (free: it doesn't use a search).

    Returns {"ok": True, "plan": ..., "searches_left": ..., "searches_per_month": ...}
    or {"ok": False, "error": ...}. Never returns the key or account email.
    """
    try:
        resp = requests.get(ACCOUNT_ENDPOINT, params={"api_key": api_key}, timeout=timeout)
        data = resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Couldn't reach SerpApi ({type(exc).__name__})"}
    except ValueError:
        return {"ok": False, "error": f"SerpApi answered with HTTP {resp.status_code}"}
    if resp.status_code != 200 or data.get("error"):
        return {"ok": False, "error": redact(data.get("error") or f"HTTP {resp.status_code}", api_key)}
    left = data.get("total_searches_left", data.get("plan_searches_left"))
    return {"ok": True, "plan": data.get("plan_name"), "searches_left": left,
            "searches_per_month": data.get("searches_per_month"),
            "used_this_month": data.get("this_month_usage")}


def google_link(url: Optional[str], currency: str, country: Optional[str]) -> Optional[str]:
    """Google Flights link with the same currency, country and language as the search."""
    if not url:
        return None
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"curr": currency, "hl": "en"})
    if country:
        query["gl"] = country.lower()
    return urlunsplit(parts._replace(query=urlencode(query)))


def redact(text: str, secret: Optional[str] = None) -> str:
    """Remove API keys from text that may be shown to people."""
    text = re.sub(r"(api_key=)[^&\s'\"]+", r"\1***", str(text))
    return text.replace(secret, "***") if secret else text


def _leg(f: dict) -> Leg:
    dep, arr = f.get("departure_airport") or {}, f.get("arrival_airport") or {}
    return Leg(
        airline=f.get("airline", ""),
        flight_number=f.get("flight_number", ""),
        from_airport=dep.get("id", ""),
        to_airport=arr.get("id", ""),
        departure=dep.get("time", ""),
        arrival=arr.get("time", ""),
        duration_minutes=f.get("duration"),
        airplane=f.get("airplane"),
        legroom=f.get("legroom"),
        travel_class=f.get("travel_class"),
        overnight=bool(f.get("overnight")),
        often_delayed=bool(f.get("often_delayed_by_over_30_min")),
        from_name=dep.get("name"),
        to_name=arr.get("name"),
        operated_by=f.get("plane_and_crew_by"),
        extensions=[e for e in f.get("extensions") or []
                    if isinstance(e, str) and not e.startswith("Carbon emissions")][:6],
    )


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
            # Include the flights Google hides by default: about 5x more itineraries
            # (checked on YYJ-CNF: 8 -> 42) for the same one search.
            "show_hidden": "true",
            "api_key": self.api_key,
        }
        if os.environ.get("SERPAPI_DEEP_SEARCH", "").lower() in ("1", "true", "yes"):
            params["deep_search"] = "true"   # browser-identical results, but several times slower
        if req.return_date:
            params["return_date"] = req.return_date.isoformat()
        if req.country:
            params["gl"] = req.country.lower()   # same point of sale as the traveller sees on Google
        if req.fresh:
            params["no_cache"] = "true"          # SerpApi otherwise reuses results up to an hour old
        return params

    def search(self, request: SearchRequest) -> list[FlightOption]:
        payload = self._get(self.build_params(request))
        return self.parse(payload, request) if payload else []

    def _get(self, params: dict) -> dict:
        try:
            resp = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            # Never include the exception text: requests puts the full URL (with api_key) in it.
            kind = "timed out" if isinstance(exc, requests.Timeout) else f"failed ({type(exc).__name__})"
            raise ProviderError(f"SerpApi request {kind}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"SerpApi returned non-JSON (HTTP {resp.status_code})") from exc
        if resp.status_code != 200 or "error" in payload:
            message = str(payload.get("error", f"HTTP {resp.status_code}"))
            if "hasn't returned any results" in message:
                return {}
            raise ProviderError(f"SerpApi error: {redact(message, self.api_key)}")
        return payload

    def return_flights(self, request: SearchRequest, departure_token: str) -> list[FlightOption]:
        """Return-flight choices for one outbound itinerary. Prices are the round-trip total."""
        payload = self._get({**self.build_params(request), "departure_token": departure_token})
        return self.parse(payload, request) if payload else []

    def booking_options(self, request: SearchRequest, booking_token: str) -> list[dict]:
        """Where this exact itinerary can be bought and for how much (uses one search).

        Each option: {"seller", "price", "title", "extensions", "baggage", "url", "post_data", "separate"}.
        "url" + "post_data" is Google's redirect to the seller (sent as a form POST).
        """
        params = {**self.build_params(request), "booking_token": booking_token}
        params.pop("show_hidden", None)
        payload = self._get(params)
        options = []
        for opt in payload.get("booking_options") or []:
            parts = [opt["together"]] if opt.get("together") else [p for p in (opt.get("departing"), opt.get("returning")) if p]
            for k, part in enumerate(parts):
                req = part.get("booking_request") or {}
                options.append({
                    "seller": part.get("book_with") or "",
                    "price": part.get("price"),
                    "title": part.get("option_title"),
                    "extensions": [e for e in part.get("extensions") or [] if isinstance(e, str)][:4],
                    "baggage": [b for b in part.get("baggage_prices") or [] if isinstance(b, str)][:3],
                    "url": req.get("url") if str(req.get("url", "")).startswith("https://") else None,
                    "post_data": req.get("post_data"),
                    "separate": None if len(parts) == 1 else ("departing" if k == 0 else "returning"),
                })
        return options

    def parse(self, payload: dict, req: SearchRequest) -> list[FlightOption]:
        booking_url = google_link(
            (payload.get("search_metadata") or {}).get("google_flights_url"), req.currency, req.country)
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
                legs = [_leg(f) for f in item.get("flights") or []]
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
                        layover_details=[{"code": lay.get("id", ""), "name": lay.get("name", ""),
                                          "duration_minutes": lay.get("duration"),
                                          "overnight": bool(lay.get("overnight"))}
                                         for lay in item.get("layovers") or []],
                        departure_token=item.get("departure_token"),
                        booking_token=item.get("booking_token"),
                        co2_kg=_kg((item.get("carbon_emissions") or {}).get("this_flight")),
                        co2_typical_kg=_kg((item.get("carbon_emissions") or {}).get("typical_for_this_route")),
                        insights=insights,
                    )
                )
        return options
