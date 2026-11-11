from datetime import date

import pytest

from airline_searcher.config import ConfigError, parse_config
from airline_searcher.emailer import SmtpSettings, build_message
from airline_searcher.history import History
from airline_searcher.models import SearchRequest
from airline_searcher.providers.base import FlightProvider, ProviderError
from airline_searcher.providers.duffel import DuffelProvider, parse_iso_duration
from airline_searcher.providers.mock import MockProvider
from airline_searcher.providers.serpapi import SerpApiProvider
from airline_searcher.report import build_report
from airline_searcher.search import date_combinations, run_search

TODAY = date(2026, 11, 8)


def make_config(**overrides):
    search = {"origin": "jfk", "destination": "LIS", "window_start": "2027-01-10",
              "window_days": 14, "nights": 7}
    search.update(overrides)
    return parse_config({"searches": [search]}, today=TODAY)


def test_config_defaults_and_window():
    s = make_config().searches[0]
    assert s.origin == "JFK"
    assert s.window_start == date(2027, 1, 10)
    assert s.window_end == date(2027, 1, 23)  # 14 days inclusive
    assert s.trip_type == "round_trip"


def test_rolling_window():
    s = parse_config({"searches": [{"origin": "JFK", "destination": "LIS",
                                    "window_starts_in_days": 30}]}, today=TODAY).searches[0]
    assert s.window_start == date(2026, 12, 8)
    assert s.window_end == date(2026, 12, 21)


@pytest.mark.parametrize("bad", [{"origin": "JFKX"}, {"cabin": "luxury"},
                                 {"nights": {"min": 9, "max": 5}}, {"window_end": "2027-01-01"}])
def test_config_validation(bad):
    with pytest.raises(ConfigError):
        make_config(**bad)


def test_round_trip_combinations_stay_in_window():
    s = make_config(nights={"min": 5, "max": 7}).searches[0]
    pairs = date_combinations(s, TODAY)
    assert all(r <= s.window_end for _, r in pairs)
    assert len(pairs) == 9 + 8 + 7  # 14-day window, 5/6/7 nights
    assert (date(2027, 1, 10), date(2027, 1, 15)) in pairs


def test_one_way_skips_past_dates():
    s = make_config(trip_type="one_way", window_start="2026-11-01").searches[0]
    pairs = date_combinations(s, TODAY)
    assert pairs[0] == (TODAY, None)
    assert len(pairs) == 7  # Nov 8..14


def test_run_search_ranks_filters_and_caps_requests():
    s = make_config(nights={"min": 3, "max": 7}, max_stops=0).searches[0]
    result = run_search(s, MockProvider(), TODAY, max_requests=10)
    assert result.requests_made == 10
    assert any("limit is 10" in e for e in result.errors)
    prices = [o.price for o in result.top]
    assert prices == sorted(prices) and len(prices) <= s.top_n
    assert all(o.stops == 0 for o in result.options)


class FailingProvider(FlightProvider):
    def search(self, request):
        raise ProviderError("quota exceeded")


def test_provider_errors_are_collected_not_raised():
    s = make_config().searches[0]
    result = run_search(s, FailingProvider(), TODAY)
    assert result.options == [] and len(result.errors) == result.requests_made == 7


def test_history_and_report(tmp_path):
    s = make_config().searches[0]
    result = run_search(s, MockProvider(), TODAY)
    history = History(tmp_path / "h.json")
    report = build_report([result], history, "Daily flight prices", TODAY)
    assert "JFK→LIS" in report.subject and "new" in report.text

    history.record(result, TODAY)
    history.save()
    reloaded = History(tmp_path / "h.json")
    tomorrow = date(2026, 11, 9)
    assert reloaded.previous_best(result, tomorrow) == result.best.price
    key = result.cheapest_by_dates()[0].date_key
    assert reloaded.previous_price_for(result, key, tomorrow) is not None
    report2 = build_report([result], reloaded, "Daily flight prices", tomorrow)
    assert "no change" in report2.text


def test_serpapi_params_and_parse():
    p = SerpApiProvider(api_key="k")
    req = SearchRequest("JFK", "LIS", date(2027, 1, 10), date(2027, 1, 17), max_stops=1)
    params = p.build_params(req)
    assert params["type"] == 1 and params["return_date"] == "2027-01-17" and params["stops"] == 2
    assert params["show_hidden"] == "true" and "deep_search" not in params
    payload = {
        "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x"},
        "price_insights": {"typical_price_range": [500, 700]},
        "best_flights": [{"price": 612, "total_duration": 445, "flights": [
            {"airline": "TAP Air Portugal", "flight_number": "TP 210",
             "departure_airport": {"id": "JFK", "time": "2027-01-10 19:00"},
             "arrival_airport": {"id": "LIS", "time": "2027-01-11 07:25"}}]}],
        "other_flights": [{"price": 540, "total_duration": 700, "flights": [{}, {}]},
                          {"total_duration": 1}],
    }
    opts = p.parse(payload, req)
    assert [o.price for o in opts] == [612, 540]
    assert opts[0].airlines == ["TAP Air Portugal"] and opts[0].stops == 0
    assert opts[1].stops == 1
    assert (opts[0].typical_low, opts[0].typical_high) == (500, 700)
    assert opts[0].booking_url.startswith("https://www.google.com/travel")


def test_duffel_body_and_parse():
    p = DuffelProvider(token="t")
    req = SearchRequest("JFK", "LIS", date(2027, 1, 10), date(2027, 1, 17), adults=2)
    body = p.build_body(req)["data"]
    assert len(body["slices"]) == 2 and len(body["passengers"]) == 2
    payload = {"data": {"offers": [{
        "total_amount": "1234.50", "total_currency": "EUR", "owner": {"name": "TAP"},
        "slices": [{"duration": "PT7H25M", "segments": [{
            "marketing_carrier": {"name": "TAP Air Portugal", "iata_code": "TP"},
            "marketing_carrier_flight_number": "210",
            "origin": {"iata_code": "JFK"}, "destination": {"iata_code": "LIS"},
            "departing_at": "2027-01-10T19:00:00", "arriving_at": "2027-01-11T07:25:00"}]}]}]}}
    [opt] = p.parse(payload, req)
    assert opt.price == 1234.5 and opt.currency == "EUR" and opt.duration_minutes == 445
    assert parse_iso_duration("P1DT2H") == 1560


def test_email_message():
    s = make_config().searches[0]
    report = build_report([run_search(s, MockProvider(), TODAY)], History(None), "Prices", TODAY)
    settings = SmtpSettings("smtp.example.com", 587, "me@example.com", "pw", "me@example.com",
                            ["a@example.com", "b@example.com"])
    msg = build_message(report, settings)
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg.is_multipart()



def test_google_flights_link_has_search_details():
    from urllib.parse import parse_qs, urlparse
    from airline_searcher.links import google_flights_link
    req = SearchRequest("JFK", "LIS", date(2027, 1, 10), date(2027, 1, 17), adults=2, max_stops=0)
    q = parse_qs(urlparse(google_flights_link(req)).query)["q"][0]
    assert q == "Flights from JFK to LIS on 2027-01-10 through 2027-01-17 for 2 adults nonstop"


def test_web_search_api(monkeypatch):
    from airline_searcher.web import search_api
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    status, body = search_api({"from": "jfk", "to": "LIS", "start": "2027-01-10", "days": "14",
                               "nights": "7", "stops": "1", "adults": "2"}, today=TODAY)
    assert status == 200 and body["searched"] == 7
    prices = [f["price"] for f in body["fares"]]
    assert prices == sorted(prices) and len(prices) > 10
    assert len(body["calendar"]) == 7 and body["insights"]["price_level"] in ("low", "typical", "high")
    assert body["insights"]["history"] and body["fares"][0]["depart_time"]
    for f in body["fares"]:
        assert f["vs_average"] == ("below" if f["price"] <= body["average"] else "above")
        assert f["booking_url"].startswith("https://www.google.com/travel/flights?")
        assert f["stops"] <= 1


def test_web_search_api_rejects_bad_input(monkeypatch):
    from airline_searcher.web import search_api
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    assert search_api({"from": "JFKX", "to": "LIS", "start": "2027-01-10"}, today=TODAY)[0] == 400
    assert search_api({"from": "JFK", "to": "LIS", "start": "2026-01-01", "days": "7"}, today=TODAY)[0] == 400


def test_web_search_api_without_key(monkeypatch):
    from airline_searcher.web import search_api
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    status, body = search_api({"from": "JFK", "to": "LIS", "start": "2027-01-10"}, today=TODAY)
    assert status == 503 and "SERPAPI_API_KEY" in body["error"]


def test_route_average_prefers_google_typical_range():
    s = make_config().searches[0]
    result = run_search(s, MockProvider(), TODAY)
    for o in result.options:
        o.typical_low, o.typical_high = 500, 700
    assert result.route_average() == (600, "typical")
    for o in result.options:
        o.typical_low = o.typical_high = None
    avg, source = result.route_average()
    assert source == "found" and avg == round(sum(o.price for o in result.options) / len(result.options), 2)


def test_web_search_api_caches_and_accepts_metro_codes(monkeypatch):
    from airline_searcher import web
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    web._cache.clear()
    q = {"from": "JFK,EWR,LGA", "to": "LIS", "start": "2027-01-10", "days": "7", "nights": "3"}
    status, first = web.search_api(q, today=TODAY)
    assert status == 200 and first["origin"] == "JFK,EWR,LGA" and first["cached"] is False
    status, second = web.search_api(q, today=TODAY)
    assert second["cached"] is True and second["fares"] == first["fares"]


def test_same_airport_both_sides_is_rejected():
    with pytest.raises(ConfigError):
        make_config(origin="JFK,EWR", destination="EWR")


def test_health_reports_demo_and_live(monkeypatch):
    from airline_searcher.web import health
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    assert health() == {"provider": "mock", "configured": True, "live": False}
    monkeypatch.setenv("SEARCH_PROVIDER", "serpapi")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "  k  ")
    assert health()["live"] is True
    monkeypatch.setenv("SERPAPI_KEY", "")
    assert "SERPAPI_KEY" in health()["help"]
    monkeypatch.delenv("SERPAPI_KEY")
    monkeypatch.setenv("SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    assert health()["live"] is True


def test_serpapi_parses_insights_logos_and_emissions():
    p = SerpApiProvider(api_key="k")
    req = SearchRequest("JFK", "LIS", date(2027, 1, 10), date(2027, 1, 17))
    payload = {
        "price_insights": {"lowest_price": 540, "price_level": "low", "typical_price_range": [600, 900],
                           "price_history": [[1767225600, 700], [1767312000, 650]]},
        "best_flights": [{"price": 540, "total_duration": 600, "airline_logo": "https://x/logo.png",
                          "layovers": [{"id": "MAD", "name": "Madrid", "duration": 90}],
                          "carbon_emissions": {"this_flight": 412000, "typical_for_this_route": 450000},
                          "flights": [{"airline": "Iberia"}, {"airline": "Iberia"}]}],
    }
    [opt] = p.parse(payload, req)
    assert opt.airline_logo == "https://x/logo.png" and opt.layovers == ["MAD"]
    assert (opt.co2_kg, opt.co2_typical_kg) == (412, 450)
    assert opt.insights["price_level"] == "low" and len(opt.insights["price_history"]) == 2


def test_vercel_entrypoint_serves_page_and_api(monkeypatch):
    """index.py's `handler` (Vercel's Python preset) serves the page, airport list and API."""
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from index import handler
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for path in ("/", "/web/index.html", "/airports.json", "/api/health"):
            with urllib.request.urlopen(base + path) as res:
                assert res.status == 200
    finally:
        server.shutdown()


def test_serpapi_leg_details_and_return_flights():
    class FakeResp:
        status_code = 200
        def __init__(self, data): self.data = data
        def json(self): return self.data

    class FakeSession:
        def __init__(self): self.calls = []
        def get(self, url, params, timeout):
            self.calls.append(params)
            return FakeResp({"other_flights": [{
                "price": 1772, "total_duration": 1700, "booking_token": "b",
                "layovers": [{"id": "GRU", "name": "Guarulhos", "duration": 230}],
                "flights": [{"airline": "LATAM", "flight_number": "LA 3553", "duration": 80,
                             "airplane": "Airbus A321", "legroom": "28 in", "overnight": True,
                             "plane_and_crew_by": "Latam Airlines Brasil",
                             "extensions": ["Wi-Fi for a fee", "Carbon emissions estimate: 66 kg"],
                             "departure_airport": {"id": "CNF", "name": "Confins", "time": "2027-02-17 18:20"},
                             "arrival_airport": {"id": "GRU", "name": "Guarulhos", "time": "2027-02-17 19:40"}}]}]})

    session = FakeSession()
    p = SerpApiProvider(api_key="k", session=session)
    req = SearchRequest("YYJ", "CNF", date(2027, 2, 3), date(2027, 2, 17), currency="CAD")
    [opt] = p.return_flights(req, "tok")
    assert session.calls[0]["departure_token"] == "tok" and session.calls[0]["show_hidden"] == "true"
    leg = opt.legs[0]
    assert (leg.duration_minutes, leg.airplane, leg.legroom, leg.overnight) == (80, "Airbus A321", "28 in", True)
    assert leg.operated_by == "Latam Airlines Brasil" and leg.extensions == ["Wi-Fi for a fee"]
    assert opt.layover_details == [{"code": "GRU", "name": "Guarulhos", "duration_minutes": 230, "overnight": False}]
    assert opt.booking_token == "b" and opt.price == 1772


def test_returns_api_needs_token_and_uses_cache(monkeypatch):
    from airline_searcher import web
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    assert web.returns_api({})[0] == 400
    status, body = web.returns_api({"token": "t", "from": "YYJ", "to": "CNF", "depart": "2027-02-03",
                                    "return": "2027-02-17"})
    assert status == 200 and body["fares"] and body["fares"][0]["legs"][0]["from"] == "CNF"


def test_errors_never_contain_the_api_key():
    import requests as rq
    from airline_searcher.providers.serpapi import redact

    class Boom:
        def get(self, url, params, timeout):
            raise rq.ConnectionError(f"Max retries exceeded with url: /search.json?api_key=SECRET123&x=1")

    p = SerpApiProvider(api_key="SECRET123", session=Boom())
    with pytest.raises(ProviderError) as err:
        p.search(SearchRequest("YYJ", "CNF", date(2027, 2, 15), None))
    assert "SECRET123" not in str(err.value)
    assert redact("bad url ...?api_key=abc&x=1") == "bad url ...?api_key=***&x=1"
    assert redact("key SECRET123 shown", "SECRET123") == "key *** shown"


def test_friendly_error_messages():
    from airline_searcher.web import friendly_error
    assert "run out of searches" in friendly_error(["SerpApi error: Your account has run out of searches."])
    assert "too long" in friendly_error(["SerpApi request timed out"])
    assert "Try again" in friendly_error(["something else"])


def test_account_status_checks_key_without_leaking_it(monkeypatch):
    from airline_searcher.providers import serpapi

    class Resp:
        def __init__(self, code, data): self.status_code, self.data = code, data
        def json(self): return self.data

    monkeypatch.setattr(serpapi.requests, "get", lambda url, params, timeout: Resp(200, {
        "api_key": params["api_key"], "account_email": "me@example.com", "plan_name": "Free Plan",
        "searches_per_month": 250, "total_searches_left": 183, "this_month_usage": 67}))
    ok = serpapi.account_status("SECRET")
    assert ok == {"ok": True, "plan": "Free Plan", "searches_left": 183, "searches_per_month": 250,
                  "used_this_month": 67}
    monkeypatch.setattr(serpapi.requests, "get", lambda url, params, timeout: Resp(401, {"error": "Invalid API key."}))
    bad = serpapi.account_status("SECRET")
    assert bad["ok"] is False and "SECRET" not in str(bad)


def test_clean_key_fixes_common_paste_mistakes():
    from airline_searcher.providers.serpapi import clean_key, describe_key
    k = "f" * 64
    for pasted in (k, f"  {k}\n", f'"{k}"', f"'{k}'", f"api_key={k}", f"https://serpapi.com/search?q=x&api_key={k}",
                   f"Your Private API Key: {k}"):
        assert clean_key(pasted) == k, pasted
    assert "64 letters" in describe_key(k) and k not in describe_key(k)
    assert "12 characters" in describe_key("abc123abc123")


def test_country_fresh_and_google_link_match_the_search():
    from urllib.parse import parse_qs, urlparse
    from airline_searcher.providers.serpapi import google_link
    p = SerpApiProvider(api_key="k")
    req = SearchRequest("YYJ", "CNF", date(2027, 2, 15), date(2027, 2, 22), currency="CAD", country="CA", fresh=True)
    params = p.build_params(req)
    assert params["gl"] == "ca" and params["no_cache"] == "true" and params["currency"] == "CAD"
    assert "gl" not in p.build_params(SearchRequest("YYJ", "CNF", date(2027, 2, 15), None))
    url = google_link("https://www.google.com/travel/flights?tfs=ABC&curr=USD&hl=pt", "CAD", "ca")
    q = parse_qs(urlparse(url).query)
    assert q["tfs"] == ["ABC"] and q["curr"] == ["CAD"] and q["gl"] == ["ca"] and q["hl"] == ["en"]


def test_booking_options_parse_together_and_separate_tickets():
    class Resp:
        status_code = 200
        def __init__(self, d): self.d = d
        def json(self): return self.d

    class Session:
        def __init__(self): self.params = None
        def get(self, url, params, timeout):
            self.params = params
            return Resp({"booking_options": [
                {"together": {"book_with": "Air Canada", "price": 1538, "option_title": "Standard",
                              "extensions": ["1 carry-on bag"], "baggage_prices": ["1st checked bag: 35 CAD"],
                              "booking_request": {"url": "https://www.google.com/travel/clk/f", "post_data": "u=abc"}}},
                {"departing": {"book_with": "Gol", "price": 700, "booking_request": {"url": "https://g/x", "post_data": "u=1"}},
                 "returning": {"book_with": "Azul", "price": 800, "booking_request": {"url": "javascript:bad"}}},
            ]})

    s = Session()
    p = SerpApiProvider(api_key="k", session=s)
    opts = p.booking_options(SearchRequest("YYJ", "CNF", date(2027, 2, 15), date(2027, 2, 22)), "tok")
    assert s.params["booking_token"] == "tok" and "show_hidden" not in s.params
    assert opts[0] == {"seller": "Air Canada", "price": 1538, "title": "Standard", "extensions": ["1 carry-on bag"],
                       "baggage": ["1st checked bag: 35 CAD"], "url": "https://www.google.com/travel/clk/f",
                       "post_data": "u=abc", "separate": None}
    assert [o["separate"] for o in opts[1:]] == ["departing", "returning"]
    assert opts[2]["url"] is None  # only https links are passed on


def test_booking_api_and_fresh_search(monkeypatch):
    from airline_searcher import web
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    web._cache.clear()
    q = {"from": "YYJ", "to": "CNF", "start": "2027-02-15", "days": "7", "trip": "one_way", "gl": "ca"}
    status, body = web.search_api(q, today=TODAY)
    token = body["fares"][0]["booking_token"]
    assert status == 200 and token
    assert web.search_api(q, today=TODAY)[1]["cached"] is True
    assert web.search_api({**q, "fresh": "1"}, today=TODAY)[1]["cached"] is False
    status, booking = web.booking_api({"booking_token": token, "from": "YYJ", "to": "CNF", "depart": "2027-02-15"})
    assert status == 200 and booking["options"][0]["price"] == body["fares"][0]["price"]
    assert web.booking_api({"from": "YYJ"})[0] == 400
