import datetime as dt

import pytest
import requests

from pricewatch.api import PAGE_LIMIT, ApiError, AuthError, TravelpayoutsClient
from pricewatch.config import Settings
from pricewatch.fetch import collect, month_queries

from conftest import NOW, make_watch, raw_offer


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {"success": True, "data": []}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params))
        result = self.responder(params, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return result


def client_for(responder, **kw):
    sleeps = []
    session = FakeSession(responder)
    client = TravelpayoutsClient("secret", pause=0, session=session, sleep=sleeps.append, **kw)
    return client, session, sleeps


def test_token_in_header_and_params():
    client, session, _ = client_for(lambda p, n: FakeResponse())
    client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)
    p = session.calls[0]
    assert p["one_way"] == "false" and p["unique"] == "false" and p["currency"] == "rub"
    assert p["limit"] == 1000 and p["page"] == 1 and "token" not in p


def test_429_retry_then_success():
    def responder(p, n):
        return FakeResponse(429, headers={"Retry-After": "2"}) if n == 1 else \
            FakeResponse(body={"success": True, "data": [raw_offer(100)]})
    client, session, sleeps = client_for(responder)
    assert len(client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)) == 1
    assert sleeps == [2.0] and len(session.calls) == 2


def test_429_gives_up():
    client, session, _ = client_for(lambda p, n: FakeResponse(429))
    with pytest.raises(ApiError, match="429"):
        client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)
    assert len(session.calls) == 3


def test_401_no_retry():
    client, session, _ = client_for(lambda p, n: FakeResponse(401))
    with pytest.raises(AuthError):
        client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)
    assert len(session.calls) == 1


def test_network_error_retry_and_message_has_no_secret():
    client, session, _ = client_for(lambda p, n: requests.ConnectionError("boom secret"))
    with pytest.raises(ApiError) as exc:
        client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)
    assert "secret" not in str(exc.value) and len(session.calls) == 3


def test_empty_and_unsuccessful_responses():
    client, _, _ = client_for(lambda p, n: FakeResponse(body={"success": True, "data": []}))
    assert client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False) == []
    client, _, _ = client_for(lambda p, n: FakeResponse(body={"success": False, "error": "bad"}))
    with pytest.raises(ApiError, match="bad"):
        client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)


def test_pagination():
    def responder(p, n):
        size = PAGE_LIMIT if p["page"] == 1 else 3
        return FakeResponse(body={"success": True, "data": [raw_offer(100 + i) for i in range(size)]})
    client, session, _ = client_for(responder)
    assert len(client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)) == PAGE_LIMIT + 3
    assert [c["page"] for c in session.calls] == [1, 2]


def test_throttle_pause():
    t = [0.0]
    sleeps = []
    session = FakeSession(lambda p, n: FakeResponse())
    client = TravelpayoutsClient("x", pause=1.0, session=session,
                                 sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)),
                                 clock=lambda: t[0])
    for _ in range(3):
        client.prices_for_dates("MOW", "IST", "2026-11", "2026-11", False)
    assert sleeps == [1.0, 1.0]


def test_month_queries_cover_return_month_across_year():
    w = make_watch(departure_from=dt.date(2026, 12, 20), departure_to=dt.date(2026, 12, 30),
                   trip_days_min=7, trip_days_max=12)
    assert month_queries(w, NOW.date()) == [("2026-12", "2026-12"), ("2026-12", "2027-01")]
    # короткая поездка в начале месяца — возврат только в том же месяце
    w2 = make_watch(departure_from=dt.date(2026, 11, 1), departure_to=dt.date(2026, 11, 5),
                    trip_days_min=2, trip_days_max=3)
    assert month_queries(w2, NOW.date()) == [("2026-11", "2026-11")]


def test_auto_falls_back_when_month_is_sparse():
    w = make_watch()

    def responder(p, n):
        if len(p["departure_at"]) == 7:     # месячный запрос — один вариант
            return FakeResponse(body={"success": True, "data": [raw_offer(20000)]})
        if len(p["return_at"]) == 7:        # день × месяц — один и тот же дешёвый вариант
            return FakeResponse(body={"success": True, "data": [raw_offer(19000, flight="7")]})
        return FakeResponse()
    client, session, _ = client_for(responder)
    res = collect(client, w, Settings(top_n=5, max_requests_per_watch=100), NOW)
    assert res.strategies == ["month", "day_month", "pairs"]
    assert [o.price for o in res.offers] == [19000, 20000]
    assert res.requests == len(session.calls) == 1 + 11 + 55 - 1  # пара 12.11/18.11 уже есть


def test_auto_stops_when_month_is_enough():
    raws = [raw_offer(20000 + i, flight=str(i)) for i in range(6)]
    client, session, _ = client_for(lambda p, n: FakeResponse(body={"success": True, "data": raws}))
    res = collect(client, make_watch(), Settings(top_n=5), NOW)
    assert res.strategies == ["month"] and len(session.calls) == 1


def test_budget_limits_requests():
    client, session, _ = client_for(lambda p, n: FakeResponse())
    res = collect(client, make_watch(), Settings(top_n=5, max_requests_per_watch=10), NOW)
    assert len(session.calls) == 10 and res.truncated
