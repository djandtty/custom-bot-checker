"""Стратегии запросов к API для наблюдения.

month      — пары «месяц вылета × месяц возврата» (ГГГГ-ММ), мало запросов;
day_month  — «конкретный день вылета × месяц возврата»;
pairs      — «конкретный день вылета × конкретный день возврата».

auto: сначала month; если подходящих вариантов меньше top_n — добавляем
day_month, затем pairs, пока не исчерпан бюджет запросов.

Для наблюдений с max_transfers: month отдаёт по одному (самому дешёвому) варианту
на пару дат, и если у него больше пересадок, чем разрешено, пара дат пропадает,
хотя подходящий рейс на эти даты может быть. Поэтому после month сразу
дозапрашиваем pairs — только для пар дат, оставшихся без подходящих вариантов.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from .api import BudgetExceeded, TravelpayoutsClient
from .config import Settings, Watch
from .offers import Offer, select_offers

log = logging.getLogger(__name__)

STRATEGIES = ("month", "day_month", "pairs")


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _next_month(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def _month_end(d: dt.date) -> dt.date:
    return _next_month(d) - dt.timedelta(days=1)


def _months(start: dt.date, end: dt.date) -> list[dt.date]:
    out, cur = [], _month_start(start)
    while cur <= end:
        out.append(cur)
        cur = _next_month(cur)
    return out


def _ym(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def departure_days(watch: Watch, today: dt.date) -> list[dt.date]:
    start = max(watch.departure_from, today)
    return [start + dt.timedelta(days=i) for i in range((watch.departure_to - start).days + 1)]


def month_queries(watch: Watch, today: dt.date) -> list[tuple[str, str]]:
    """Комбинации месяцев, в которых может найтись подходящая пара дат."""
    dep_start = max(watch.departure_from, today)
    if dep_start > watch.departure_to:
        return []
    ret_start = dep_start + dt.timedelta(days=watch.trip_days_min)
    ret_end = watch.departure_to + dt.timedelta(days=watch.trip_days_max)
    queries = []
    for dm in _months(dep_start, watch.departure_to):
        dep_lo, dep_hi = max(dm, dep_start), min(_month_end(dm), watch.departure_to)
        for rm in _months(ret_start, ret_end):
            ret_lo, ret_hi = max(rm, ret_start), min(_month_end(rm), ret_end)
            # есть ли в этих месяцах пара с подходящей длительностью
            if (ret_hi - dep_lo).days >= watch.trip_days_min and \
               (ret_lo - dep_hi).days <= watch.trip_days_max:
                queries.append((_ym(dm), _ym(rm)))
    return queries


def day_month_queries(watch: Watch, today: dt.date) -> list[tuple[str, str]]:
    queries = []
    for d in departure_days(watch, today):
        lo = d + dt.timedelta(days=watch.trip_days_min)
        hi = d + dt.timedelta(days=watch.trip_days_max)
        for rm in _months(lo, hi):
            queries.append((d.isoformat(), _ym(rm)))
    return queries


def pair_queries(watch: Watch, today: dt.date) -> list[tuple[str, str]]:
    queries = []
    for d in departure_days(watch, today):
        for k in range(watch.trip_days_min, watch.trip_days_max + 1):
            queries.append((d.isoformat(), (d + dt.timedelta(days=k)).isoformat()))
    return queries


QUERY_BUILDERS = {"month": month_queries, "day_month": day_month_queries, "pairs": pair_queries}


@dataclass
class FetchResult:
    offers: list[Offer]
    raw_count: int = 0
    requests: int = 0
    strategies: list[str] = field(default_factory=list)
    truncated: bool = False   # бюджет запросов закончился раньше, чем запросы


def _run_strategy(client: TravelpayoutsClient, watch: Watch, strategy: str,
                  today: dt.date, raw: list[dict], skip: set[tuple[str, str]]) -> bool:
    """Выполняет запросы стратегии, дописывая результаты в raw. False — если кончился бюджет."""
    for dep, ret in QUERY_BUILDERS[strategy](watch, today):
        if (dep, ret) in skip:
            continue
        try:
            items = client.prices_for_dates(watch.origin, watch.destination, dep, ret, watch.direct)
        except BudgetExceeded:
            return False
        log.debug("%s [%s] %s/%s: %d записей", watch.name, strategy, dep, ret, len(items))
        raw.extend(items)
    return True


def collect(client: TravelpayoutsClient, watch: Watch, settings: Settings,
            now: dt.datetime, strategy: str = "auto") -> FetchResult:
    """Собирает все подходящие билеты для наблюдения (отсортированы по цене)."""
    today = now.date()
    start_requests = client.requests_made
    client.budget = start_requests + settings.max_requests_per_watch
    raw: list[dict] = []
    result = FetchResult(offers=[])
    try:
        transfer_limited = watch.max_transfers is not None and not watch.direct
        if strategy != "auto":
            order: tuple[str, ...] = (strategy,)
        elif transfer_limited:
            order = ("month", "pairs")
        else:
            order = STRATEGIES
        for name in order:
            skip: set[tuple[str, str]] = set()
            if name == "pairs":
                # пары дат, по которым уже есть варианты, повторно не запрашиваем
                skip = {(o.departure_date.isoformat(), o.return_date.isoformat())
                        for o in result.offers}
            result.strategies.append(name)
            complete = _run_strategy(client, watch, name, today, raw, skip)
            result.offers = select_offers(raw, watch, now)
            if not complete:
                result.truncated = True
                log.warning("%s: исчерпан лимит запросов (%d), стратегия %s выполнена не полностью",
                            watch.name, settings.max_requests_per_watch, name)
                break
            if strategy == "auto" and not transfer_limited and len(result.offers) >= settings.top_n:
                break
    finally:
        client.budget = None
    result.raw_count = len(raw)
    result.requests = client.requests_made - start_requests
    return result
