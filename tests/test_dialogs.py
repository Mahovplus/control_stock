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
from bot.handlers import BTN_ADD, BTN_RECOUNT, BTN_REPLENISH, BTN_SALE, make_router
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
    def __init__(self, waiters=WAITERS):
        self.conn = connect(":memory:")
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher()
        self.dp.include_router(make_router(self.conn, CHEF, waiters))
        self.n = 0

    def to_waiters(self) -> list[str]:
        return [t for t, c in zip(self.session.sent, self.session.chats) if c == WAITERS]

    def tick(self, now) -> list[str]:
        """Как будто часы дошли до now: срабатывают таймеры."""
        before = len(self.session.sent)
        asyncio.run(ask_due(self.bot, self.conn, CHEF, now))
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
    assert "Добавил «Вителло тоннато»" in h.say("Вителло тоннато")[0]
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
    assert "закончилось, ставлю в стоп" in out[-1] and "Через сколько" in out[-1]
    assert stock.get_dish(h.conn, dish.id).in_stop

    out = h.press(f"eta:{dish.id}:2")
    assert "спрошу, готово ли" in out[0]
    assert h.to_waiters() == ["⛔ Вителло тоннато в стопе, будет примерно через 2 часа."]

    assert h.tick(datetime.now() + timedelta(hours=1)) == []  # ещё рано
    assert h.tick(datetime.now() + timedelta(hours=2, minutes=1)) == ["Шеф, Вителло тоннато готово?"]
    assert h.tick(datetime.now() + timedelta(hours=3)) == []  # спрашивает один раз

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
    assert h.to_waiters()[-1] == "⛔ Вителло тоннато в стопе, будет примерно через 3 часа."


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
