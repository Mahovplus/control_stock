"""Проверка диалогов су-шефа целиком, без выхода в интернет.

Вместо настоящего Telegram подставляем «фальшивую сессию»: она запоминает,
что бот хотел отправить, а мы проверяем эти тексты.
"""

import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot import stock
from bot.db import connect
from bot.handlers import BTN_ADD, BTN_RECOUNT, BTN_REPLENISH, make_router

CHEF = 111
STRANGER = 222


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[str] = []

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method.text)
            return Message(
                message_id=1, date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"Неожиданный запрос: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError

    async def close(self):
        pass


class Harness:
    def __init__(self):
        self.conn = connect(":memory:")
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher()
        self.dp.include_router(make_router(self.conn, CHEF))
        self.n = 0

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


def test_bot_ignores_everyone_except_sous_chef():
    h = Harness()
    assert h.say("/start", user_id=STRANGER) == []
    assert h.say(BTN_ADD, user_id=STRANGER) == []
