"""Google results (through SerpApi) limited to the sites you choose: Reddit, Threads, forums.

Reddit closed its feeds and public API to new apps, so Google's index of Reddit is the dependable
way to find new threads there. Every search costs one SerpApi credit, so searches are rationed:
the daily budget is spread across the day and the trigger phrases take turns.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from radar.sources import SourceError, env, fetch
from radar.text import Found, parse_when

ENDPOINT = "https://serpapi.com/search.json"
PER_SCAN_CAP = 6
NO_RESULTS = "hasn't returned any results"


def api_key() -> str:
    return env("RADAR_SERPAPI_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")


def allowance(budget: int, used: int, now: datetime) -> int:
    """How many searches this scan may make so the budget lasts the whole (UTC) day."""
    if budget <= 0:
        return 0
    day_fraction = (now.hour * 3600 + now.minute * 60 + now.second) / 86400
    earned = math.ceil(budget * day_fraction) + 2      # +2: the first scan of the day can search
    return max(0, min(PER_SCAN_CAP, budget - used, earned - used))


def build_query(phrase: str, sites: list[str]) -> str:
    phrase = " ".join(phrase.split())
    if not sites:
        return phrase
    scope = " OR ".join(f"site:{site}" for site in sites[:10])
    return f"{phrase} ({scope})" if len(sites) > 1 else f"{phrase} {scope}"


def time_filter(max_age_days: int) -> str:
    period = "d" if max_age_days <= 1 else "w" if max_age_days <= 7 else "m"
    return f"qdr:{period},sbd:1"      # sbd:1 = newest first


def search(phrase: str, sites: list[str], max_age_days: int, key: Optional[str] = None,
           now: Optional[datetime] = None) -> list[Found]:
    key = key or api_key()
    if not key:
        raise SourceError("No SerpApi key is set (SERPAPI_API_KEY).")
    response = fetch(ENDPOINT, what="SerpApi", timeout=25, params={
        "engine": "google", "q": build_query(phrase, sites), "tbs": time_filter(max_age_days),
        "num": 20, "hl": "en", "api_key": key})
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceError(f"SerpApi sent an unreadable answer (HTTP {response.status_code}).") from exc
    error = data.get("error") if isinstance(data, dict) else "unexpected answer"
    if error:
        if NO_RESULTS in str(error):
            return []
        if response.status_code == 401 or "api key" in str(error).lower():
            raise SourceError("SerpApi didn't accept the key. Check SERPAPI_API_KEY.")
        if response.status_code == 429 or "run out of searches" in str(error).lower():
            raise SourceError("SerpApi says this account has no searches left this month.")
        raise SourceError(f"SerpApi: {str(error)[:160]}")
    if response.status_code >= 400:
        raise SourceError(f"SerpApi answered with HTTP {response.status_code}.")
    found = []
    for result in data.get("organic_results") or []:
        link = result.get("link")
        if not link:
            continue
        found.append(Found(
            url=link, title=result.get("title") or "", body=result.get("snippet") or "",
            created_at=parse_when(result.get("date"), now), source="Google search",
            phrase=phrase, trusted=True))
    return found
