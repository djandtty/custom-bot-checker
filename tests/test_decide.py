import datetime as dt

from pricewatch import decide
from pricewatch.config import Settings

from conftest import NOW, make_watch, offer

LATER = NOW + dt.timedelta(hours=2)


def run(watch, settings, entry, prices, now=NOW):
    offers = [offer(p, flight=str(i)) for i, p in enumerate(sorted(prices))]
    return decide.evaluate(watch, settings, entry, offers, now)


def test_first_run_sends_start_with_top_n(settings):
    w = make_watch()
    out = run(w, settings, None, [30000, 20000, 25000, 21000, 22000, 23000, 24000])
    assert out.kind == decide.START
    assert [o.price for o in out.top] == [20000, 21000, 22000, 23000, 24000]
    e = out.entry
    assert e["params_hash"] == w.params_hash()
    assert e["last_notified_price"] == 20000 and e["last_check_price"] == 20000
    assert e["min_ever"]["price"] == 20000 and e["min_ever"]["departure_date"] == "2026-11-12"


def test_start_ignores_max_price(settings):
    out = run(make_watch(max_price=10000), settings, None, [20000])
    assert out.kind == decide.START


def test_no_offers_first_run_postpones_start(settings):
    w = make_watch()
    out = run(w, settings, None, [])
    assert out.kind == decide.EMPTY and not out.notify
    assert out.entry["started_at"] is None
    assert run(w, settings, out.entry, [19000], LATER).kind == decide.START


def test_no_drop_is_quiet(settings):
    w = make_watch()
    e = run(w, settings, None, [20000]).entry
    out = run(w, settings, e, [20000], LATER)
    assert out.kind == decide.QUIET and not out.notify
    out = run(w, settings, e, [21000], LATER)
    assert out.kind == decide.QUIET


def test_drop_notifies_and_updates(settings):
    w = make_watch()
    e = run(w, settings, None, [18900]).entry
    out = run(w, settings, e, [17400, 19000], LATER)
    assert out.kind == decide.DROP and out.prev_price == 18900
    assert out.entry["last_notified_price"] == 17400
    assert out.entry["min_ever"]["price"] == 17400


def test_record_low_vs_any_drop():
    w = make_watch()
    record = Settings(notify_mode="record_low")
    anyd = Settings(notify_mode="any_drop")
    for s in (record, anyd):
        e = run(w, s, None, [20000]).entry                 # старт, уведомлено 20000
        e = run(w, s, e, [25000], LATER).entry             # рост, тишина
        out = run(w, s, e, [22000], LATER)                 # снижение 25000 → 22000
        if s is record:
            assert out.kind == decide.QUIET                # не ниже 20000
        else:
            assert out.kind == decide.DROP and out.prev_price == 25000


def test_record_low_same_price_not_repeated(settings):
    w = make_watch()
    e = run(w, settings, None, [20000]).entry
    e = run(w, settings, e, [19000], LATER).entry
    assert run(w, settings, e, [19000], LATER).kind == decide.QUIET


def test_max_price_gates_drop():
    s = Settings(notify_mode="any_drop")
    w = make_watch(max_price=22000)
    e = run(w, s, None, [30000]).entry
    out = run(w, s, e, [25000], LATER)
    assert out.kind == decide.QUIET                        # снизилась, но выше потолка
    out = run(w, s, out.entry, [21000], LATER)
    assert out.kind == decide.DROP and out.prev_price == 25000


def test_empty_result_keeps_last_check_price():
    s = Settings(notify_mode="any_drop")
    w = make_watch()
    e = run(w, s, None, [20000]).entry
    e = run(w, s, e, [], LATER).entry
    assert e["last_check_price"] == 20000
    assert run(w, s, e, [20000], LATER).kind == decide.QUIET


def test_params_change_restarts(settings):
    w = make_watch()
    e = run(w, settings, None, [15000]).entry
    changed = make_watch(trip_days_max=12)
    out = run(changed, settings, e, [20000], LATER)
    assert out.kind == decide.START and out.restarted
    assert out.entry["params_hash"] == changed.params_hash()
    assert out.entry["min_ever"]["price"] == 20000         # минимум старых параметров сброшен


def test_max_price_change_does_not_restart(settings):
    e = run(make_watch(), settings, None, [20000]).entry
    out = run(make_watch(max_price=18000), settings, e, [20000], LATER)
    assert out.kind == decide.QUIET


def test_finish_once():
    w = make_watch()
    s = Settings()
    e = run(w, s, None, [20000]).entry
    after = dt.datetime(2026, 11, 21, 1, tzinfo=dt.timezone.utc)
    assert decide.evaluate_finish(w, e, NOW) is None
    out = decide.evaluate_finish(w, e, after)
    assert out.kind == decide.FINISH and out.entry["status"] == "finished"
    assert decide.evaluate_finish(w, out.entry, after).kind == decide.SKIP


def test_finish_without_start_is_silent():
    w = make_watch()
    after = dt.datetime(2026, 11, 21, 1, tzinfo=dt.timezone.utc)
    out = decide.evaluate_finish(w, None, after)
    assert out.kind == decide.FINISH_SILENT and not out.notify
    assert decide.evaluate_finish(w, out.entry, after).kind == decide.SKIP


def test_finished_watch_with_new_dates_restarts(settings):
    w = make_watch()
    after = dt.datetime(2026, 11, 21, 1, tzinfo=dt.timezone.utc)
    e = decide.evaluate_finish(w, run(w, settings, None, [20000]).entry, after).entry
    moved = make_watch(departure_from=dt.date(2026, 12, 1), departure_to=dt.date(2026, 12, 10))
    assert decide.evaluate_finish(moved, e, after) is None
    out = decide.evaluate(moved, settings, e,
                          [offer(19000, dep="2026-12-02", ret="2026-12-08")], after)
    assert out.kind == decide.START and out.entry["status"] == "active"


def test_min_drop_threshold_ignores_small_fluctuations():
    s = Settings(notify_mode="record_low", min_drop_rub=1000, min_drop_pct=2)
    w = make_watch()
    e = run(w, s, None, [70656]).entry
    # −24 ₽ — шум кэша, уведомления нет; порог = max(1000, 2% от 70 656 = 1413,12)
    out = run(w, s, e, [70632], LATER)
    assert out.kind == decide.QUIET and out.entry["last_notified_price"] == 70656
    assert out.entry["last_check_price"] == 70632
    assert run(w, s, out.entry, [69300], LATER).kind == decide.QUIET   # −1 356 < 1 413
    out = run(w, s, out.entry, [69242], LATER)                          # −1 414 ≥ 1 413,12
    assert out.kind == decide.DROP and out.prev_price == 70656


def test_min_drop_accumulates_in_record_low():
    s = Settings(notify_mode="record_low", min_drop_rub=1000, min_drop_pct=0)
    w = make_watch()
    e = run(w, s, None, [20000]).entry
    for price in (19700, 19400, 19100):          # по 300 ₽ — тишина
        out = run(w, s, e, [price], LATER)
        assert out.kind == decide.QUIET
        e = out.entry
    out = run(w, s, e, [19000], LATER)           # всего −1 000 от последнего уведомления
    assert out.kind == decide.DROP and out.prev_price == 20000


def test_min_drop_rub_is_used_when_larger_than_pct():
    s = Settings(min_drop_rub=1000, min_drop_pct=2)   # 2% от 20 000 = 400 < 1000
    assert s.min_drop(20000) == 1000
    assert s.min_drop(100000) == 2000


def test_zero_threshold_still_requires_real_drop():
    s = Settings(min_drop_rub=0, min_drop_pct=0)
    w = make_watch()
    e = run(w, s, None, [20000]).entry
    assert run(w, s, e, [20000], LATER).kind == decide.QUIET
    assert run(w, s, e, [19999], LATER).kind == decide.DROP
