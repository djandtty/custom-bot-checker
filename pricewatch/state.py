"""state.json и history.csv."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

HISTORY_FIELDS = ["checked_at", "name", "min_price", "departure_date", "return_date",
                  "airline", "offers_found"]


def empty_state() -> dict:
    return {
        "version": 1,
        "meta": {"consecutive_failures": 0, "failure_alert_sent": False,
                 "last_run_at": None, "last_errors": []},
        "watches": {},
    }


def load_state(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_state()
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    state = empty_state()
    if isinstance(data, dict):
        state["meta"].update(data.get("meta") or {})
        state["watches"].update(data.get("watches") or {})
    return state


def save_state(path: str | Path, state: dict) -> None:
    """Атомарная запись: сначала во временный файл, потом rename."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent or ".", prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_history(path: str | Path, rows: list[dict]) -> None:
    if not rows:
        return
    path = Path(path)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") if row.get(k) is not None else "" for k in HISTORY_FIELDS})
