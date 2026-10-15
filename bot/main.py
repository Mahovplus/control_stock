"""Точка запуска: python -m bot.main

Открывает базу, подключает кнопки су-шефа, запускает проверку таймеров
копию базы раз в день и начинает слушать Telegram.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.backup import backup_dir_for
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
    dp.include_router(make_router(conn, config.sous_chef_ids, config.waiters_chat_id))
    timers = asyncio.create_task(  # фоном
        timer_loop(bot, conn, config.sous_chef_ids, backup_dir_for(config.db_path))
    )
    try:
        await dp.start_polling(bot)  # опрос Telegram: внешний адрес сервера не нужен
    finally:
        timers.cancel()


if __name__ == "__main__":
    asyncio.run(main())
