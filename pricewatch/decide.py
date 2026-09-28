"""Решения об уведомлениях без побочных эффектов.

Каждая функция получает текущую запись состояния наблюдения (или None)
и возвращает Outcome с новой записью. Записывать её — забота вызывающего,
и только после успешной отправки сообщения.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .config import Settings, Watch
from .offers import Offer

START = "start"            # первый запуск или смена параметров — стартовый топ
DROP = "drop"              # цена снизилась — уведомление
QUIET = "quiet"            # проверили, уведомлять не о чем
EMPTY = "empty"            # вариантов нет
FINISH = "finish"          # период прошёл — финальное сообщение
FINISH_SILENT = "finish_silent"  # период прошёл, но мониторинг не начинался
SKIP = "skip"              # наблюдение уже завершено

NOTIFY_KINDS = (START, DROP, FINISH)


@dataclass
class Outcome:
    kind: str
    entry: dict
    top: list[Offer] = field(default_factory=list)
    best: Offer | None = None
    prev_price: int | None = None
    restarted: bool = False    # параметры наблюдения поменялись

    @property
    def notify(self) -> bool:
        return self.kind in NOTIFY_KINDS

    @property
    def checked(self) -> bool:
        return self.kind in (START, DROP, QUIET, EMPTY)


def new_entry(watch: Watch) -> dict:
    return {
        "params_hash": watch.params_hash(),
        "status": "active",
        "started_at": None,
        "last_notified_price": None,
        "last_notified_at": None,
        "last_check_price": None,
        "last_checked_at": None,
        "min_ever": None,
    }


def _iso(now: dt.datetime) -> str:
    return now.replace(microsecond=0).isoformat()


def _offer_record(offer: Offer, now: dt.datetime) -> dict:
    return {
        "price": offer.price,
        "departure_date": offer.departure_date.isoformat(),
        "return_date": offer.return_date.isoformat(),
        "airline": offer.airline,
        "flight_number": offer.flight_number,
        "found_at": _iso(now),
    }


def evaluate_finish(watch: Watch, entry: dict | None, now: dt.datetime) -> Outcome | None:
    """Если диапазон вылета прошёл — решение о завершении, иначе None."""
    if now.date() <= watch.departure_to:
        return None
    same_params = entry is not None and entry.get("params_hash") == watch.params_hash()
    if same_params and entry.get("status") == "finished":
        return Outcome(SKIP, entry)
    if same_params and entry.get("started_at"):
        updated = dict(entry, status="finished", finished_at=_iso(now))
        return Outcome(FINISH, updated)
    base = dict(entry) if same_params else new_entry(watch)
    return Outcome(FINISH_SILENT, dict(base, status="finished", finished_at=_iso(now)))


def evaluate(watch: Watch, settings: Settings, entry: dict | None,
             offers: list[Offer], now: dt.datetime) -> Outcome:
    """Решение по результатам проверки. offers отсортированы по цене."""
    restarted = entry is not None and entry.get("params_hash") != watch.params_hash()
    if entry is None or restarted or entry.get("status") == "finished":
        entry = new_entry(watch)
    else:
        entry = dict(entry)
    entry["last_checked_at"] = _iso(now)
    top = offers[:settings.top_n]

    if not top:
        # Цену прошлой проверки не трогаем: пустой кэш — не «рост цены»
        return Outcome(EMPTY, entry, restarted=restarted)

    best = top[0]
    min_ever = entry.get("min_ever")
    if min_ever is None or best.price < min_ever["price"]:
        entry["min_ever"] = _offer_record(best, now)

    if not entry.get("started_at"):
        entry.update(started_at=_iso(now), last_notified_price=best.price,
                     last_notified_at=_iso(now), last_check_price=best.price)
        return Outcome(START, entry, top=top, best=best, restarted=restarted)

    if settings.notify_mode == "any_drop":
        prev = entry.get("last_check_price")
    else:
        prev = entry.get("last_notified_price")
    # Мелкие колебания кэша (десятки рублей) не считаем снижением
    dropped = prev is not None and best.price < prev and \
        prev - best.price >= settings.min_drop(prev)
    if watch.max_price is not None and best.price > watch.max_price:
        dropped = False

    entry["last_check_price"] = best.price
    if dropped:
        entry.update(last_notified_price=best.price, last_notified_at=_iso(now))
        return Outcome(DROP, entry, top=top, best=best, prev_price=prev)
    return Outcome(QUIET, entry, top=top, best=best, prev_price=prev)
