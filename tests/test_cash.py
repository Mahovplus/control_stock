"""Продажи с кассы iiko (раздел 4) без выхода в интернет: заказы подставляем сами."""

import asyncio
from datetime import datetime, timedelta, timezone

from bot import stock
from bot.cash import MOSCOW, CashWatch, cash_tick, kitchen_open
from bot.iiko import CashItem, CashOrder, IikoError, _cash_order
from tests.test_dialogs import Harness

SINCE = datetime(2026, 10, 18, 10, 0)
PRINTED = datetime(2026, 10, 18, 10, 15)


def _setup(free=5):
    h = Harness()
    dish = stock.add_dish(h.conn, "Лосось на гриле")
    stock.set_iiko_id(h.conn, dish.id, "salmon-id")
    if free:
        stock.replenish(h.conn, dish.id, free, "test")
    return h, dish


def _order(status="New", amount=2, printed=PRINTED, deleted=False, note=""):
    item = CashItem("pos-1", "salmon-id", amount, printed, deleted, note)
    return CashOrder("order-1", status, [item])


def _poll(h, *orders):
    before = len(h.session.sent)
    asyncio.run(h.router.apply_cash(h.bot, list(orders), SINCE))
    return h.session.sent[before:]


def _row(h, dish):
    return h.conn.execute("SELECT * FROM dishes WHERE id = ?", (dish.id,)).fetchone()


def test_sent_to_kitchen_then_paid():
    h, dish = _setup(free=10)
    assert _poll(h, _order()) == []  # на каждую порцию су-шефу не пишем
    assert (_row(h, dish)["free"], _row(h, dish)["in_work"]) == (8, 2)
    _poll(h, _order())  # тот же заказ при следующем опросе не считаем второй раз
    assert _row(h, dish)["free"] == 8
    _poll(h, _order("Closed"))
    row = _row(h, dish)
    assert (row["free"], row["in_work"], row["sold"]) == (8, 0, 2)


def test_not_sent_or_before_bot_is_ignored():
    h, dish = _setup()
    _poll(h, _order(printed=None), _order(printed=datetime(2026, 10, 18, 9, 0)))
    _poll(h, _order(deleted=True, printed=None))
    assert _row(h, dish)["free"] == 5


def test_last_portion_from_cash_goes_to_stop():
    h, dish = _setup(free=2)
    out = _poll(h, _order())
    assert h.to_waiters() == ["⛔ Лосось на гриле в стопе. Когда вернётся, сообщу."]
    assert "закончилось, ставлю в стоп" in out[-1]


def test_refusal_return_to_stock():
    h, dish = _setup(free=2)
    _poll(h, _order())  # 2 порции, блюдо ушло в стоп
    out = _poll(h, _order("Deleted", deleted=True, note="With write-off, удаление тест"))
    assert "Лосось на гриле ×2 удалили из заказа после кухни" in out[0]
    assert "With write-off, удаление тест" in out[0]
    assert _poll(h, _order("Deleted", deleted=True)) == []  # повторно не спрашиваем

    out = h.press("rfb:pos-1")
    assert "свободно 2" in out[0] and "вышло из стопа" in out[0]
    row = _row(h, dish)
    assert (row["free"], row["in_work"], row["in_stop"]) == (2, 0, 0)
    assert "уже решили" in h.press("rfb:pos-1")[0]


def test_refusal_write_off_asks_reason():
    h, dish = _setup()
    _poll(h, _order(amount=1))
    _poll(h, _order("Deleted", deleted=True, note="Without write-off"))
    assert "почему" in h.press("rfw:pos-1")[0]
    assert h.say("гость передумал") == ["Записал причину списания."]
    row = _row(h, dish)
    assert (row["free"], row["in_work"], row["written_off"]) == (4, 0, 1)
    comment = h.conn.execute(
        "SELECT comment FROM journal WHERE action = 'write_off'"
    ).fetchone()[0]
    assert comment == "отказ гостя: Without write-off. Су-шеф: гость передумал"


def test_paid_before_first_poll_is_counted_as_sold():
    h, dish = _setup()
    _poll(h, _order("Closed", amount=1))
    row = _row(h, dish)
    assert (row["free"], row["in_work"], row["sold"]) == (4, 0, 1)


def test_oversold_refusal_closes_oversell_without_question():
    h, dish = _setup(free=0)
    out = _poll(h, _order(amount=1))
    assert "пробили, а порций нет" in out[0]
    assert stock.pending_oversells(h.conn, dish.id) == 1
    assert _poll(h, _order("Deleted", deleted=True)) == []
    assert stock.pending_oversells(h.conn, dish.id) == 0


def test_cash_since_is_kept_after_restart():
    h, _ = _setup()
    first = stock.cash_since(h.conn, datetime(2026, 10, 18, 10, 0, 5))
    assert stock.cash_since(h.conn, datetime(2026, 10, 19, 8, 0)) == first


def test_order_from_iiko_is_read():
    # Так заказ выглядел в ответе облака на стенде 18.10.2026 (лишние поля убраны)
    raw = {"id": "7022", "order": {"status": "Deleted", "items": [{
        "type": "Product", "positionId": "p1", "amount": 2.0, "status": "CookingStarted",
        "product": {"id": "salmon-id", "name": "Лосось на гриле"},
        "whenPrinted": "2026-10-18 10:23:24.065",
        "deleted": {"deletionMethod": {"id": "cafe_dm", "comment": "удаление тест",
                                       "removalType": {"name": "With write-off"}}},
    }]}}
    order = _cash_order(raw)
    assert order.status == "Deleted"
    assert order.items == [CashItem(
        "p1", "salmon-id", 2.0, datetime(2026, 10, 18, 10, 23, 24), True,
        "With write-off, удаление тест",
    )]
    assert _cash_order({"id": "x", "order": None}) is None
    # официант написал комментарий в несколько строк с точкой (стенд, 19.10)
    raw["order"]["items"][0]["deleted"]["deletionMethod"]["comment"] = "\n\nтест\n."
    assert _cash_order(raw).items[0].note == "With write-off, тест"


class BrokenIiko:
    def __init__(self):
        self.broken = True

    async def orders(self):
        if self.broken:
            raise IikoError("нет связи с iiko")
        return []


def test_chef_is_told_when_orders_are_not_visible_for_10_minutes():
    h, _ = _setup()
    iiko = BrokenIiko()
    noon = datetime(2026, 10, 18, 12, 0, tzinfo=MOSCOW)
    watch = CashWatch(since=SINCE, last_ok=noon)

    def tick(minutes):
        before = len(h.session.sent)
        now = noon + timedelta(minutes=minutes)
        asyncio.run(cash_tick(h.bot, iiko, h.router.apply_cash, h.chefs, watch, now))
        return h.session.sent[before:]

    assert tick(5) == []
    assert "Не вижу заказы из iiko больше 10 минут" in tick(10)[0]
    assert tick(11) == []  # одно предупреждение, не каждые 30 секунд
    iiko.broken = False
    assert tick(12) == ["✅ Снова вижу заказы из iiko."]
    assert tick(13) == []


def test_no_warning_while_kitchen_is_closed():
    h, _ = _setup()
    iiko = BrokenIiko()  # ночью касса выключена
    night = datetime(2026, 10, 18, 2, 0, tzinfo=MOSCOW)
    watch = CashWatch(since=SINCE, last_ok=night)

    def tick(at):
        before = len(h.session.sent)
        asyncio.run(cash_tick(h.bot, iiko, h.router.apply_cash, h.chefs, watch, at))
        return h.session.sent[before:]

    assert tick(datetime(2026, 10, 18, 10, 59, tzinfo=MOSCOW)) == []
    assert tick(datetime(2026, 10, 18, 11, 5, tzinfo=MOSCOW)) == []  # 10 минут с открытия
    assert "Не вижу заказы" in tick(datetime(2026, 10, 18, 11, 10, tzinfo=MOSCOW))[0]
    # компьютер с ботом может жить по другому времени: 13:00 в Екатеринбурге = 11:00 в Москве
    ekb = timezone(timedelta(hours=5))
    assert kitchen_open(datetime(2026, 10, 18, 13, 0, tzinfo=ekb))
    assert not kitchen_open(datetime(2026, 10, 18, 23, 30, tzinfo=MOSCOW))


# --- Весовые блюда с кассы (раздел 7в, п. 6) --------------------------------

def _rib(*grams):
    h = Harness()
    dish = stock.add_dish(h.conn, "Ребро телёнка", stock.WEIGHT)
    stock.set_iiko_id(h.conn, dish.id, "rib-id")
    if grams:
        stock.add_pieces(h.conn, dish.id, list(grams), "test")
    return h, dish


def _rib_order(status="New", kg=0.55, deleted=False, pos="rib-1"):
    # так вес пришёл на стенде 19.10: amount 0.55 у блюда в кг
    return CashOrder("order-2", status, [CashItem(pos, "rib-id", kg, PRINTED, deleted, "")])


def _states(h):
    return [r[0] for r in h.conn.execute("SELECT state FROM pieces ORDER BY grams")]


def test_weight_piece_with_same_weight_goes_to_work_then_sold():
    h, dish = _rib(480, 550, 620)
    assert _poll(h, _rib_order()) == []
    assert _states(h) == ["free", "in_work", "free"]
    assert stock.get_dish(h.conn, dish.id).free == 2
    _poll(h, _rib_order("Closed"))
    assert _states(h) == ["free", "sold", "free"]
    assert _row(h, dish)["in_work"] == 0 and _row(h, dish)["sold"] == 1


def test_weight_not_found_asks_chef_which_piece():
    h, dish = _rib(550, 620)
    out = _poll(h, _rib_order(kg=0.56))
    assert out == ["⚖️ Шеф, Ребро телёнка: пробили 0,560, а у меня 550, 620 г. Какой снять?"]
    assert _poll(h, _rib_order(kg=0.56)) == []  # повторно не спрашиваем
    piece_620 = stock.free_pieces(h.conn, dish.id)[1][0]
    out = h.press(f"cp:rib-1:{piece_620}")
    assert out[0] == "Пробито: Ребро телёнка, свободно 1."
    assert _states(h) == ["free", "in_work"]
    assert "уже решили" in h.press(f"cp:rib-1:{piece_620}")[0]


def test_weight_deleted_before_chef_picked_needs_nothing():
    h, dish = _rib(550, 620)
    _poll(h, _rib_order(kg=0.56))
    assert _poll(h, _rib_order("Deleted", kg=0.56, deleted=True)) == []
    assert _states(h) == ["free", "free"]
    assert "уже решили" in h.press(f"cp:rib-1:{stock.free_pieces(h.conn, dish.id)[0][0]}")[0]


def test_weight_refusal_return_frees_the_same_piece():
    h, dish = _rib(550)
    out = _poll(h, _rib_order())
    assert "закончилось, ставлю в стоп" in out[-1]  # последний кусок
    _poll(h, _rib_order("Deleted", deleted=True))
    out = h.press("rfb:rib-1")
    assert "вышло из стопа" in out[0]
    assert stock.free_pieces(h.conn, dish.id) == [(1, 550)]
    assert _row(h, dish)["in_work"] == 0


def test_weight_refusal_write_off_marks_piece():
    h, dish = _rib(550, 620)
    _poll(h, _rib_order())
    _poll(h, _rib_order("Deleted", deleted=True))
    h.press("rfw:rib-1")
    assert _states(h) == ["written_off", "free"]
    assert _row(h, dish)["written_off"] == 1


def test_weight_without_pieces_is_oversell():
    h, dish = _rib()
    out = _poll(h, _rib_order())
    assert "пробили, а порций нет" in out[0]
    assert stock.pending_oversells(h.conn, dish.id) == 1
