"""Links to the Google Flights results page for a given search."""

from __future__ import annotations

from urllib.parse import urlencode

from airline_searcher.models import SearchRequest

CABIN_WORDS = {"premium_economy": "premium economy", "business": "business class", "first": "first class"}


def google_flights_link(req: SearchRequest) -> str:
    """Google Flights page for this route, dates, passengers and cabin."""
    q = f"Flights from {req.origin} to {req.destination} on {req.depart_date.isoformat()}"
    q += f" through {req.return_date.isoformat()}" if req.return_date else " one way"
    if req.adults > 1:
        q += f" for {req.adults} adults"
    if req.cabin in CABIN_WORDS:
        q += f" {CABIN_WORDS[req.cabin]}"
    if req.max_stops == 0:
        q += " nonstop"
    return "https://www.google.com/travel/flights?" + urlencode({"q": q, "curr": req.currency, "hl": "en"})
