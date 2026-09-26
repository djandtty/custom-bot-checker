"""Отправка сообщений в Telegram. Токен не попадает в логи и тексты ошибок."""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class Notifier:
    """Интерфейс: send(text) отправляет HTML-сообщение или бросает TelegramError."""

    def send(self, text: str) -> None:  # pragma: no cover - интерфейс
        raise NotImplementedError


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._session = session or requests.Session()

    def send(self, text: str) -> None:
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        for attempt in range(3):
            try:
                resp = self._session.post(self._url, json=payload, timeout=30)
            except requests.RequestException as exc:
                # В тексте исключения requests есть URL с токеном — его не выводим
                error = f"сетевая ошибка Telegram: {type(exc).__name__}"
                if attempt == 2:
                    raise TelegramError(error) from None
                log.warning("%s, повтор", error)
                time.sleep(3)
                continue
            if resp.status_code == 200:
                return
            description = _description(resp)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < 2:
                    wait = _retry_after(resp) or 5
                    log.warning("Telegram HTTP %s, повтор через %s с", resp.status_code, wait)
                    time.sleep(wait)
                    continue
            if resp.status_code == 401:
                raise TelegramError("Telegram 401 — проверьте секрет TG_TOKEN")
            raise TelegramError(f"Telegram HTTP {resp.status_code}: {description}")
        raise TelegramError("Telegram: не удалось отправить сообщение")


class ConsoleNotifier(Notifier):
    """--dry-run: печатает сообщение вместо отправки."""

    def send(self, text: str) -> None:
        print("─" * 60)
        print(text)
        print("─" * 60, flush=True)


def _description(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("description", ""))[:200]
    except (ValueError, AttributeError):
        return resp.text[:200]


def _retry_after(resp: requests.Response) -> float | None:
    try:
        return float(resp.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
