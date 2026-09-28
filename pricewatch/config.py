"""Загрузка и проверка config.yaml."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

NOTIFY_MODES = ("record_low", "any_drop")
IATA_RE = re.compile(r"^[A-Z]{3}$")


class ConfigError(Exception):
    """Ошибка конфигурации; str() содержит все найденные проблемы."""


@dataclass(frozen=True)
class Settings:
    notify_mode: str = "record_low"
    top_n: int = 5
    request_pause: float = 1.0          # секунд между запросами к API
    max_requests_per_watch: int = 60    # бюджет запросов на наблюдение за запуск


@dataclass(frozen=True)
class Watch:
    name: str
    origin: str
    destination: str
    departure_from: dt.date
    departure_to: dt.date
    trip_days_min: int
    trip_days_max: int
    direct: bool = False
    max_transfers: int | None = None   # максимум пересадок в каждую сторону; None — без ограничения
    max_price: int | None = None

    def params_hash(self) -> str:
        """Хеш параметров поиска. При его изменении мониторинг начинается заново.

        max_price не входит: это порог уведомлений, а не параметр поиска,
        его можно менять без повторного стартового сообщения.
        """
        payload = {
            "origin": self.origin,
            "destination": self.destination,
            "departure_from": self.departure_from.isoformat(),
            "departure_to": self.departure_to.isoformat(),
            "trip_days_min": self.trip_days_min,
            "trip_days_max": self.trip_days_max,
            "direct": self.direct,
        }
        # добавляется только если задан — чтобы не менять хеш существующих наблюдений
        if self.max_transfers is not None:
            payload["max_transfers"] = self.max_transfers
        raw = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class Config:
    settings: Settings
    watches: tuple[Watch, ...]

    def watch(self, name: str) -> Watch | None:
        return next((w for w in self.watches if w.name == name), None)


def _as_date(value, field: str, errors: list[str], where: str) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError:
            pass
    errors.append(f"{where}: {field} должен быть датой в формате ГГГГ-ММ-ДД, получено {value!r}")
    return None


def _as_int(value, field: str, errors: list[str], where: str, minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{where}: {field} должен быть целым числом, получено {value!r}")
        return None
    if value < minimum:
        errors.append(f"{where}: {field} должен быть >= {minimum}, получено {value}")
        return None
    return value


def _parse_settings(raw, errors: list[str]) -> Settings:
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        errors.append("settings должен быть словарём")
        return Settings()
    known = {"notify_mode", "top_n", "request_pause", "max_requests_per_watch"}
    for key in raw:
        if key not in known:
            errors.append(f"settings: неизвестный параметр {key!r}")
    mode = raw.get("notify_mode", "record_low")
    if mode not in NOTIFY_MODES:
        errors.append(f"settings.notify_mode должен быть одним из {NOTIFY_MODES}, получено {mode!r}")
        mode = "record_low"
    top_n = _as_int(raw.get("top_n", 5), "top_n", errors, "settings", 1) or 5
    if top_n > 20:
        errors.append("settings.top_n не больше 20 (ограничение длины сообщения)")
    pause = raw.get("request_pause", 1.0)
    if isinstance(pause, bool) or not isinstance(pause, (int, float)) or pause < 0:
        errors.append(f"settings.request_pause должен быть числом >= 0, получено {pause!r}")
        pause = 1.0
    budget = _as_int(raw.get("max_requests_per_watch", 60), "max_requests_per_watch",
                     errors, "settings", 1) or 60
    return Settings(notify_mode=mode, top_n=top_n, request_pause=float(pause),
                    max_requests_per_watch=budget)


def _parse_watch(raw, index: int, errors: list[str]) -> Watch | None:
    where = f"watches[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{where}: ожидается словарь с параметрами наблюдения")
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{where}: name обязателен и должен быть непустой строкой")
        return None
    name = name.strip()
    where = f"watches[{index}] ({name!r})"
    n_errors = len(errors)

    known = {"name", "origin", "destination", "departure_from", "departure_to",
             "trip_days_min", "trip_days_max", "direct", "max_transfers", "max_price"}
    for key in raw:
        if key not in known:
            errors.append(f"{where}: неизвестный параметр {key!r}")

    codes = {}
    for field in ("origin", "destination"):
        value = raw.get(field)
        code = value.strip().upper() if isinstance(value, str) else None
        if not code or not IATA_RE.match(code):
            errors.append(f"{where}: {field} должен быть IATA-кодом из 3 букв, получено {value!r}")
        codes[field] = code
    if codes["origin"] and codes["origin"] == codes["destination"]:
        errors.append(f"{where}: origin и destination совпадают")

    dep_from = _as_date(raw.get("departure_from"), "departure_from", errors, where)
    dep_to = _as_date(raw.get("departure_to"), "departure_to", errors, where)
    if dep_from and dep_to and dep_from > dep_to:
        errors.append(f"{where}: departure_from ({dep_from}) позже departure_to ({dep_to}) — даты перепутаны")
    if dep_from and dep_to and (dep_to - dep_from).days > 62:
        errors.append(f"{where}: диапазон вылета больше 62 дней — разбейте на несколько наблюдений")

    d_min = _as_int(raw.get("trip_days_min"), "trip_days_min", errors, where, 1)
    d_max = _as_int(raw.get("trip_days_max"), "trip_days_max", errors, where, 1)
    if d_min is not None and d_max is not None and d_min > d_max:
        errors.append(f"{where}: trip_days_min ({d_min}) больше trip_days_max ({d_max})")

    direct = raw.get("direct", False)
    if not isinstance(direct, bool):
        errors.append(f"{where}: direct должен быть true или false, получено {direct!r}")

    max_transfers = raw.get("max_transfers")
    if max_transfers is not None:
        max_transfers = _as_int(max_transfers, "max_transfers", errors, where, 0)

    max_price = raw.get("max_price")
    if max_price is not None:
        max_price = _as_int(max_price, "max_price", errors, where, 1)

    if len(errors) > n_errors:
        return None
    return Watch(name=name, origin=codes["origin"], destination=codes["destination"],
                 departure_from=dep_from, departure_to=dep_to,
                 trip_days_min=d_min, trip_days_max=d_max,
                 direct=direct, max_transfers=max_transfers, max_price=max_price)


def parse_config(data) -> Config:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ConfigError("config.yaml: ожидается словарь с ключами settings и watches")
    for key in data:
        if key not in ("settings", "watches"):
            errors.append(f"неизвестный раздел верхнего уровня {key!r}")
    settings = _parse_settings(data.get("settings"), errors)
    raw_watches = data.get("watches")
    if not isinstance(raw_watches, list) or not raw_watches:
        errors.append("watches должен быть непустым списком наблюдений")
        raw_watches = []

    watches: list[Watch] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_watches):
        watch = _parse_watch(raw, i, errors)
        if watch is None:
            continue
        if watch.name in seen:
            errors.append(f"watches[{i}]: имя {watch.name!r} уже используется — имена должны быть уникальны")
            continue
        seen.add(watch.name)
        watches.append(watch)

    if errors:
        raise ConfigError("Ошибки в конфигурации:\n" + "\n".join(f"  - {e}" for e in errors))
    return Config(settings=settings, watches=tuple(watches))


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: некорректный YAML: {exc}") from exc
    return parse_config(data)
