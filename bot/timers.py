"""Таймер «готово?» (раздел 6 logika-bota.md) и копия базы раз в день (раздел 14).

Время хранится в базе (таблица timers), поэтому после перезапуска бота
таймеры не теряются: бот просто спросит, как только время подошло.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot

from bot import stock
from bot.backup import daily_backup
from bot.handlers import as_ids, eta_keyboard, ready_keyboard, send_to_chefs

CHECK_EVERY_SECONDS = 30


async def ask_due(bot: Bot, conn: sqlite3.Connection, sous_chef_ids, now: datetime) -> int:
    """Спрашивает «готово?» и шлёт напоминания, чьё время пришло. Возвращает, сколько написал.

    Пишет всем су-шефам: ответит тот, кто свободен.
    """
    chefs = as_ids(sous_chef_ids)
    dishes = stock.take_due_timers(conn, now)
    for dish in dishes:
        await send_to_chefs(
            bot, chefs, f"Шеф, {dish.name} готово?", reply_markup=ready_keyboard(dish.id)
        )
        stock.set_reminder(conn, dish.id, "ready", now)
    sent = len(dishes)
    for dish, kind in stock.take_due_reminders(conn, now):
        if not dish.in_stop:  # уже пополнили: напоминать не о чем
            continue
        sent += 1
        if kind == "eta":
            await send_to_chefs(
                bot,
                chefs,
                f"Напоминаю: {dish.name} в стопе. Через сколько будет?",
                reply_markup=eta_keyboard(dish.id),
            )
        else:
            await send_to_chefs(
                bot, chefs, f"Напоминаю: {dish.name} готово?", reply_markup=ready_keyboard(dish.id)
            )
    return sent


async def timer_loop(
    bot: Bot, conn: sqlite3.Connection, sous_chef_ids, backup_dir: Path | None = None
) -> None:
    """Каждые 30 секунд проверяет таймеры и раз в день делает копию базы.
    Ошибка не останавливает проверку."""
    while True:
        try:
            await ask_due(bot, conn, sous_chef_ids, datetime.now())
        except Exception:
            logging.exception("Ошибка при проверке таймеров")
        if backup_dir is not None:
            try:
                made = daily_backup(conn, backup_dir, datetime.now().date())
                if made:
                    logging.info("Резервная копия базы: %s", made)
            except Exception:
                logging.exception("Не получилось сделать резервную копию базы")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
