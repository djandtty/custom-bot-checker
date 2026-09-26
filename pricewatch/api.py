"""Клиент Aviasales Data API (Travelpayouts) с повторами и паузой между запросами."""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
PAGE_LIMIT = 1000          # максимум, который принимает API
MAX_PAGES = 5              # страховка от бесконечной пагинации
RATE_LIMIT_WAITS = (5, 15)  # паузы при 429, секунд
NETWORK_WAITS = (3, 10)     # паузы при сетевых ошибках и 5xx


class ApiError(Exception):
    """Ошибка API, после которой повторять запрос бессмысленно."""


class AuthError(ApiError):
    """401: неверный или отсутствующий TP_TOKEN."""


class BudgetExceeded(Exception):
    """Лимит запросов на наблюдение исчерпан."""


class TravelpayoutsClient:
    def __init__(self, token: str, pause: float = 1.0,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._token = token
        self._pause = pause
        self._session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self.requests_made = 0
        self.budget: int | None = None   # None — без лимита

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            wait = self._pause - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _get(self, params: dict) -> dict:
        rate_waits = list(RATE_LIMIT_WAITS)
        net_waits = list(NETWORK_WAITS)
        while True:
            if self.budget is not None and self.requests_made >= self.budget:
                raise BudgetExceeded()
            self._throttle()
            self.requests_made += 1
            try:
                resp = self._session.get(PRICES_URL, params=params, timeout=30,
                                         headers={"X-Access-Token": self._token,
                                                  "Accept-Encoding": "gzip"})
            except requests.RequestException as exc:
                if not net_waits:
                    raise ApiError(f"сетевая ошибка: {type(exc).__name__}") from None
                wait = net_waits.pop(0)
                log.warning("Сетевая ошибка (%s), повтор через %s с", type(exc).__name__, wait)
                self._sleep(wait)
                continue

            if resp.status_code == 401:
                raise AuthError("401 Unauthorized — проверьте секрет TP_TOKEN")
            if resp.status_code == 429:
                if not rate_waits:
                    raise ApiError("429 Too Many Requests — лимит API исчерпан")
                wait = _retry_after(resp) or rate_waits[0]
                rate_waits.pop(0)
                log.warning("429 от API, повтор через %s с", wait)
                self._sleep(wait)
                continue
            if resp.status_code >= 500:
                if not net_waits:
                    raise ApiError(f"ошибка сервера API: HTTP {resp.status_code}")
                wait = net_waits.pop(0)
                log.warning("HTTP %s от API, повтор через %s с", resp.status_code, wait)
                self._sleep(wait)
                continue
            if resp.status_code != 200:
                raise ApiError(f"HTTP {resp.status_code}: {_error_text(resp)}")

            try:
                body = resp.json()
            except ValueError:
                raise ApiError("API вернул не JSON") from None
            if not isinstance(body, dict):
                raise ApiError("неожиданный формат ответа API")
            if body.get("success") is False:
                raise ApiError(f"API вернул ошибку: {body.get('error') or 'без описания'}")
            return body

    def prices_for_dates(self, origin: str, destination: str, departure_at: str,
                         return_at: str, direct: bool) -> list[dict]:
        """Все билеты туда-обратно для пары дат (ГГГГ-ММ или ГГГГ-ММ-ДД), со всех страниц."""
        results: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params = {
                "origin": origin,
                "destination": destination,
                "departure_at": departure_at,
                "return_at": return_at,
                "one_way": "false",
                "direct": "true" if direct else "false",
                "currency": "rub",
                "market": "ru",
                "sorting": "price",
                "unique": "false",
                "limit": PAGE_LIMIT,
                "page": page,
            }
            data = self._get(params).get("data") or []
            if not isinstance(data, list):
                raise ApiError("поле data в ответе API не является списком")
            results.extend(d for d in data if isinstance(d, dict))
            if len(data) < PAGE_LIMIT:
                break
        return results


def _retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return min(float(value), 60.0) if value else None
    except ValueError:
        return None


def _error_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("error") or body.get("message") or body)[:200]
    except ValueError:
        pass
    return resp.text[:200]
