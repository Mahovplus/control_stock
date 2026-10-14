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
from bot.handlers import eta_keyboard, ready_keyboard

CHECK_EVERY_SECONDS = 30


async def ask_due(bot: Bot, conn: sqlite3.Connection, sous_chef_id: int, now: datetime) -> int:
    """Спрашивает «готово?» и шлёт напоминания, чьё время пришло. Возвращает, сколько написал."""
    dishes = stock.take_due_timers(conn, now)
    for dish in dishes:
        await bot.send_message(
            sous_chef_id, f"Шеф, {dish.name} готово?", reply_markup=ready_keyboard(dish.id)
        )
        stock.set_reminder(conn, dish.id, "ready", now)
    sent = len(dishes)
    for dish, kind in stock.take_due_reminders(conn, now):
        if not dish.in_stop:  # уже пополнили: напоминать не о чем
            continue
        sent += 1
        if kind == "eta":
            await bot.send_message(
                sous_chef_id,
                f"Напоминаю: {dish.name} в стопе. Через сколько будет?",
                reply_markup=eta_keyboard(dish.id),
            )
        else:
            await bot.send_message(
                sous_chef_id, f"Напоминаю: {dish.name} готово?", reply_markup=ready_keyboard(dish.id)
            )
    return sent


async def timer_loop(bot: Bot, conn: sqlite3.Connection, sous_chef_id: int) -> None:
    """Каждые 30 секунд проверяет таймеры. Ошибка не останавливает проверку."""
    while True:
        try:
            await ask_due(bot, conn, sous_chef_id, datetime.now())
        except Exception:
            logging.exception("Ошибка при проверке таймеров")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
