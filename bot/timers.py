"""Таймер «готово?» (раздел 6 logika-bota.md).

Время хранится в базе (таблица timers), поэтому после перезапуска бота
таймеры не теряются: бот просто спросит, как только время подошло.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot

from bot import stock
from bot.handlers import ready_keyboard

CHECK_EVERY_SECONDS = 30


async def ask_due(bot: Bot, conn: sqlite3.Connection, sous_chef_id: int, now: datetime) -> int:
    """Спрашивает су-шефа по каждому блюду, чьё время пришло. Возвращает, сколько спросил."""
    dishes = stock.take_due_timers(conn, now)
    for dish in dishes:
        await bot.send_message(
            sous_chef_id, f"Шеф, {dish.name} готово?", reply_markup=ready_keyboard(dish.id)
        )
    return len(dishes)


async def timer_loop(bot: Bot, conn: sqlite3.Connection, sous_chef_id: int) -> None:
    """Каждые 30 секунд проверяет таймеры. Ошибка не останавливает проверку."""
    while True:
        try:
            await ask_due(bot, conn, sous_chef_id, datetime.now())
        except Exception:
            logging.exception("Ошибка при проверке таймеров")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
