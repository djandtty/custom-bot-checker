"""Названия авиакомпаний по IATA-коду (справочник Travelpayouts)."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

AIRLINES_URL = "https://api.travelpayouts.com/data/ru/airlines.json"


class AirlineNames:
    def __init__(self, names: dict[str, str] | None = None):
        self._names = names or {}

    @classmethod
    def load(cls, session: requests.Session | None = None) -> "AirlineNames":
        """Загружает справочник; при ошибке вместо названий будут коды."""
        try:
            resp = (session or requests).get(AIRLINES_URL, timeout=30)
            resp.raise_for_status()
            names = {}
            for item in resp.json():
                code = item.get("code")
                name = item.get("name") or (item.get("name_translations") or {}).get("en")
                if code and name:
                    names[code] = name
            return cls(names)
        except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
            log.warning("Справочник авиакомпаний недоступен (%s), покажу коды", type(exc).__name__)
            return cls()

    def __call__(self, code: str) -> str:
        return self._names.get(code, code)
