from airline_searcher.providers.base import FlightProvider, ProviderError


def get_provider(name: str) -> FlightProvider:
    name = name.lower()
    if name == "serpapi":
        from airline_searcher.providers.serpapi import SerpApiProvider
        return SerpApiProvider()
    if name == "duffel":
        from airline_searcher.providers.duffel import DuffelProvider
        return DuffelProvider()
    if name == "mock":
        from airline_searcher.providers.mock import MockProvider
        return MockProvider()
    raise ProviderError(f"Unknown provider {name!r} (choose serpapi, duffel or mock)")


__all__ = ["FlightProvider", "ProviderError", "get_provider"]
