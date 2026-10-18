"""Стоп в iiko (раздел 8) без выхода в интернет: вместо iiko подставляем «фальшивую кассу»."""

import asyncio

from bot import stock
from bot.handlers import BTN_REPLENISH, BTN_SALE, link_with_iiko
from bot.iiko import IikoDish, IikoError, find_dish, same_name
from tests.test_dialogs import Harness

SALMON = IikoDish("salmon-id", "Лосось на гриле", "00005")
LAMB = IikoDish("lamb-id", "Томлёная в печи лопатка ягнёнка", "00004")


class FakeIiko:
    def __init__(self, menu=(SALMON, LAMB), broken=False):
        self.menu = list(menu)
        self.broken = broken  # True: iiko или касса не отвечают
        self.calls: list[tuple[str, str]] = []

    async def dishes(self):
        if self.broken:
            raise IikoError("нет связи с iiko")
        return self.menu

    async def stop(self, product_id):
        self._call("stop", product_id)

    async def unstop(self, product_id):
        self._call("unstop", product_id)

    def _call(self, what, product_id):
        if self.broken:
            raise IikoError("касса не ответила")
        self.calls.append((what, product_id))


def _add(h, name):
    h.say("🍽 Добавить блюдо")
    h.say(name)
    return h.press("kind:portion")[0]


def _sell_out(h, dish, qty=1):
    h.say(BTN_REPLENISH)
    h.press(f"rep:{dish.id}")
    h.say(str(qty))
    h.say(BTN_SALE)
    out = []
    for _ in range(qty):
        out += h.press(f"sale:{dish.id}")
    return out


def test_names_match_without_case_spaces_and_yo():
    assert same_name("Томлёная в печи  лопатка ягнёнка", "томленая в печи лопатка ягненка")
    assert not same_name("Лосось на гриле", "Лосось")
    assert find_dish([SALMON, LAMB], "лосось на гриле") == SALMON
    twins = [SALMON, IikoDish("other", "Лосось на гриле", "00009")]
    assert find_dish(twins, "Лосось на гриле") is None  # двух одинаковых не угадываем


def test_new_dish_is_linked_and_goes_to_stop_and_back_in_iiko():
    iiko = FakeIiko()
    h = Harness(iiko=iiko)
    assert "Связал с меню iiko" in _add(h, "лосось на гриле")
    dish = stock.list_dishes(h.conn)[0]
    assert stock.get_iiko_id(h.conn, dish.id) == "salmon-id"

    out = _sell_out(h, dish)
    # официанты узнают первыми, потом стоп в iiko, потом су-шеф
    assert h.to_waiters() == ["⛔ лосось на гриле в стопе. Когда вернётся, сообщу."]
    assert iiko.calls == [("stop", "salmon-id")]
    assert "В iiko поставил в стоп" in out[-1] and "Через сколько" in out[-1]

    h.say(BTN_REPLENISH)
    h.press(f"rep:{dish.id}")
    out = h.say("5")
    assert "вышло из стопа" in out[0] and "В iiko стоп снял" in out[0]
    assert iiko.calls[-1] == ("unstop", "salmon-id")


def test_dish_not_in_iiko_menu_asks_for_manual_stop():
    iiko = FakeIiko()
    h = Harness(iiko=iiko)
    assert "не нашёл блюдо с таким же названием" in _add(h, "Вителло тоннато")
    dish = stock.list_dishes(h.conn)[0]
    out = _sell_out(h, dish)
    assert "в iiko вручную: не нашёл «Вителло тоннато»" in out[-1]
    assert iiko.calls == []


def test_rename_to_iiko_name_links_dish():
    h = Harness(iiko=FakeIiko())
    _add(h, "Лосось")
    dish = stock.list_dishes(h.conn)[0]
    h.say("⚙️ Настройки")
    h.press(f"ren:{dish.id}")
    assert "Связал с меню iiko" in h.say("Лосось на гриле")[0]
    assert stock.get_iiko_id(h.conn, dish.id) == "salmon-id"


def test_iiko_failure_does_not_break_stop():
    iiko = FakeIiko()
    h = Harness(iiko=iiko)
    _add(h, "Лосось на гриле")
    dish = stock.list_dishes(h.conn)[0]
    iiko.broken = True
    out = _sell_out(h, dish)
    assert stock.get_dish(h.conn, dish.id).in_stop  # в боте стоп всё равно стоит
    assert "Не получилось в iiko (касса не ответила). Поставьте в стоп вручную." in out[-1]
    assert len(h.to_waiters()) == 1


def test_without_iiko_keys_bot_asks_for_manual_stop():
    h = Harness()
    msg = _add(h, "Лосось на гриле")
    assert "iiko" not in msg
    out = _sell_out(h, stock.list_dishes(h.conn)[0])
    assert "в iiko вручную: iiko не подключена" in out[-1]


def test_one_iiko_dish_is_not_linked_twice():
    h = Harness()
    a = stock.add_dish(h.conn, "Лосось на гриле")
    b = stock.add_dish(h.conn, "лосось на гриле ")  # тот же в другом написании
    missing = asyncio.run(link_with_iiko(h.conn, FakeIiko(), [a, b]))
    assert stock.get_iiko_id(h.conn, a.id) == "salmon-id"
    assert missing == ["лосось на гриле"]
