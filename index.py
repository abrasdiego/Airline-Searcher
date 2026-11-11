"""Vercel entry point: the whole site (page, airport list, /api/search, /api/health) is one handler.

Vercel's Python preset looks for a top-level `handler` (or `app`) in index.py. Locally, run
`python -m airline_searcher.web` instead.
"""

from airline_searcher.web import Handler


class handler(Handler):  # noqa: N801 (Vercel looks for this exact name)
    pass
