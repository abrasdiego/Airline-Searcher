"""Print what SerpApi's Google Flights API returns for one search, to check the parser.

Prints structure, counts and a few sample values; never the API key.
Run by hand: Actions -> "Inspect flight data" -> Run workflow (uses about 5 SerpApi searches).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

import requests

ENDPOINT = "https://serpapi.com/search.json"


def call(params: dict) -> dict:
    params = {**params, "engine": "google_flights", "api_key": os.environ["SERPAPI_API_KEY"]}
    resp = requests.get(ENDPOINT, params=params, timeout=120)
    data = resp.json()
    data.pop("search_parameters", None)
    return {"_status": resp.status_code, **data}


def keys(obj) -> list:
    return sorted(obj.keys()) if isinstance(obj, dict) else []


def summarize(label: str, data: dict) -> list[dict]:
    print(f"\n===== {label} =====")
    print("HTTP", data["_status"], "| error:", data.get("error"))
    meta = data.get("search_metadata") or {}
    print("status:", meta.get("status"), "| time:", meta.get("total_time_taken"))
    print("top-level keys:", keys(data))
    items = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    print("best_flights:", len(data.get("best_flights") or []), "| other_flights:", len(data.get("other_flights") or []))
    if not items:
        return []
    print("item keys (union):", sorted(set().union(*[set(i) for i in items])))
    legs = [f for i in items for f in i.get("flights") or []]
    print("leg keys (union):", sorted(set().union(*[set(f) for f in legs])) if legs else [])
    lays = [l for i in items for l in i.get("layovers") or []]
    print("layover keys (union):", sorted(set().union(*[set(l) for l in lays])) if lays else [])
    print("stops:", dict(Counter(len(i.get("flights") or []) - 1 for i in items)))
    print("airlines:", dict(Counter(f.get("airline") for f in legs).most_common(15)))
    prices = sorted(i["price"] for i in items if isinstance(i.get("price"), (int, float)))
    print("prices:", prices[:5], "...", prices[-3:], "| missing price:", sum(1 for i in items if "price" not in i))
    print("routes:", dict(Counter(
        "-".join([i["flights"][0]["departure_airport"]["id"]] + [f["arrival_airport"]["id"] for f in i["flights"]])
        for i in items if i.get("flights")).most_common(12)))
    print("price_insights:", json.dumps({k: v for k, v in (data.get("price_insights") or {}).items()
                                         if k != "price_history"}),
          "| history points:", len((data.get("price_insights") or {}).get("price_history") or []))
    print("airports block:", json.dumps(data.get("airports"))[:600])
    sample = dict(items[0]); sample.pop("departure_token", None); sample.pop("booking_token", None)
    print("sample item:", json.dumps(sample)[:1500])
    print("has departure_token:", sum(1 for i in items if i.get("departure_token")),
          "| has booking_token:", sum(1 for i in items if i.get("booking_token")))
    return items


def main() -> None:
    origin, dest, out_date, ret_date = sys.argv[1:5]
    base = {"departure_id": origin, "arrival_id": dest, "outbound_date": out_date, "currency": "CAD", "hl": "en"}
    if ret_date and ret_date != "-":
        base.update({"type": 1, "return_date": ret_date})
    else:
        base["type"] = 2
    plain = summarize("default", call(base))
    summarize("show_hidden=true", call({**base, "show_hidden": "true"}))
    summarize("deep_search=true + show_hidden=true", call({**base, "show_hidden": "true", "deep_search": "true"}))
    token = next((i.get("departure_token") for i in plain if i.get("departure_token")), None)
    if token:
        returns = summarize("return flights for the first outbound (departure_token)",
                            call({**base, "departure_token": token}))
        btoken = next((i.get("booking_token") for i in returns if i.get("booking_token")), None)
        if btoken:
            data = call({**base, "booking_token": btoken})
            print("\n===== booking options (booking_token) =====")
            print("HTTP", data["_status"], "| error:", data.get("error"), "| top-level keys:", keys(data))
            opts = data.get("booking_options") or []
            print("booking_options:", len(opts))
            for o in opts[:6]:
                t = o.get("together") or {}
                req = t.get("booking_request") or {}
                print("  -", t.get("book_with"), t.get("price"), "| keys:", keys(t), "| request url:", str(req.get("url"))[:80])


if __name__ == "__main__":
    main()
