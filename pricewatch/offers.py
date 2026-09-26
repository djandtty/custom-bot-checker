"""Разбор, фильтрация, дедупликация и сортировка билетов."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from .config import Watch

log = logging.getLogger(__name__)

AVIASALES_URL = "https://www.aviasales.ru"


@dataclass(frozen=True)
class Offer:
    price: int
    airline: str
    flight_number: str
    departure_at: dt.datetime
    return_at: dt.datetime
    transfers: int
    return_transfers: int
    link: str
    expires_at: dt.datetime | None = None

    @property
    def departure_date(self) -> dt.date:
        return self.departure_at.date()

    @property
    def return_date(self) -> dt.date:
        return self.return_at.date()

    @property
    def trip_days(self) -> int:
        return (self.return_date - self.departure_date).days

    @property
    def url(self) -> str:
        if self.link.startswith("http"):
            return self.link
        return AVIASALES_URL + (self.link if self.link.startswith("/") else "/" + self.link)

    @property
    def dedupe_key(self) -> tuple:
        return (self.departure_date, self.return_date, self.airline, self.flight_number)


def _parse_dt(value) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_offer(raw: dict) -> Offer | None:
    """Превращает элемент data из ответа API в Offer; битые записи — None."""
    try:
        price = raw.get("price")
        dep = _parse_dt(raw.get("departure_at"))
        ret = _parse_dt(raw.get("return_at"))
        if dep is None or ret is None or isinstance(price, bool) or not isinstance(price, (int, float)):
            return None
        return Offer(
            price=int(round(price)),
            airline=str(raw.get("airline") or ""),
            flight_number=str(raw.get("flight_number") or ""),
            departure_at=dep,
            return_at=ret,
            transfers=int(raw.get("transfers") or 0),
            return_transfers=int(raw.get("return_transfers") or 0),
            link=str(raw.get("link") or ""),
            expires_at=_parse_dt(raw.get("expires_at")),
        )
    except (TypeError, ValueError):
        return None


def matches_watch(offer: Offer, watch: Watch, today: dt.date) -> bool:
    """Подходит ли билет под диапазон вылета, длительность и прямые рейсы."""
    if not (watch.departure_from <= offer.departure_date <= watch.departure_to):
        return False
    if offer.departure_date < today:
        return False
    if not (watch.trip_days_min <= offer.trip_days <= watch.trip_days_max):
        return False
    if watch.direct and (offer.transfers or offer.return_transfers):
        return False
    return True


def is_expired(offer: Offer, now: dt.datetime) -> bool:
    if offer.expires_at is None:
        return False
    expires = offer.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    return expires < now


def dedupe(offers: list[Offer]) -> list[Offer]:
    """Одинаковые даты + рейс — оставляем самый дешёвый."""
    best: dict[tuple, Offer] = {}
    for offer in offers:
        current = best.get(offer.dedupe_key)
        if current is None or offer.price < current.price:
            best[offer.dedupe_key] = offer
    return list(best.values())


def sort_offers(offers: list[Offer]) -> list[Offer]:
    return sorted(offers, key=lambda o: (o.price, o.departure_at, o.return_at, o.airline, o.flight_number))


def select_offers(raw_items: list[dict], watch: Watch, now: dt.datetime) -> list[Offer]:
    """Полный конвейер: разбор → фильтр → без просроченных → дедуп → сортировка."""
    today = now.date()
    parsed = [o for o in (parse_offer(r) for r in raw_items) if o is not None]
    matching = [o for o in parsed if matches_watch(o, watch, today)]
    fresh = [o for o in matching if not is_expired(o, now)]
    if len(fresh) < len(matching):
        log.info("%s: отброшено просроченных цен: %d", watch.name, len(matching) - len(fresh))
    return sort_offers(dedupe(fresh))
