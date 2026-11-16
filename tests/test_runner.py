import datetime as dt
import json

import pytest

from pricewatch import __main__ as cli
from pricewatch.api import AuthError
from pricewatch.config import Config, Settings
from pricewatch.fetch import FetchResult
from pricewatch.runner import record_run_result, run_watches
from pricewatch.state import empty_state
from pricewatch.telegram import TelegramError

from conftest import NOW, make_watch, offer


class Recorder:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, text, silent=False):
        if self.fail:
            raise TelegramError("nope")
        self.sent.append(text)


@pytest.fixture
def fake_collect(monkeypatch):
    prices = {}

    def _collect(client, watch, settings, now, strategy="auto"):
        value = prices.get(watch.name)
        if isinstance(value, Exception):
            raise value
        offers = [offer(p, flight=str(i)) for i, p in enumerate(sorted(value or []))]
        return FetchResult(offers=offers, raw_count=len(offers), requests=1, strategies=["month"])
    monkeypatch.setattr("pricewatch.runner.collect", _collect)
    return prices


def cfg(*watches):
    return Config(settings=Settings(top_n=5), watches=tuple(watches))


def test_full_cycle(fake_collect):
    w = make_watch()
    config, state, tg = cfg(w), empty_state(), Recorder()
    fake_collect[w.name] = [20000, 21000]
    report = run_watches(config, state, None, tg, lambda c: c, NOW)
    assert len(tg.sent) == 1 and "Старт мониторинга" in tg.sent[0]
    assert report.history[0]["min_price"] == 20000

    report = run_watches(config, state, None, tg, lambda c: c, NOW)
    assert len(tg.sent) == 1                                        # без изменений — тишина

    fake_collect[w.name] = [18000]
    run_watches(config, state, None, tg, lambda c: c, NOW)
    assert len(tg.sent) == 2 and "Цена снизилась" in tg.sent[1]

    fake_collect[w.name] = []
    report = run_watches(config, state, None, tg, lambda c: c, NOW)
    assert len(tg.sent) == 2 and report.history[0]["min_price"] is None and not report.errors

    after = dt.datetime(2026, 11, 21, 1, tzinfo=dt.timezone.utc)
    run_watches(config, state, None, tg, lambda c: c, after)
    run_watches(config, state, None, tg, lambda c: c, after)
    assert len(tg.sent) == 3 and "Мониторинг завершён" in tg.sent[2] and "18" in tg.sent[2]


def test_one_watch_error_does_not_break_others(fake_collect):
    a, b = make_watch(name="A"), make_watch(name="B")
    fake_collect["A"] = RuntimeError("boom")
    fake_collect["B"] = [20000]
    state, tg = empty_state(), Recorder()
    report = run_watches(cfg(a, b), state, None, tg, lambda c: c, NOW)
    assert len(report.errors) == 1 and report.errors[0].startswith("A:")
    assert "B" in state["watches"] and len(tg.sent) == 1


def test_auth_error_skips_remaining(fake_collect):
    a, b = make_watch(name="A"), make_watch(name="B")
    fake_collect["A"] = AuthError("401")
    fake_collect["B"] = [20000]
    report = run_watches(cfg(a, b), empty_state(), None, Recorder(), lambda c: c, NOW)
    assert len(report.errors) == 2 and "пропущено" in report.errors[1]


def test_telegram_failure_keeps_state_for_retry(fake_collect):
    w = make_watch()
    fake_collect[w.name] = [20000]
    state = empty_state()
    report = run_watches(cfg(w), state, None, Recorder(fail=True), lambda c: c, NOW)
    assert report.errors and w.name not in state["watches"]
    tg = Recorder()
    run_watches(cfg(w), state, None, tg, lambda c: c, NOW)
    assert "Старт мониторинга" in tg.sent[0]


def test_failure_alert_after_three_and_recovery():
    state, tg = empty_state(), Recorder()
    for _ in range(2):
        record_run_result(state, ["x: err"], tg, NOW)
    assert tg.sent == []
    record_run_result(state, ["x: err"], tg, NOW)
    assert len(tg.sent) == 1 and "3 запуска подряд" in tg.sent[0]
    record_run_result(state, ["x: err"], tg, NOW)
    assert len(tg.sent) == 1                                          # не спамим
    record_run_result(state, [], tg, NOW)
    assert len(tg.sent) == 2 and "снова работает" in tg.sent[1]
    assert state["meta"]["consecutive_failures"] == 0


def test_success_resets_counter_without_message():
    state, tg = empty_state(), Recorder()
    record_run_result(state, ["e"], tg, NOW)
    record_run_result(state, [], tg, NOW)
    record_run_result(state, ["e"], tg, NOW)
    record_run_result(state, ["e"], tg, NOW)
    assert tg.sent == [] and state["meta"]["consecutive_failures"] == 2


def test_cli_reset(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state = empty_state()
    state["watches"] = {"Стамбул ноябрь": {"params_hash": "x"}, "Дубай": {"params_hash": "y"}}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    args = ["--state", str(state_file), "--config", "config.yaml"]
    monkeypatch.chdir(__import__("pathlib").Path(__file__).resolve().parent.parent)
    assert cli.main(args + ["--reset", "Стамбул ноябрь"]) == 0
    assert list(json.loads(state_file.read_text())["watches"]) == ["Дубай"]
    assert cli.main(args + ["--reset", "Нет такого"]) == 2
    assert cli.main(args + ["--reset", "ALL"]) == 0
    assert json.loads(state_file.read_text())["watches"] == {}


def test_cli_dry_run_does_not_write(tmp_path, monkeypatch, fake_collect, capsys):
    monkeypatch.setenv("TP_TOKEN", "tp-secret-value")
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.setattr("pricewatch.__main__.AirlineNames.load", classmethod(lambda cls: cls()))
    fake_collect["Стамбул ноябрь"] = [18900]
    config = tmp_path / "config.yaml"
    config.write_text("""watches:
  - name: "Стамбул ноябрь"
    origin: MOW
    destination: IST
    departure_from: 2026-11-10
    departure_to: 2026-11-20
    trip_days_min: 5
    trip_days_max: 9
""", encoding="utf-8")
    state_file, history = tmp_path / "state.json", tmp_path / "history.csv"
    code = cli.main(["--dry-run", "--config", str(config),
                     "--state", str(state_file), "--history", str(history)])
    out = capsys.readouterr().out
    assert code == 0 and "Старт мониторинга: Стамбул ноябрь" in out
    assert not state_file.exists() and not history.exists()
    assert "tp-secret-value" not in out


# --- ежедневная отбивка -------------------------------------------------------

from pricewatch.runner import RunReport, maybe_send_heartbeat, track_recent_runs  # noqa: E402


class SilentRecorder:
    def __init__(self):
        self.sent = []

    def send(self, text, silent=False):
        self.sent.append((text, silent))


def _report(**best):
    r = RunReport()
    r.best = best
    return r


def _at(msk_hour, day=16, minute=27):
    # время по Москве → UTC
    return dt.datetime(2026, 11, day, msk_hour, minute, tzinfo=dt.timezone.utc) - dt.timedelta(hours=3)


def test_heartbeat_once_per_day_after_hour():
    config, state, tg = cfg(make_watch()), empty_state(), SilentRecorder()
    report = _report(**{"Токио 05.04–09.04": offer(58569, airline="CA"),
                        "Осака 12.03–14.03": offer(69669, airline="HU")})
    assert not maybe_send_heartbeat(state, report, config, tg, lambda c: c, _at(8, minute=59))
    track_recent_runs(state, _at(9, minute=27))
    assert maybe_send_heartbeat(state, report, config, tg, lambda c: c, _at(9, minute=27))
    text, silent = tg.sent[0]
    assert silent is True
    assert "✅ <b>Бот работает</b>" in text
    assert "58 569 ₽" in text and "Токио 05.04–09.04" in text and "69 669" not in text
    assert "последняя в 09:27 МСК" in text
    # повторно в тот же день — нет, на следующий день — да
    assert not maybe_send_heartbeat(state, report, config, tg, lambda c: c, _at(15))
    assert maybe_send_heartbeat(state, report, config, tg, lambda c: c, _at(10, day=17))
    assert len(tg.sent) == 2


def test_heartbeat_disabled_and_without_offers():
    from dataclasses import replace
    config = cfg(make_watch())
    off = Config(settings=replace(config.settings, heartbeat_hour=None), watches=config.watches)
    tg = SilentRecorder()
    assert not maybe_send_heartbeat(empty_state(), _report(x=offer(1)), off, tg, lambda c: c, _at(12))
    assert maybe_send_heartbeat(empty_state(), RunReport(), config, tg, lambda c: c, _at(12))
    assert "в кэше сейчас нет" in tg.sent[0][0]


def test_heartbeat_mentions_errors():
    tg, report = SilentRecorder(), _report(a=offer(50000))
    report.errors = ["a: boom", "b: boom"]
    maybe_send_heartbeat(empty_state(), report, cfg(make_watch()), tg, lambda c: c, _at(12))
    assert "Ошибок в последней проверке: 2" in tg.sent[0][0]


def test_heartbeat_telegram_failure_retries_next_run():
    state = empty_state()
    report = _report(a=offer(50000))
    assert not maybe_send_heartbeat(state, report, cfg(make_watch()), Recorder(fail=True),
                                    lambda c: c, _at(12))
    assert "heartbeat_sent_date" not in state["meta"]


def test_recent_runs_window():
    state = empty_state()
    base = _at(9)
    for h in (0, 7, 15, 23):
        track_recent_runs(state, base + dt.timedelta(hours=h))
    # через 25 часов после первого: первый и второй (7 ч) выпали из окна 24 ч
    assert track_recent_runs(state, base + dt.timedelta(hours=31, minutes=30)) == 3
