import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.store import Store, reset_shared_store  # noqa: E402

KEYS = ("RADAR_PASSWORD", "CRON_SECRET", "RADAR_CRON_SECRET", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "SERPAPI_API_KEY", "SERPAPI_KEY", "RADAR_SERPAPI_KEY", "THREADS_ACCESS_TOKEN", "VERCEL",
        "DATABASE_URL", "POSTGRES_URL", "RADAR_DATABASE_URL", "RADAR_DB", "RADAR_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RADAR_DB", str(tmp_path / "radar.db"))
    reset_shared_store()
    yield
    reset_shared_store()


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    """Every storage test runs on SQLite, and on Postgres too when RADAR_TEST_POSTGRES is set."""
    if request.param == "postgres":
        url = os.environ.get("RADAR_TEST_POSTGRES")
        if not url:
            pytest.skip("RADAR_TEST_POSTGRES not set")
        s = Store(url=url).ready()
        for table in ("radar_threads", "radar_meta", "radar_locks"):
            s._run(f"DELETE FROM {table}")
    else:
        s = Store(path=str(tmp_path / "s.db")).ready()
    yield s
    s.close()
