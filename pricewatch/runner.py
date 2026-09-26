"""Основной цикл: проверка наблюдений, уведомления, состояние, учёт падений."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from . import decide, messages
from .api import ApiError, AuthError, TravelpayoutsClient
from .config import Config, Watch
from .fetch import STRATEGIES, collect
from .messages import AirlineNamer
from .telegram import Notifier, TelegramError

log = logging.getLogger(__name__)

FAILURE_ALERT_AFTER = 3


@dataclass
class RunReport:
    history: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sent: int = 0


def _history_row(watch: Watch, outcome: decide.Outcome, found: int, now: dt.datetime) -> dict:
    best = outcome.best
    return {
        "checked_at": now.replace(microsecond=0).isoformat(),
        "name": watch.name,
        "min_price": best.price if best else None,
        "departure_date": best.departure_date.isoformat() if best else None,
        "return_date": best.return_date.isoformat() if best else None,
        "airline": best.airline if best else None,
        "offers_found": found,
    }


def _message_for(watch: Watch, outcome: decide.Outcome, names: AirlineNamer) -> str:
    if outcome.kind == decide.START:
        return messages.start_message(watch, outcome.top, names)
    if outcome.kind == decide.DROP:
        return messages.drop_message(watch, outcome.top, outcome.prev_price,
                                     outcome.entry["min_ever"]["price"], names)
    return messages.finish_message(watch, outcome.entry, names)


def check_watch(watch: Watch, config: Config, state: dict, client: TravelpayoutsClient,
                notifier: Notifier, names: AirlineNamer, now: dt.datetime,
                report: RunReport, strategy: str = "auto") -> None:
    watches = state["watches"]
    entry = watches.get(watch.name)

    finish = decide.evaluate_finish(watch, entry, now)
    if finish is not None:
        if finish.kind == decide.SKIP:
            log.info("%s: мониторинг завершён ранее, пропускаю", watch.name)
            return
        if finish.kind == decide.FINISH:
            notifier.send(_message_for(watch, finish, names))
            report.sent += 1
            log.info("%s: диапазон вылета прошёл — отправлено «Мониторинг завершён»", watch.name)
        else:
            log.info("%s: диапазон вылета прошёл, мониторинг не начинался — помечаю завершённым",
                     watch.name)
        watches[watch.name] = finish.entry
        return

    result = collect(client, watch, config.settings, now, strategy=strategy)
    outcome = decide.evaluate(watch, config.settings, entry, result.offers, now)
    report.history.append(_history_row(watch, outcome, len(result.offers), now))

    best = f"{outcome.best.price} ₽" if outcome.best else "—"
    log.info("%s: запросов %d (%s), записей %d, подходящих %d, лучшая цена %s → %s%s",
             watch.name, result.requests, "+".join(result.strategies), result.raw_count,
             len(result.offers), best, outcome.kind,
             " (параметры изменились, начинаю заново)" if outcome.restarted else "")
    if outcome.kind == decide.EMPTY:
        log.info("%s: подходящих вариантов в кэше нет", watch.name)

    if outcome.notify:
        # Состояние обновляем только после успешной отправки, чтобы не потерять уведомление
        notifier.send(_message_for(watch, outcome, names))
        report.sent += 1
    watches[watch.name] = outcome.entry


def run_watches(config: Config, state: dict, client: TravelpayoutsClient, notifier: Notifier,
                names: AirlineNamer, now: dt.datetime, strategy: str = "auto") -> RunReport:
    report = RunReport()
    auth_error: str | None = None
    for watch in config.watches:
        if auth_error:
            report.errors.append(f"{watch.name}: пропущено — {auth_error}")
            continue
        try:
            check_watch(watch, config, state, client, notifier, names, now, report, strategy)
        except AuthError as exc:
            auth_error = str(exc)
            report.errors.append(f"{watch.name}: {exc}")
            log.error("%s: %s", watch.name, exc)
        except (ApiError, TelegramError) as exc:
            report.errors.append(f"{watch.name}: {exc}")
            log.error("%s: %s", watch.name, exc)
        except Exception as exc:  # одно наблюдение не должно ломать остальные
            report.errors.append(f"{watch.name}: непредвиденная ошибка {type(exc).__name__}")
            log.exception("%s: непредвиденная ошибка", watch.name)
    return report


def record_run_result(state: dict, errors: list[str], notifier: Notifier | None,
                      now: dt.datetime) -> None:
    """Счётчик падений подряд: одно сообщение на третьем падении, одно — при восстановлении."""
    meta = state["meta"]
    meta["last_run_at"] = now.replace(microsecond=0).isoformat()
    if errors:
        meta["consecutive_failures"] = int(meta.get("consecutive_failures") or 0) + 1
        meta["last_errors"] = errors[:10]
        if meta["consecutive_failures"] >= FAILURE_ALERT_AFTER and not meta.get("failure_alert_sent"):
            if notifier is None:
                return
            try:
                notifier.send(messages.failure_message(meta["consecutive_failures"], errors))
                meta["failure_alert_sent"] = True
            except TelegramError as exc:
                log.error("Не удалось отправить сообщение об ошибке: %s", exc)
        return
    if meta.get("failure_alert_sent") and notifier is not None:
        try:
            notifier.send(messages.recovery_message())
        except TelegramError as exc:
            log.error("Не удалось отправить сообщение о восстановлении: %s", exc)
            return
    meta.update(consecutive_failures=0, failure_alert_sent=False, last_errors=[])


def compare_strategies(config: Config, client: TravelpayoutsClient, now: dt.datetime,
                       budget: int = 200) -> list[dict]:
    """Прогоняет каждую стратегию отдельно и возвращает сравнение покрытия."""
    from dataclasses import replace
    settings = replace(config.settings, max_requests_per_watch=budget)
    rows = []
    for watch in config.watches:
        if now.date() > watch.departure_to:
            continue
        for strategy in STRATEGIES:
            try:
                res = collect(client, watch, settings, now, strategy=strategy)
            except ApiError as exc:
                rows.append({"watch": watch.name, "strategy": strategy, "error": str(exc)})
                continue
            rows.append({
                "watch": watch.name,
                "strategy": strategy,
                "requests": res.requests,
                "raw": res.raw_count,
                "offers": len(res.offers),
                "dep_dates": len({o.departure_date for o in res.offers}),
                "date_pairs": len({(o.departure_date, o.return_date) for o in res.offers}),
                "min_price": res.offers[0].price if res.offers else None,
                "truncated": res.truncated,
            })
    return rows
