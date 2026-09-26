from pricewatch import messages

from conftest import make_watch, offer

NAMES = {"PC": "Pegasus", "TK": "Turkish Airlines"}.get


def names(code):
    return NAMES(code, code)


def test_price_format():
    assert messages.fmt_price(18900) == "18 900 ₽"
    assert messages.fmt_price(950) == "950 ₽"


def test_start_message():
    w = make_watch()
    top = [offer(18900, airline="PC", transfers=1, return_transfers=0)]
    text = messages.start_message(w, top, names)
    assert "🟢 <b>Старт мониторинга: Стамбул ноябрь</b>" in text
    assert "MOW → IST, вылет 10.11–20.11, поездка 5–9 дней" in text
    assert "1) 18 900 ₽ — 12.11 → 18.11 (6 дн.), Pegasus, 1 пер. / прямой — <a href=\"https://www.aviasales.ru/search/" in text
    assert text.endswith(messages.CACHE_WARNING)


def test_drop_message():
    w = make_watch()
    top = [offer(17400, dep="2026-11-14", ret="2026-11-20", airline="TK", transfers=0),
           offer(18000, flight="401")]
    text = messages.drop_message(w, top, 18900, 17400, names)
    assert "📉 <b>Цена снизилась: Стамбул ноябрь</b>" in text
    assert "(было 18 900 ₽, −1 500 ₽ / −8%)" in text
    assert "Лучший: 14.11 → 20.11 (6 дн.), Turkish Airlines, прямой / прямой" in text
    assert "Текущий топ-2:" in text
    assert "Минимум за всё время: 17 400 ₽" in text


def test_html_escaping():
    w = make_watch(name="<b>Тест & ко</b>")
    o = offer(1000, airline="X<Y")
    o = o.__class__(**{**o.__dict__, "link": '/search?a=1&b="2"'})
    text = messages.start_message(w, [o], lambda c: c)
    assert "&lt;b&gt;Тест &amp; ко&lt;/b&gt;" in text
    assert "X&lt;Y" in text
    assert 'href="https://www.aviasales.ru/search?a=1&amp;b=&quot;2&quot;"' in text


def test_finish_message():
    w = make_watch()
    entry = {"min_ever": {"price": 17400, "departure_date": "2026-11-14", "return_date": "2026-11-20",
                          "airline": "TK", "flight_number": "1", "found_at": "2026-10-20T10:00:00+00:00"}}
    text = messages.finish_message(w, entry, names)
    assert "🏁" in text and "17 400 ₽" in text and "найден 20.10.2026" in text
    assert "не нашлось" in messages.finish_message(w, {"min_ever": None}, names)


def test_plural():
    assert messages.plural(1, "день", "дня", "дней") == "день"
    assert messages.plural(3, "день", "дня", "дней") == "дня"
    assert messages.plural(12, "день", "дня", "дней") == "дней"
    assert "поездка 7 дней" in messages.watch_summary(make_watch(trip_days_min=7, trip_days_max=7))
