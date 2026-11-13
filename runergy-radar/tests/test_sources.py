from datetime import datetime, timezone

import pytest

from radar import sources
from radar.sources import SourceError, feeds, serpapi, threads

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)

ALERT = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:idx="urn:atom-extension:indexing">
 <id>tag:google.com,2005:reader/user/1/state/com.google/alerts/2</id>
 <title>Google Alert - running app</title>
 <link href="https://www.google.com/alerts/feeds/1/2" rel="self"/>
 <entry>
  <id>tag:google.com,2013:googlealerts/feed:1</id>
  <title type="html">Which &lt;b&gt;running app&lt;/b&gt; for a first marathon? : r/running - Reddit</title>
  <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://www.reddit.com/r/running/comments/1abc/which/&amp;ct=ga&amp;usg=x"/>
  <published>2026-11-13T09:00:00Z</published>
  <updated>2026-11-13T09:00:00Z</updated>
  <content type="html">Looking for a &lt;b&gt;running app&lt;/b&gt; with a plan&amp;nbsp;...</content>
  <author><name></name></author>
 </entry>
</feed>"""

RSS = b"""<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<title>Forum</title><link>https://forum.example/</link>
<item><title>Best 10k training plan?</title><link>https://forum.example/t/42</link>
<description><![CDATA[<p>Need a <b>10k training plan</b></p>]]></description>
<pubDate>Thu, 12 Nov 2026 08:30:00 GMT</pubDate><dc:creator>sam</dc:creator></item>
<item><title>No link</title></item>
</channel></rss>"""


def test_google_alert_feed():
    (item,) = feeds.parse_feed(ALERT, "Alert", trusted=True, now=NOW)
    assert item.url == "https://www.reddit.com/r/running/comments/1abc/which/"
    assert "running app" in item.title and item.trusted
    assert item.created_at == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)


def test_rss_feed():
    (item,) = feeds.parse_feed(RSS, "Forum", trusted=False, now=NOW)
    assert (item.url, item.author) == ("https://forum.example/t/42", "sam")
    assert "10k training plan" in item.body
    assert item.created_at == datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc)


def test_not_a_feed():
    with pytest.raises(SourceError):
        feeds.parse_feed(b"<html><body>hi</body></html>", "Page", False)
    with pytest.raises(SourceError):
        feeds.parse_feed(b"not xml at all", "Page", False)
    assert feeds.is_alert_feed("https://www.google.com/alerts/feeds/1/2")
    assert not feeds.is_alert_feed("https://forum.example/feed")


class Reply:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def test_serpapi_search(monkeypatch):
    seen = {}

    def fake(url, params=None, **kw):
        seen.update(params)
        return Reply({"organic_results": [
            {"title": "Which running app?", "link": "https://www.reddit.com/r/running/comments/1/x/",
             "snippet": "Looking for an app", "date": "2 hours ago"},
            {"title": "no link"}]})
    monkeypatch.setattr(serpapi, "fetch", fake)
    (item,) = serpapi.search("best running app", ["reddit.com", "threads.com"], 3, "KEY", NOW)
    assert seen["q"] == "best running app (site:reddit.com OR site:threads.com)"
    assert seen["tbs"] == "qdr:w,sbd:1" and seen["api_key"] == "KEY"
    assert item.trusted and item.phrase == "best running app"
    assert item.created_at == datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("data,status,expect", [
    ({"error": "Google hasn't returned any results for this query."}, 200, []),
    ({"error": "Invalid API key. Your API key should be here: ..."}, 401, "didn't accept the key"),
    ({"error": "Your account has run out of searches."}, 429, "no searches left"),
    (ValueError("x"), 502, "unreadable"),
])
def test_serpapi_errors(monkeypatch, data, status, expect):
    monkeypatch.setattr(serpapi, "fetch", lambda *a, **k: Reply(data, status))
    if expect == []:
        assert serpapi.search("x", [], 1, "KEY") == []
    else:
        with pytest.raises(SourceError, match=expect):
            serpapi.search("x", [], 1, "KEY")


def test_search_budget_is_spread_over_the_day():
    at = lambda hour: datetime(2026, 11, 13, hour, 0, tzinfo=timezone.utc)   # noqa: E731
    assert serpapi.allowance(30, 0, at(0)) == 2          # first scan of the day may search
    assert serpapi.allowance(30, 2, at(0)) == 0          # ...but not burn the day's budget at once
    assert serpapi.allowance(30, 2, at(12)) == serpapi.PER_SCAN_CAP
    assert serpapi.allowance(30, 30, at(23)) == 0        # never over budget
    assert serpapi.allowance(30, 28, at(23)) == 2
    assert serpapi.allowance(0, 0, at(12)) == 0
    assert serpapi.time_filter(1) == "qdr:d,sbd:1" and serpapi.time_filter(20) == "qdr:m,sbd:1"


def test_threads_search(monkeypatch):
    monkeypatch.setattr(threads, "fetch", lambda *a, **k: Reply({"data": [
        {"id": "1", "text": "How many gels in a marathon?", "permalink": "https://www.threads.com/@mia/post/C1",
         "timestamp": "2026-11-13T09:00:00+0000", "username": "mia"}, {"id": "2"}]}))
    (item,) = threads.search("how many gels", "TOKEN", NOW)
    assert item.author == "mia" and item.created_at.hour == 9 and item.trusted
    monkeypatch.setattr(threads, "fetch", lambda *a, **k: Reply(
        {"error": {"message": "Error validating access token", "code": 190}}, 400))
    with pytest.raises(SourceError, match="access token"):
        threads.search("x", "TOKEN")


def test_fetch_never_leaks_the_address(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("https://serpapi.com/search.json?api_key=SECRET failed")
    monkeypatch.setattr(sources.requests, "get", boom)
    with pytest.raises(SourceError) as error:
        sources.fetch("https://serpapi.com/search.json", params={"api_key": "SECRET"}, what="SerpApi")
    assert "SECRET" not in str(error.value)
