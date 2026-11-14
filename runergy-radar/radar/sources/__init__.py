"""Where conversations come from. Each source returns `Found` items or raises SourceError."""

from __future__ import annotations

import os

import requests

USER_AGENT = "RunergyRadar/1.0 (personal mention monitor)"
MAX_BYTES = 3_000_000


class SourceError(Exception):
    """A source could not be read; the message is shown on the Sources page."""


def env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip().strip("\"'")
        if value:
            return value
    return ""


def fetch(url: str, params: dict | None = None, timeout: float = 15, what: str = "The site",
          headers: dict | None = None) -> requests.Response:
    """GET with a size cap and errors turned into plain sentences."""
    try:
        response = requests.get(url, params=params, timeout=timeout, stream=True,
                                headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
        body = response.raw.read(MAX_BYTES + 1, decode_content=True)
    except requests.Timeout as exc:
        raise SourceError(f"{what} took too long to answer.") from exc
    except requests.RequestException as exc:
        raise SourceError(f"{what} couldn't be reached ({type(exc).__name__}).") from exc
    if len(body) > MAX_BYTES:
        raise SourceError(f"{what} sent more data than expected.")
    response._content = body
    return response
