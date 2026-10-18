"""Проверка диалогов су-шефа целиком, без выхода в интернет.

Вместо настоящего Telegram подставляем «фальшивую сессию»: она запоминает,
что бот хотел отправить, а мы проверяем эти тексты.
"""

import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot import stock
from bot.db import connect
from bot.handlers import (
    BTN_ADD,
    BTN_LIST,
    BTN_RECOUNT,
    BTN_REPLENISH,
    BTN_SALE,
    BTN_SETTINGS,
    make_router,
)
from bot.timers import ask_due

CHEF = 111
STRANGER = 222
WAITERS = -100500  # у групп ID с минусом


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[str] = []
        self.chats: list[int] = []  # кому ушло каждое сообщение из sent

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method.text)
            self.chats.append(method.chat_id)
            return Message(
                message_id=1, date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        if isinstance(method, SendPhoto):  # для проверки важна подпись
            self.sent.append(method.caption)
            self.chats.append(method.chat_id)
            return Message(
                message_id=1, date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"Неожиданный запрос: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError

    async def close(self):
        pass


class Harness:
    def __init__(self, waiters=WAITERS, chefs=(CHEF,), iiko=None):
        self.conn = connect(":memory:")
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher()
        self.chefs = list(chefs)
        self.router = make_router(self.conn, self.chefs, waiters, iiko)
        self.dp.include_router(self.router)
        self.n = 0

    def to_waiters(self) -> list[str]:
        return [t for t, c in zip(self.session.sent, self.session.chats) if c == WAITERS]

    def tick(self, now) -> list[str]:
        """Как будто часы дошли до now: срабатывают таймеры."""
        before = len(self.session.sent)
        asyncio.run(ask_due(self.bot, self.conn, self.chefs, now))
        return self.session.sent[before:]

    def _msg(self, text, user_id):
        self.n += 1
        user = User(id=user_id, is_bot=False, first_name="Шеф")
        return Message(
            message_id=self.n, date=datetime.now(), chat=Chat(id=user_id, type="private"),
            from_user=user, text=text,
        )

    def say(self, text, user_id=CHEF) -> list[str]:
        before = len(self.session.sent)
        upd = Update(update_id=self.n + 1, message=self._msg(text, user_id))
        asyncio.run(self.dp.feed_update(self.bot, upd))
        return self.session.sent[before:]

    def press(self, data, user_id=CHEF) -> list[str]:
        before = len(self.session.sent)
        self.n += 1
        user = User(id=user_id, is_bot=False, first_name="Шеф")
        call = CallbackQuery(
            id=str(self.n), from_user=user, chat_instance="x", data=data,
            message=self._msg("кнопка", user_id),
        )
        asyncio.run(self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=call)))
        return self.session.sent[before:]


def test_full_flow_add_replenish_recount():
    h = Harness()
    assert "Здравствуйте, шеф" in h.say("/start")[0]

    h.say(BTN_ADD)
    assert "какое это блюдо" in h.say("Вителло тоннато")[0]
    assert "Добавил «Вителло тоннато» (порционное)" in h.press("kind:portion")[0]
    dish = stock.list_dishes(h.conn)[0]

    assert "Что пополняем?" in h.say(BTN_REPLENISH)[0]
    assert "сколько порций добавить" in h.press(f"rep:{dish.id}")[0]
    assert "Нужно целое число" in h.say("много")[0]  # ошибка не сбрасывает диалог
    assert "+10, свободно 10" in h.say("10")[0]

    assert "в боте 10" in h.say(BTN_RECOUNT)[0]
    out = h.say("8")
    assert "Пересчёт закончен" in out[0] and "расхождение -2" in out[0]
    assert stock.get_dish(h.conn, dish.id).free == 8


def test_welcome_picture_and_help():
    from bot.handlers import WELCOME_PICTURE, WELCOME_TEXT

    assert WELCOME_PICTURE.exists()
    assert len(WELCOME_TEXT) <= 1024  # лимит подписи к фото в Telegram
    h = Harness()
    assert h.say("/start") == [WELCOME_TEXT]
    assert h.say("/help") == [WELCOME_TEXT]


def test_bot_ignores_everyone_except_sous_chef():
    h = Harness()
    assert h.say("/start", user_id=STRANGER) == []
    assert h.say(BTN_ADD, user_id=STRANGER) == []


def _dish_with(h, qty):
    h.say(BTN_ADD)
    h.say("Вителло тоннато")
    h.press("kind:portion")
    dish = stock.list_dishes(h.conn)[0]
    h.press(f"rep:{dish.id}")
    h.say(str(qty))
    return dish


def test_low_stock_stop_timer_and_back_to_sale():
    """Разделы 5 и 6 целиком: 3 → 0 → стоп → «через 2 часа» → «готово?» → снова в продаже."""
    h = Harness()
    dish = _dish_with(h, 4)
    assert "Тест: какое блюдо" in h.say(BTN_SALE)[0]

    out = h.press(f"sale:{dish.id}")
    assert "свободно 3" in out[0] and "осталось 3 порции" in out[1]
    assert not any("осталось" in t for t in h.press(f"sale:{dish.id}"))  # второй раз молчит
    h.press(f"sale:{dish.id}")
    out = h.press(f"sale:{dish.id}")
    # официанты узнают первыми, су-шефа бот не ждёт
    assert h.to_waiters() == ["⛔ Вителло тоннато в стопе. Когда вернётся, сообщу."]
    assert out.index(h.to_waiters()[0]) < len(out) - 1
    assert "закончилось, ставлю в стоп" in out[-1] and "Через сколько" in out[-1]
    assert stock.get_dish(h.conn, dish.id).in_stop

    out = h.press(f"eta:{dish.id}:2")
    assert "спрошу, готово ли" in out[0]
    assert h.to_waiters()[-1] == "⏱ Вителло тоннато будет примерно через 2 часа."

    assert h.tick(datetime.now() + timedelta(hours=1)) == []  # ещё рано, напоминаний нет
    t = datetime.now() + timedelta(hours=2, minutes=1)
    assert h.tick(t) == ["Шеф, Вителло тоннато готово?"]
    assert h.tick(t + timedelta(minutes=16)) == ["Напоминаю: Вителло тоннато готово?"]
    assert h.tick(t + timedelta(hours=3)) == []  # напоминает только один раз

    assert "сколько порций готово" in h.press(f"ready:{dish.id}:1")[0]
    out = h.say("6")
    assert "свободно 6" in out[0] and "вышло из стопа" in out[0]
    assert h.to_waiters()[-1] == "✅ Вителло тоннато снова в продаже."
    assert not stock.get_dish(h.conn, dish.id).in_stop


def test_not_ready_asks_time_again():
    h = Harness()
    dish = _dish_with(h, 1)
    h.press(f"sale:{dish.id}")
    h.press(f"eta:{dish.id}:1")
    out = h.press(f"ready:{dish.id}:0")
    assert "через сколько будет" in out[0]
    h.press(f"eta:{dish.id}:3")
    assert h.to_waiters()[-1] == "⏱ Вителло тоннато будет примерно через 3 часа."


def test_oversell_alerts_chef_and_waiters_without_negative():
    h = Harness()
    dish = _dish_with(h, 1)
    h.press(f"sale:{dish.id}")
    out = h.press(f"sale:{dish.id}")
    assert "пробили, а порций нет" in out[0]
    assert h.to_waiters()[-1] == "🚨 Вителло тоннато пробили, а порций нет. Подойдите к су-шефу."
    assert stock.get_dish(h.conn, dish.id).free == 0

    h.press(f"rep:{dish.id}")
    out = h.say("1")  # эта порция закрывает пробитый заказ
    assert "1 из них закрыли заказы" in out[0]
    assert stock.get_dish(h.conn, dish.id).free == 0


def test_without_waiters_chat_chef_is_told():
    h = Harness(waiters=None)
    dish = _dish_with(h, 1)
    h.press(f"sale:{dish.id}")
    out = h.press(f"eta:{dish.id}:1")
    assert "Чат официантов не настроен" in out[-1]
    assert h.session.chats[-1] == CHEF


def test_recount_to_zero_puts_dish_in_stop():
    h = Harness()
    dish = _dish_with(h, 5)
    h.say(BTN_RECOUNT)
    out = h.say("0")
    assert any("ставлю в стоп" in t for t in out)
    assert stock.get_dish(h.conn, dish.id).in_stop


def test_chatid_command_answers_with_chat_number():
    h = Harness()
    assert h.say("/chatid") == [f"ID этого чата: {CHEF}"]


def test_busy_chef_gets_one_reminder_about_time():
    """Раздел 6: су-шеф у плиты не ответил, напомним один раз через 15 минут."""
    h = Harness()
    dish = _dish_with(h, 1)
    h.press(f"sale:{dish.id}")
    now = datetime.now()
    assert h.tick(now + timedelta(minutes=10)) == []
    assert h.tick(now + timedelta(minutes=16)) == ["Напоминаю: Вителло тоннато в стопе. Через сколько будет?"]
    assert h.tick(now + timedelta(hours=1)) == []


def test_no_reminder_after_answer_or_replenish():
    h = Harness()
    dish = _dish_with(h, 1)
    h.press(f"sale:{dish.id}")
    h.press(f"eta:{dish.id}:1")  # ответил: про время не напоминаем
    assert h.tick(datetime.now() + timedelta(minutes=20)) == []

    h.press(f"rep:{dish.id}")
    h.say("3")  # пополнил раньше таймера: ни «готово?», ни напоминаний
    assert h.tick(datetime.now() + timedelta(hours=5)) == []


def _rib(h, weights="550 620 480"):
    h.say(BTN_ADD)
    h.say("Ребро телёнка")
    assert "штучное весовое" in h.press("kind:weight")[0]
    dish = stock.list_dishes(h.conn)[0]
    assert "веса кусков в граммах" in h.press(f"rep:{dish.id}")[0]
    out = h.say(weights)
    return dish, out


def test_weight_dish_replenish_list_and_quick_button():
    """Раздел 7в: веса одним сообщением, остатки с весами, быстрая кнопка."""
    h = Harness()
    dish, out = _rib(h)
    assert "+3 (550, 620, 480 г), свободно 3" in out[0]
    assert "Ребро телёнка: 3 шт (480, 550, 620 г) / 0" in h.say("📋 Остатки")[0]

    h.press(f"rep:{dish.id}")  # недавние веса уже есть кнопками
    assert "+1 (550 г), свободно 4" in h.press(f"w:{dish.id}:550")[0]


def test_weight_dish_sale_by_piece_low_and_stop():
    h = Harness()
    dish, _ = _rib(h, "550 620")
    assert "какой кусок пробили" in h.press(f"sale:{dish.id}")[0]
    piece_550 = stock.find_piece(h.conn, dish.id, 550)
    out = h.press(f"sp:{piece_550}")
    assert "свободно 1" in out[0] and "остался 1 кусок (620 г)" in out[1]

    out = h.press(f"sp:{stock.find_piece(h.conn, dish.id, 620)}")
    assert any("ставлю в стоп" in t for t in out)
    assert h.to_waiters()[-1] == "⛔ Ребро телёнка в стопе. Когда вернётся, сообщу."
    assert "Этого куска уже нет" in h.press(f"sp:{piece_550}")[0]  # дважды не продать


def test_weight_dish_recount_marks_missing_piece():
    h = Harness()
    dish, _ = _rib(h, "550 620")
    out = h.say(BTN_RECOUNT)
    assert "нажмите куски, которых нет" in out[0]
    lost = stock.find_piece(h.conn, dish.id, 620)
    assert "нет куска 620 г. Свободно 1" in h.press(f"lost:{lost}")[0]
    out = h.press("rcok")
    assert "Пересчёт закончен" in out[0] and "нет куска 620 г" in out[0]
    assert [g for _, g in stock.free_pieces(h.conn, dish.id)] == [550]


SECOND = 333  # второй су-шеф


def test_two_chefs_both_work_and_both_hear_about_stop():
    h = Harness(chefs=[CHEF, SECOND])
    h.say(BTN_ADD, user_id=SECOND)
    h.say("Тирамису", user_id=SECOND)
    h.press("kind:portion", user_id=SECOND)
    dish = stock.list_dishes(h.conn)[0]
    h.press(f"rep:{dish.id}")
    h.say("1")
    assert h.say(BTN_LIST, user_id=STRANGER) == []  # чужой по-прежнему не слышен
    h.say(BTN_SALE)
    before = len(h.session.sent)
    h.press(f"sale:{dish.id}")
    stop = [c for t, c in zip(h.session.sent[before:], h.session.chats[before:]) if "закончилось" in t]
    assert sorted(stop) == [CHEF, SECOND]  # время выберет тот, кто свободен
    out = h.tick(datetime.now() + timedelta(minutes=16))
    assert len(out) == 2 and all("Напоминаю" in t for t in out)


def test_settings_buttons_change_threshold_and_welcome():
    h = Harness()
    assert "Настройки" in h.say(BTN_SETTINGS)[0]
    assert "Сейчас 3" in h.press("set:low_stock")[0]
    assert "Готово" in h.press("setv:low_stock:5")[0]
    assert stock.get_setting(h.conn, "low_stock") == 5
    h.press("setv:remind_minutes:30")
    assert stock.get_setting(h.conn, "remind_minutes") == 30
    assert "нет среди вариантов" in h.press("setv:low_stock:99")[0]
    welcome = h.say("/help")[0]
    assert "Осталось 5 порций" in welcome and "через 30 минут" in welcome


def test_rename_and_delete_dish_with_confirmation():
    h = Harness()
    _dish_with(h, 2)
    dish = stock.list_dishes(h.conn)[0]
    h.say(BTN_SETTINGS)
    assert "переименовать" in h.press("set:rename")[0]
    h.press(f"ren:{dish.id}")
    assert "теперь «Тирамису классик»" in h.say("Тирамису классик")[0]
    h.press("set:delete")
    ask = h.press(f"del:{dish.id}")[0]
    assert "Удалить «Тирамису классик»" in ask and "свободно 2" in ask
    assert "ничего не удаляю" in h.press("delno")[0]
    assert stock.list_dishes(h.conn) != []
    assert "Удалил" in h.press(f"delok:{dish.id}")[0]
    assert "Список пуст" in h.say(BTN_LIST)[0]


def test_testchat_reports_missing_or_sent():
    assert "WAITERS_CHAT_ID не задан" in Harness(waiters=None).say("/testchat")[0]
    h = Harness()
    out = h.say("/testchat")
    assert h.to_waiters() == ["✅ Проверка связи: бот остатков пишет в этот чат."]
    assert "Отправил пробное сообщение" in out[-1]


def test_broken_button_tells_chef_instead_of_silence():
    h = Harness()
    out = h.press("rep:999")  # такого блюда нет: обработчик падает
    assert "Что-то пошло не так" in out[-1] and "StockError" in out[-1]
    assert h.say(BTN_LIST)  # бот жив и отвечает дальше
