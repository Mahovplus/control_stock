"""Точка запуска: python -m bot.main

Открывает базу, подключает кнопки су-шефа, запускает проверку таймеров
и начинает слушать Telegram.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import load_config
from bot.db import connect
from bot.handlers import make_router
from bot.timers import timer_loop


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    conn = connect(config.db_path)
    bot = Bot(config.bot_token)
    dp = Dispatcher()  # по умолчанию хранит состояние диалогов в памяти
    dp.include_router(make_router(conn, config.sous_chef_id, config.waiters_chat_id))
    timers = asyncio.create_task(timer_loop(bot, conn, config.sous_chef_id))  # фоном
    try:
        await dp.start_polling(bot)  # опрос Telegram: внешний адрес сервера не нужен
    finally:
        timers.cancel()


if __name__ == "__main__":
    asyncio.run(main())
