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
    assert any("max_requests_per_search" in e for e in result.errors)
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
    payload = {
        "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x"},
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


def test_json_export_matches_email_data(tmp_path):
    from airline_searcher.export import build_payload, write_json
    s = make_config().searches[0]
    result = run_search(s, MockProvider(), TODAY)
    history = History(tmp_path / "h.json")
    history.record(result, date(2026, 11, 7))  # pretend yesterday's run
    payload = build_payload([result], history, "mock", TODAY)
    write_json(payload, tmp_path / "out" / "latest.json")
    entry = payload["searches"][0]
    assert entry["best"]["price"] == result.best.price
    assert [o["price"] for o in entry["top"]] == [o.price for o in result.top]
    assert len(entry["by_date"]) == len(result.cheapest_by_dates())
    assert entry["previous_best"] == result.best.price
    assert all("previous_price" in o for o in entry["by_date"])
    assert [r["date"] for r in entry["best_price_history"]] == ["2026-11-07", "2026-11-08"]
    assert (tmp_path / "out" / "latest.json").exists()
