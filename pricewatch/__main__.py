"""CLI: python -m pricewatch [--dry-run] [--test-telegram] [--reset NAME] [--compare-strategies]"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

from . import messages
from .airlines import AirlineNames
from .api import TravelpayoutsClient
from .config import Config, ConfigError, load_config
from .fetch import STRATEGIES
from .runner import compare_strategies, record_run_result, run_watches
from .state import append_history, load_state, save_state
from .telegram import ConsoleNotifier, Notifier, TelegramError, TelegramNotifier

log = logging.getLogger("pricewatch")

SECRET_VARS = ("TP_TOKEN", "TG_TOKEN", "TG_CHAT_ID")


class RedactSecrets(logging.Filter):
    """Страховка: вырезает значения секретов из любых строк лога."""

    def __init__(self):
        super().__init__()
        self._secrets = [v for v in (os.environ.get(k) for k in SECRET_VARS) if v and len(v) >= 4]

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            text = record.getMessage()
            for secret in self._secrets:
                text = text.replace(secret, "***")
            record.msg, record.args = text, None
        return True


def setup_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pricewatch", description="Мониторинг цен Aviasales → Telegram")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--state", default="state.json")
    p.add_argument("--history", default="history.csv")
    p.add_argument("--dry-run", action="store_true",
                   help="печатать сообщения вместо отправки; state.json и history.csv не меняются")
    p.add_argument("--test-telegram", action="store_true", help="отправить тестовое сообщение и выйти")
    p.add_argument("--reset", metavar="NAME", action="append",
                   help="сбросить состояние наблюдения (можно несколько раз); 'ALL' — все")
    p.add_argument("--compare-strategies", action="store_true",
                   help="сравнить покрытие стратегий запросов на реальном API и выйти")
    p.add_argument("--strategy", choices=("auto",) + STRATEGIES, default="auto",
                   help="стратегия запросов (по умолчанию auto)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _require_env(*names: str) -> None:
    missing = [n for n in names if not _env(n)]
    if missing:
        raise SystemExit(f"Не заданы переменные окружения: {', '.join(missing)}")


def make_notifier(dry_run: bool) -> Notifier:
    if dry_run:
        return ConsoleNotifier()
    _require_env("TG_TOKEN", "TG_CHAT_ID")
    return TelegramNotifier(_env("TG_TOKEN"), _env("TG_CHAT_ID"))


def cmd_reset(args, names: list[str]) -> int:
    state = load_state(args.state)
    try:
        config_names = {w.name for w in load_config(args.config).watches}
    except ConfigError:
        config_names = set()
    targets = list(state["watches"]) if "ALL" in names else names
    unknown = [n for n in targets if n not in state["watches"] and n not in config_names]
    if unknown:
        print(f"Нет наблюдения с именем: {', '.join(unknown)}", file=sys.stderr)
        return 2
    for name in targets:
        if state["watches"].pop(name, None) is not None:
            log.info("Состояние «%s» сброшено — при следующем запуске придёт стартовый топ", name)
        else:
            log.info("У «%s» нет сохранённого состояния — сбрасывать нечего", name)
    save_state(args.state, state)
    return 0


def cmd_test_telegram(args) -> int:
    try:
        watch_names = [w.name for w in load_config(args.config).watches]
    except ConfigError:
        watch_names = []
    try:
        make_notifier(args.dry_run).send(messages.test_message(watch_names))
    except TelegramError as exc:
        log.error("%s", exc)
        return 1
    log.info("Тестовое сообщение %s", "напечатано" if args.dry_run else "отправлено")
    return 0


def cmd_compare(config: Config) -> int:
    _require_env("TP_TOKEN")
    client = TravelpayoutsClient(_env("TP_TOKEN"), pause=config.settings.request_pause)
    rows = compare_strategies(config, client, dt.datetime.now(dt.timezone.utc))
    header = f"{'наблюдение':<20} {'стратегия':<10} {'запр.':>5} {'записей':>7} {'вариантов':>9} " \
             f"{'дат выл.':>8} {'пар дат':>7} {'мин. цена':>9}"
    print(header)
    print("-" * len(header))
    for r in rows:
        if "error" in r:
            print(f"{r['watch']:<20} {r['strategy']:<10} ошибка: {r['error']}")
            continue
        price = r["min_price"] if r["min_price"] is not None else "—"
        mark = " (лимит)" if r["truncated"] else ""
        print(f"{r['watch']:<20} {r['strategy']:<10} {r['requests']:>5} {r['raw']:>7} "
              f"{r['offers']:>9} {r['dep_dates']:>8} {r['date_pairs']:>7} {price:>9}{mark}")
    return 0


def cmd_run(args) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state(args.state)
    notifier = make_notifier(args.dry_run)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        if not args.dry_run:
            record_run_result(state, ["config.yaml: " + str(exc).splitlines()[0]], notifier, now)
            save_state(args.state, state)
        return 1
    _require_env("TP_TOKEN")

    client = TravelpayoutsClient(_env("TP_TOKEN"), pause=config.settings.request_pause)
    names = AirlineNames.load()
    report = run_watches(config, state, client, notifier, names, now, strategy=args.strategy)
    log.info("Итого: наблюдений %d, запросов к API %d, сообщений %d, ошибок %d",
             len(config.watches), client.requests_made, report.sent, len(report.errors))

    if args.dry_run:
        log.info("dry-run: state.json и history.csv не изменены")
    else:
        record_run_result(state, report.errors, notifier, now)
        save_state(args.state, state)
        append_history(args.history, report.history)
    for err in report.errors:
        log.error("Ошибка: %s", err)
    return 1 if report.errors else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    if args.reset:
        return cmd_reset(args, args.reset)
    if args.test_telegram:
        return cmd_test_telegram(args)
    if args.compare_strategies:
        try:
            return cmd_compare(load_config(args.config))
        except ConfigError as exc:
            log.error("%s", exc)
            return 1
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
