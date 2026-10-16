"""Точка запуска: python -m bot.main

Открывает базу, подключает кнопки су-шефа, запускает проверку таймеров
копию базы раз в день и начинает слушать Telegram.
"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher

from bot.backup import backup_dir_for
from bot.config import load_config
from bot.db import connect
from bot.handlers import make_router
from bot.timers import timer_loop


def setup_logging(db_path: str) -> None:
    """Лог в окно и в файл data/bot.log (до 3 файлов по 1 МБ), чтобы ошибку можно было найти потом."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if db_path != ":memory:":
        log_path = Path(db_path).resolve().parent / "bot.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers
    )


async def main() -> None:
    try:
        config = load_config()
    except SystemExit as e:  # ошибка в .env: перезапуск не поможет, код 2 для zapusk.cmd
        print(e)
        sys.exit(2)
    setup_logging(config.db_path)
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
