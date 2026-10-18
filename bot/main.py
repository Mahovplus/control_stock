"""Точка запуска: python -m bot.main

Открывает базу, подключает кнопки су-шефа, запускает проверку таймеров,
копию базы раз в день, опрос кассы iiko (если есть ключи) и начинает слушать Telegram.
"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher

from bot import stock
from bot.backup import backup_dir_for
from bot.cash import cash_loop
from bot.config import load_config
from bot.db import connect
from bot.handlers import link_with_iiko, make_router
from bot.iiko import IikoClient, IikoError
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


async def link_old_dishes(conn, iiko: IikoClient) -> None:
    """При запуске связываем с меню iiko блюда, добавленные раньше. Ошибка iiko не мешает боту."""
    dishes = stock.unlinked_dishes(conn)
    if not dishes:
        return
    try:
        missing = await link_with_iiko(conn, iiko, dishes)
    except IikoError as e:
        logging.warning("iiko: не связал блюда при запуске: %s", e)
        return
    logging.info("iiko: связал %d блюд", len(dishes) - len(missing))
    if missing:
        logging.warning("iiko: нет в меню iiko: %s", ", ".join(missing))


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
    iiko = IikoClient(**config.iiko) if config.iiko else None
    if iiko:
        await link_old_dishes(conn, iiko)
    else:
        logging.info("iiko не подключена: ключей IIKO_ нет в .env, стоп в iiko вручную")
    router = make_router(conn, config.sous_chef_ids, config.waiters_chat_id, iiko)
    dp.include_router(router)
    background = [asyncio.create_task(  # фоном
        timer_loop(bot, conn, config.sous_chef_ids, backup_dir_for(config.db_path))
    )]
    if iiko:
        background.append(asyncio.create_task(
            cash_loop(bot, conn, iiko, router.apply_cash, config.sous_chef_ids)
        ))
    try:
        await dp.start_polling(bot)  # опрос Telegram: внешний адрес сервера не нужен
    finally:
        for task in background:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
