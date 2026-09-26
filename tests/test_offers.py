import datetime as dt

from pricewatch.offers import dedupe, is_expired, matches_watch, parse_offer, select_offers

from conftest import NOW, make_watch, offer, raw_offer

TODAY = NOW.date()


def test_parse_and_link():
    o = parse_offer(raw_offer(18900))
    assert o.price == 18900 and o.trip_days == 6
    assert o.url.startswith("https://www.aviasales.ru/search/")


def test_parse_rejects_broken():
    assert parse_offer({"price": 100}) is None
    assert parse_offer(dict(raw_offer(100), departure_at="garbage")) is None
    assert parse_offer(dict(raw_offer(100), price=None)) is None


def test_departure_range_bounds_inclusive():
    w = make_watch()
    assert matches_watch(offer(1, dep="2026-11-10", ret="2026-11-15"), w, TODAY)
    assert matches_watch(offer(1, dep="2026-11-20", ret="2026-11-25"), w, TODAY)
    assert not matches_watch(offer(1, dep="2026-11-09", ret="2026-11-15"), w, TODAY)
    assert not matches_watch(offer(1, dep="2026-11-21", ret="2026-11-27"), w, TODAY)


def test_trip_length_bounds_inclusive():
    w = make_watch()  # 5..9 дней
    assert not matches_watch(offer(1, dep="2026-11-12", ret="2026-11-16"), w, TODAY)  # 4
    assert matches_watch(offer(1, dep="2026-11-12", ret="2026-11-17"), w, TODAY)      # 5
    assert matches_watch(offer(1, dep="2026-11-12", ret="2026-11-21"), w, TODAY)      # 9
    assert not matches_watch(offer(1, dep="2026-11-12", ret="2026-11-22"), w, TODAY)  # 10


def test_trip_length_across_year():
    w = make_watch(departure_from=dt.date(2026, 12, 20), departure_to=dt.date(2026, 12, 30),
                   trip_days_min=7, trip_days_max=12)
    assert matches_watch(offer(1, dep="2026-12-28", ret="2027-01-08"), w, TODAY)      # 11
    assert not matches_watch(offer(1, dep="2026-12-28", ret="2027-01-10"), w, TODAY)  # 13


def test_departure_in_past_dropped():
    w = make_watch()
    today = dt.date(2026, 11, 15)
    assert not matches_watch(offer(1, dep="2026-11-14", ret="2026-11-20"), w, today)
    assert matches_watch(offer(1, dep="2026-11-15", ret="2026-11-21"), w, today)


def test_direct_only():
    w = make_watch(direct=True)
    assert not matches_watch(offer(1, transfers=1, return_transfers=0), w, TODAY)
    assert matches_watch(offer(1, transfers=0, return_transfers=0), w, TODAY)


def test_expired():
    assert is_expired(offer(1, expires="2026-09-30T00:00:00Z"), NOW)
    assert not is_expired(offer(1, expires="2026-10-05T00:00:00Z"), NOW)
    assert not is_expired(parse_offer(dict(raw_offer(1), expires_at=None)), NOW)


def test_dedupe_keeps_cheapest_same_dates_and_flight():
    a = offer(20000, flight="400")
    b = offer(18900, flight="400")
    c = offer(19000, flight="401")                     # другой рейс
    d = offer(17000, ret="2026-11-19", flight="400")   # другие даты
    result = dedupe([a, b, c, d])
    assert sorted(o.price for o in result) == [17000, 18900, 19000]


def test_select_pipeline_sorted_filtered():
    raws = [
        raw_offer(25000, dep="2026-11-14", ret="2026-11-20"),
        raw_offer(18000, dep="2026-11-01", ret="2026-11-07"),     # вне диапазона
        raw_offer(15000, expires="2026-09-01T00:00:00Z"),          # просрочен
        raw_offer(21000),
        raw_offer(20500),                                          # дубль, дешевле
        raw_offer(19000, dep="2026-11-15", ret="2026-11-26"),      # 11 дней
        {"broken": True},
    ]
    result = select_offers(raws, make_watch(), NOW)
    assert [o.price for o in result] == [20500, 25000]
