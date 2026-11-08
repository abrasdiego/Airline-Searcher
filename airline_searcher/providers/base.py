from __future__ import annotations

from abc import ABC, abstractmethod

from airline_searcher.models import FlightOption, SearchRequest


class ProviderError(RuntimeError):
    pass


class FlightProvider(ABC):
    name: str = "base"

    @abstractmethod
    def search(self, request: SearchRequest) -> list[FlightOption]:
        """Return the flight options for one origin/destination/date combination."""
