"""Vercel entry point: every URL is rewritten here (see vercel.json) and served by one handler."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.web import Handler  # noqa: E402


class handler(Handler):  # noqa: N801 (Vercel looks for this exact name)
    pass
