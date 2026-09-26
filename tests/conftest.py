import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricewatch.config import Settings, Watch  # noqa: E402
from pricewatch.offers import Offer  # noqa: E402

NOW = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)


def make_watch(**kw) -> Watch:
    base = dict(name="Стамбул ноябрь", origin="MOW", destination="IST",
                departure_from=dt.date(2026, 11, 10), departure_to=dt.date(2026, 11, 20),
                trip_days_min=5, trip_days_max=9, direct=False, max_price=None)
    base.update(kw)
    return Watch(**base)


def raw_offer(price, dep="2026-11-12", ret="2026-11-18", airline="PC", flight="400",
              transfers=1, return_transfers=0, expires="2026-10-02T00:00:00Z"):
    return {
        "price": price, "airline": airline, "flight_number": flight,
        "departure_at": f"{dep}T06:00:00+03:00", "return_at": f"{ret}T20:00:00+03:00",
        "transfers": transfers, "return_transfers": return_transfers,
        "link": f"/search/MOW{dep[8:10]}{dep[5:7]}IST{ret[8:10]}{ret[5:7]}1?t=x",
        "expires_at": expires,
    }


def offer(price, dep="2026-11-12", ret="2026-11-18", airline="PC", flight="400", **kw) -> Offer:
    from pricewatch.offers import parse_offer
    return parse_offer(raw_offer(price, dep, ret, airline, flight, **kw))


@pytest.fixture
def settings():
    return Settings(notify_mode="record_low", top_n=5, request_pause=0)
