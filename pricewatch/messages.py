"""Тексты сообщений для Telegram (parse_mode=HTML)."""

from __future__ import annotations

import datetime as dt
from html import escape
from typing import Callable

from .config import Watch
from .offers import Offer

NBSP = " "
MINUS = "−"
CACHE_WARNING = "⚠️ Цены из кэша — проверь на сайте перед покупкой."

AirlineNamer = Callable[[str], str]


def fmt_price(value: int) -> str:
    return f"{value:,}".replace(",", NBSP) + f"{NBSP}₽"


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d.%m")


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def fmt_transfers(n: int) -> str:
    return "прямой" if n == 0 else f"{n} пер."


def _link(url: str) -> str:
    return f'<a href="{escape(url, quote=True)}">🔗</a>'


def _offer_details(offer: Offer, airline_name: AirlineNamer) -> str:
    airline = airline_name(offer.airline) or offer.airline or "авиакомпания не указана"
    return (f"{fmt_date(offer.departure_date)} → {fmt_date(offer.return_date)} "
            f"({offer.trip_days} дн.), {escape(airline)}, "
            f"{fmt_transfers(offer.transfers)} / {fmt_transfers(offer.return_transfers)} — "
            f"{_link(offer.url)}")


def _offer_lines(offers: list[Offer], airline_name: AirlineNamer) -> list[str]:
    return [f"{i}) {fmt_price(o.price)} — {_offer_details(o, airline_name)}"
            for i, o in enumerate(offers, 1)]


def watch_summary(watch: Watch) -> str:
    if watch.trip_days_min == watch.trip_days_max:
        days = f"{watch.trip_days_min}"
    else:
        days = f"{watch.trip_days_min}–{watch.trip_days_max}"
    unit = plural(watch.trip_days_max, "день", "дня", "дней")
    line = (f"{escape(watch.origin)} → {escape(watch.destination)}, "
            f"вылет {fmt_date(watch.departure_from)}–{fmt_date(watch.departure_to)}, "
            f"поездка {days} {unit}")
    if watch.direct:
        line += ", только прямые"
    return line


def start_message(watch: Watch, top: list[Offer], airline_name: AirlineNamer) -> str:
    lines = [f"🟢 <b>Старт мониторинга: {escape(watch.name)}</b>", watch_summary(watch)]
    if watch.max_price is not None:
        lines.append(f"Уведомлю о снижении до {fmt_price(watch.max_price)} и ниже")
    lines += _offer_lines(top, airline_name)
    lines.append(CACHE_WARNING)
    return "\n".join(lines)


def drop_message(watch: Watch, top: list[Offer], prev_price: int, min_ever: int,
                 airline_name: AirlineNamer) -> str:
    best = top[0]
    diff = prev_price - best.price
    pct = round(diff / prev_price * 100) if prev_price else 0
    lines = [
        f"📉 <b>Цена снизилась: {escape(watch.name)}</b>",
        f"💰 <b>{fmt_price(best.price)}</b> (было {fmt_price(prev_price)}, "
        f"{MINUS}{fmt_price(diff)} / {MINUS}{pct}%)",
        f"Лучший: {_offer_details(best, airline_name)}",
        f"Текущий топ-{len(top)}:",
        *_offer_lines(top, airline_name),
        f"Минимум за всё время: {fmt_price(min_ever)}",
        CACHE_WARNING,
    ]
    return "\n".join(lines)


def finish_message(watch: Watch, entry: dict, airline_name: AirlineNamer) -> str:
    lines = [f"🏁 <b>Мониторинг завершён: {escape(watch.name)}</b>", watch_summary(watch)]
    best = entry.get("min_ever")
    if best:
        dep = dt.date.fromisoformat(best["departure_date"])
        ret = dt.date.fromisoformat(best["return_date"])
        airline = airline_name(best.get("airline", "")) or best.get("airline") or ""
        found = dt.datetime.fromisoformat(best["found_at"]).strftime("%d.%m.%Y")
        lines.append(f"Минимум за всё время: <b>{fmt_price(best['price'])}</b> — "
                     f"{fmt_date(dep)} → {fmt_date(ret)} ({(ret - dep).days} дн.), "
                     f"{escape(airline)} (найден {found})")
    else:
        lines.append("За время мониторинга подходящих цен не нашлось.")
    return "\n".join(lines)


def failure_message(failures: int, errors: list[str]) -> str:
    lines = [f"🚨 <b>Мониторинг цен падает {failures} запуска подряд</b>"]
    lines += [f"• {escape(e)}" for e in errors[:10]]
    lines.append("Подробности — в логах GitHub Actions. Следующее сообщение придёт, когда всё починится.")
    return "\n".join(lines)


def recovery_message() -> str:
    return "✅ <b>Мониторинг цен снова работает</b>"


def test_message(watch_names: list[str]) -> str:
    names = ", ".join(escape(n) for n in watch_names) or "—"
    return f"🧪 <b>Тестовое сообщение</b>\nБот мониторинга цен на связи.\nНаблюдения: {names}"
