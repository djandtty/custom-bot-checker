import datetime as dt

import pytest

from pricewatch.config import ConfigError, parse_config

from conftest import make_watch

VALID = {
    "settings": {"notify_mode": "record_low", "top_n": 5},
    "watches": [{
        "name": "Стамбул ноябрь", "origin": "MOW", "destination": "IST",
        "departure_from": dt.date(2026, 11, 10), "departure_to": dt.date(2026, 11, 20),
        "trip_days_min": 5, "trip_days_max": 9, "direct": False, "max_price": None,
    }],
}


def _with_watch(**changes):
    data = {"settings": dict(VALID["settings"]), "watches": [dict(VALID["watches"][0], **changes)]}
    return data


def test_valid_config():
    cfg = parse_config(VALID)
    assert cfg.settings.top_n == 5
    w = cfg.watches[0]
    assert w.departure_from == dt.date(2026, 11, 10) and w.max_price is None


def test_string_dates_and_lowercase_codes():
    cfg = parse_config(_with_watch(departure_from="2026-11-10", origin="mow"))
    assert cfg.watches[0].departure_from == dt.date(2026, 11, 10)
    assert cfg.watches[0].origin == "MOW"


@pytest.mark.parametrize("changes, fragment", [
    ({"departure_from": dt.date(2026, 11, 25)}, "даты перепутаны"),
    ({"trip_days_min": 10}, "trip_days_min (10) больше trip_days_max (9)"),
    ({"origin": "MOSCOW"}, "IATA"),
    ({"destination": "MOW"}, "совпадают"),
    ({"departure_to": "20.11.2026"}, "ГГГГ-ММ-ДД"),
    ({"direct": "yes"}, "direct"),
    ({"max_price": -5}, "max_price"),
    ({"trip_days_max": 0}, "trip_days_max"),
    ({"unknown_key": 1}, "неизвестный параметр"),
])
def test_invalid_watch(changes, fragment):
    with pytest.raises(ConfigError) as exc:
        parse_config(_with_watch(**changes))
    assert fragment in str(exc.value)


def test_duplicate_names():
    data = {"watches": [VALID["watches"][0], dict(VALID["watches"][0])]}
    with pytest.raises(ConfigError, match="уже используется"):
        parse_config(data)


def test_bad_notify_mode_and_empty_watches():
    with pytest.raises(ConfigError) as exc:
        parse_config({"settings": {"notify_mode": "sometimes"}, "watches": []})
    assert "notify_mode" in str(exc.value) and "непустым списком" in str(exc.value)


def test_params_hash_changes_with_search_params_only():
    base = make_watch()
    assert base.params_hash() == make_watch().params_hash()
    assert base.params_hash() != make_watch(trip_days_max=10).params_hash()
    assert base.params_hash() != make_watch(departure_to=dt.date(2026, 11, 21)).params_hash()
    assert base.params_hash() != make_watch(direct=True).params_hash()
    # max_price — порог уведомлений, а не параметр поиска
    assert base.params_hash() == make_watch(max_price=20000).params_hash()


def test_repo_config_is_valid():
    from pathlib import Path
    from pricewatch.config import load_config
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert len(cfg.watches) >= 1


def test_max_transfers_parsing():
    assert parse_config(_with_watch(max_transfers=1)).watches[0].max_transfers == 1
    assert parse_config(_with_watch(max_transfers=0)).watches[0].max_transfers == 0
    assert parse_config(VALID).watches[0].max_transfers is None
    with pytest.raises(ConfigError, match="max_transfers"):
        parse_config(_with_watch(max_transfers=-1))
    with pytest.raises(ConfigError, match="max_transfers"):
        parse_config(_with_watch(max_transfers="one"))


def test_max_transfers_in_hash_only_when_set():
    # старые наблюдения без max_transfers сохраняют прежний хеш — без повторного старта
    assert make_watch(max_transfers=None).params_hash() == make_watch().params_hash()
    assert make_watch(max_transfers=1).params_hash() != make_watch().params_hash()
    assert make_watch(max_transfers=1).params_hash() != make_watch(max_transfers=2).params_hash()


def test_min_drop_settings():
    cfg = parse_config(dict(VALID, settings={"min_drop_rub": 500, "min_drop_pct": 1.5}))
    assert cfg.settings.min_drop_rub == 500 and cfg.settings.min_drop_pct == 1.5
    defaults = parse_config(VALID).settings
    assert defaults.min_drop_rub == 1000 and defaults.min_drop_pct == 2.0
    with pytest.raises(ConfigError, match="min_drop_pct"):
        parse_config(dict(VALID, settings={"min_drop_pct": 150}))
    with pytest.raises(ConfigError, match="min_drop_rub"):
        parse_config(dict(VALID, settings={"min_drop_rub": -1}))
