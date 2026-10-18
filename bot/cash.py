"""Опрос кассы iiko (раздел 4 logika-bota.md): раз в 30 секунд читаем заказы.

iiko сама не сообщает боту о заказах (бот без внешнего адреса), поэтому бот
спрашивает сам. Что делать с заказами, решает handlers.py (router.apply_cash).
"""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot

from bot import stock
from bot.handlers import as_ids, send_to_chefs
from bot.iiko import IikoClient, IikoError

CASH_EVERY_SECONDS = 30
SILENCE = timedelta(minutes=10)  # раздел 8: столько без связи с iiko, и пишем су-шефу


@dataclass
class CashWatch:
    since: datetime          # считаем только то, что отправили на кухню после этого
    last_ok: datetime        # когда последний раз прочитали заказы
    warned: bool = False     # су-шефу уже написали, что заказов не видно


async def cash_tick(
    bot: Bot, iiko: IikoClient, apply_cash, sous_chef_ids, watch: CashWatch, now: datetime
) -> None:
    """Один опрос. Ошибка iiko не останавливает бота: пишем в лог, а после 10 минут
    без связи один раз говорим су-шефу. Когда связь вернулась, тоже говорим."""
    try:
        orders = await iiko.orders()
    except IikoError as e:
        logging.warning("iiko: заказы не прочитал: %s", e)
        if not watch.warned and now - watch.last_ok >= SILENCE:
            watch.warned = True
            await send_to_chefs(
                bot, as_ids(sous_chef_ids),
                f"⚠️ Не вижу заказы из iiko больше 10 минут, остатки могут быть неточными.\n"
                f"Ошибка: {e}",
            )
        return
    watch.last_ok = now
    if watch.warned:
        watch.warned = False
        await send_to_chefs(bot, as_ids(sous_chef_ids), "✅ Снова вижу заказы из iiko.")
    await apply_cash(bot, orders, watch.since)


async def cash_loop(
    bot: Bot, conn: sqlite3.Connection, iiko: IikoClient, apply_cash, sous_chef_ids
) -> None:
    now = datetime.now()
    watch = CashWatch(since=stock.cash_since(conn, now), last_ok=now)
    logging.info("iiko: считаю заказы с кассы, отправленные на кухню с %s", watch.since)
    while True:
        try:
            await cash_tick(bot, iiko, apply_cash, sous_chef_ids, watch, datetime.now())
        except Exception:
            logging.exception("Ошибка при чтении заказов с кассы")
        await asyncio.sleep(CASH_EVERY_SECONDS)
