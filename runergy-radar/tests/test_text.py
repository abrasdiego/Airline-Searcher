from datetime import datetime, timedelta, timezone

import pytest

from radar.defaults import clean_settings, defaults
from radar.text import (Found, canonical_url, clean_text, evaluate, has_phrase, parse_when,
                        platform_of, thread_id)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("a,b", [
    ("https://www.reddit.com/r/Running/comments/1abc2d/which_app/?utm_source=share",
     "https://old.reddit.com/r/running/comments/1abc2d/"),
    ("https://www.reddit.com/r/running/comments/1abc2d/which_app/k9x1/",
     "https://reddit.com/r/running/comments/1abc2d"),
    ("https://www.threads.net/@Mia/post/Cxyz?xmt=abc", "https://www.threads.com/@mia/post/Cxyz"),
    ("http://WWW.LetsRun.com/forum/flat_read.php?thread=12&utm_medium=x#top",
     "https://letsrun.com/forum/flat_read.php?thread=12"),
])
def test_same_conversation_has_one_id(a, b):
    assert canonical_url(a) == canonical_url(b)
    assert thread_id(a) == thread_id(b)


def test_different_forum_threads_stay_different():
    assert thread_id("https://x.com/f?thread=1") != thread_id("https://x.com/f?thread=2")
    assert thread_id("https://reddit.com/r/a/comments/1/") != thread_id("https://reddit.com/r/a/comments/2/")


def test_platforms():
    assert platform_of("https://old.reddit.com/r/running/comments/1/x") == ("reddit", "r/running")
    assert platform_of("https://www.threads.com/@mia/post/C1") == ("threads", "@mia")
    assert platform_of("https://www.letsrun.com/forum/x") == ("web", "letsrun.com")


@pytest.mark.parametrize("text,expected", [
    ("3 days ago", NOW - timedelta(days=3)),
    ("an hour ago", NOW - timedelta(hours=1)),
    ("2 hours ago", NOW - timedelta(hours=2)),
    ("Nov 10, 2026", datetime(2026, 11, 10, tzinfo=timezone.utc)),
    ("2026-11-12T08:30:00Z", datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc)),
    ("2026-11-12T08:30:00+0000", datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc)),
    ("Thu, 12 Nov 2026 08:30:00 GMT", datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc)),
    ("", None), ("soon", None),
])
def test_parse_when(text, expected):
    assert parse_when(text, NOW) == expected


def test_phrases_match_whole_words_only():
    assert has_phrase("Any Couch-to-5K tips?", "couch to 5k")
    assert has_phrase("best  running\napp?", "best running app")
    assert not has_phrase("I love trail running apparel", "running app")
    assert not has_phrase("rerun the job", "run")


def test_clean_text_strips_markup():
    assert clean_text("<b>Best</b> running&nbsp;app &amp; plan<br>ok") == "Best running app & plan\nok"


def found(**kw):
    base = dict(url="https://www.reddit.com/r/running/comments/1a/x/", title="Which running app is best?",
                body="Looking for a running app recommendation for my first marathon.",
                created_at=NOW - timedelta(hours=2))
    base.update(kw)
    return Found(**base)


def test_evaluate_scores_a_good_match():
    thread = evaluate(found(), defaults(), NOW)
    assert thread["platform"] == "reddit" and thread["community"] == "r/running"
    assert "App recommendations" in thread["topics"]
    assert "running app recommendation" in thread["matched"]
    assert thread["kw_score"] >= 70


def test_evaluate_skips_old_excluded_and_unmatched():
    s = defaults()
    assert evaluate(found(created_at=NOW - timedelta(days=10)), s, NOW) is None
    assert evaluate(found(body="We are hiring! best running app team"), s, NOW) is None
    assert evaluate(found(title="Nice weather", body="Went for a run today"), s, NOW) is None
    assert evaluate(found(url="javascript:alert(1)"), s, NOW) is None


def test_feed_item_must_be_about_running():
    s = defaults()
    s["topics"] = [{"name": "Plans", "phrases": ["training plan"]}]
    assert evaluate(found(url="https://chess.example/t/1", title="My chess training plan", body="Openings"), s, NOW) is None
    assert evaluate(found(title="My training plan", body="for a marathon"), s, NOW)


def test_search_results_count_without_a_literal_match():
    thread = evaluate(found(title="What do you use to plan your runs?", body="", trusted=True,
                            phrase="best running app"), defaults(), NOW)
    assert thread["matched"] == ["best running app"]
    assert thread["topics"] == ["App recommendations"]


def test_untitled_posts_get_a_title():
    thread = evaluate(found(url="https://www.threads.com/@a/post/C1", title="",
                            body="How many gels for a marathon? " * 8), defaults(), NOW)
    assert thread["title"].endswith("…") and len(thread["title"]) <= 110


def test_clean_settings_fills_and_limits():
    s = clean_settings({"topics": [{"name": " Plans ", "phrases": "5k plan, 5K Plan\n10k plan"}],
                        "sites": ["https://www.Reddit.com/r/running/"], "max_age_days": "99",
                        "ai": {"min_relevance": -5}, "feeds": [{"url": "https://a.example/feed"}]})
    assert s["topics"] == [{"name": "Plans", "phrases": ["5k plan", "10k plan"]}]
    assert s["sites"] == ["reddit.com/r/running"]
    assert s["max_age_days"] == 30 and s["ai"]["min_relevance"] == 0
    assert s["feeds"] == [{"name": "a.example", "url": "https://a.example/feed"}]
    assert s["brand"]["name"] == "Runergy Lab"
    assert clean_settings(None) == defaults() == clean_settings(defaults())


@pytest.mark.parametrize("bad", [
    {"feeds": [{"url": "ftp://x/feed"}]}, {"sites": ["not a site"]},
    {"topics": [{"name": "", "phrases": ["x"]}]}, {"brand": {"url": "javascript:x"}},
    {"topics": [{"name": "A", "phrases": []}, {"name": "a", "phrases": []}]},
])
def test_clean_settings_rejects_mistakes(bad):
    with pytest.raises(ValueError):
        clean_settings(bad)
