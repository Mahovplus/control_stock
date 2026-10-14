"""Кнопки и диалоги су-шефа в Telegram (разделы 5, 6 и 7 logika-bota.md).

Каждый обработчик только разговаривает с человеком, а считает stock.py.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bot import stock

BTN_LIST = "📋 Остатки"
BTN_REPLENISH = "➕ Пополнить"
BTN_RECOUNT = "🔄 Утренний пересчёт"
BTN_ADD = "🍽 Добавить блюдо"
BTN_SALE = "🧾 Продажа (тест)"  # пока iiko не подключена, продажу вводим руками

WELCOME_PICTURE = Path(__file__).resolve().parent.parent / "assets" / "welcome.png"

# Подпись к картинке в Telegram: не больше 1024 символов (проверяет тест)
WELCOME_TEXT = f"""Здравствуйте, шеф! 👋
Я помощник кухни: считаю порционные блюда, чтобы гостю не сказали «уже закончилось».

Кнопки:
• {BTN_LIST}: сколько порций свободно и сколько в работе.
• {BTN_REPLENISH}: приготовили партию, добавьте порции. Блюдо выйдет из стопа.
• {BTN_RECOUNT}: спрошу факт по каждому блюду и покажу расхождения.
• {BTN_ADD}: новое порционное блюдо в список.

Сам слежу и пишу:
• Осталось 3 порции: предупрежу один раз.
• 0 порций: ставлю в стоп и спрашиваю, через сколько будет. Официантам сообщу время.
• Время вышло: спрошу «Готово?». «Да» вернёт блюдо в продажу.
• Пробили, а порций нет: срочно напишу вам и официантам.

Важно:
• Отвечаю только су-шефу.
• Остаток не уходит ниже 0, решение всегда за вами.
• Пока iiko не подключена, стоп в iiko ставьте вручную, а продажи для проверки вводите кнопкой «{BTN_SALE}».

Эта памятка всегда по команде /help."""

HOURS = (1, 2, 3)  # раздел 6: через сколько блюдо вернётся

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_REPLENISH)],
        [KeyboardButton(text=BTN_RECOUNT), KeyboardButton(text=BTN_ADD)],
        [KeyboardButton(text=BTN_SALE)],
    ],
    resize_keyboard=True,
)


class Dialog(StatesGroup):
    """Где сейчас су-шеф в разговоре с ботом."""

    add_name = State()        # ждём название нового блюда
    replenish_qty = State()   # ждём, сколько порций добавить
    recount_qty = State()     # ждём фактическое число при пересчёте


def format_stock(dishes: list[stock.Dish]) -> str:
    if not dishes:
        return "Список пуст. Добавь порционные блюда кнопкой «🍽 Добавить блюдо»."
    lines = ["Остатки (свободно / в работе):"]
    for d in dishes:
        mark = " ⛔ стоп" if d.in_stop else ""
        lines.append(f"• {d.name}: {d.free} / {d.in_work}{mark}")
    return "\n".join(lines)


def dishes_keyboard(dishes: list[stock.Dish], prefix: str) -> InlineKeyboardMarkup:
    """Кнопка на каждое блюдо. В callback_data кладём «prefix:id»."""
    rows = [
        [InlineKeyboardButton(text=f"{d.name} ({d.free})", callback_data=f"{prefix}:{d.id}")]
        for d in dishes
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hours_text(h: int) -> str:
    return "1 час" if h == 1 else f"{h} часа"


def eta_keyboard(dish_id: int) -> InlineKeyboardMarkup:
    """Кнопки «1 час / 2 часа / 3 часа». В callback_data: «eta:блюдо:часы»."""
    row = [
        InlineKeyboardButton(text=hours_text(h), callback_data=f"eta:{dish_id}:{h}")
        for h in HOURS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def ready_keyboard(dish_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=f"ready:{dish_id}:1"),
        InlineKeyboardButton(text="Нет", callback_data=f"ready:{dish_id}:0"),
    ]])


def stop_text(name: str) -> str:
    return (
        f"⛔ Шеф, {name} закончилось, ставлю в стоп.\n"
        "Поставьте в стоп и в iiko вручную: iiko пока не подключена.\n"
        "Через сколько будет?"
    )


async def notify_waiters(bot: Bot, waiters_chat_id: int | None, sous_chef_id: int, text: str) -> None:
    """Пишет в чат официантов. Если не вышло, говорит об этом су-шефу, а не молчит."""
    if waiters_chat_id is None:
        await bot.send_message(
            sous_chef_id,
            "Чат официантов не настроен, им я не написал. "
            "Как настроить: WAITERS_CHAT_ID в файле .env.",
        )
        return
    try:
        await bot.send_message(waiters_chat_id, text)
    except TelegramAPIError as e:
        logging.warning("Не смог написать в чат официантов: %s", e)
        await bot.send_message(
            sous_chef_id,
            "Не смог написать в чат официантов. Проверьте, что бот добавлен в группу "
            "и WAITERS_CHAT_ID в .env верный.",
        )


def make_router(
    conn: sqlite3.Connection, sous_chef_id: int, waiters_chat_id: int | None = None
) -> Router:
    router = Router()
    # Бот отвечает только су-шефу (раздел 2). Остальных просто не слышит.
    router.message.filter(F.from_user.id == sous_chef_id)
    router.callback_query.filter(F.from_user.id == sous_chef_id)

    def who(user_id: int) -> str:
        return str(user_id)

    async def to_waiters(bot: Bot, text: str) -> None:
        await notify_waiters(bot, waiters_chat_id, sous_chef_id, text)

    @router.message(Command("chatid"))
    async def chat_id(message: Message) -> None:
        # Напишите /chatid в группе официантов, чтобы узнать число для .env
        await message.answer(f"ID этого чата: {message.chat.id}")

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if WELCOME_PICTURE.exists():
            await message.answer_photo(
                FSInputFile(WELCOME_PICTURE), caption=WELCOME_TEXT, reply_markup=MAIN_MENU
            )
        else:  # картинку случайно удалили: памятка всё равно нужна
            await message.answer(WELCOME_TEXT, reply_markup=MAIN_MENU)

    @router.message(F.text == BTN_LIST)
    async def show_stock(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(format_stock(stock.list_dishes(conn)))

    # --- Добавить блюдо -------------------------------------------------
    @router.message(F.text == BTN_ADD)
    async def add_start(message: Message, state: FSMContext) -> None:
        await state.set_state(Dialog.add_name)
        await message.answer("Как блюдо называется в меню? Напишите название.")

    @router.message(Dialog.add_name, F.text)
    async def add_name(message: Message, state: FSMContext) -> None:
        try:
            dish = stock.add_dish(conn, message.text)
        except stock.StockError as e:
            await message.answer(str(e))
            return
        await state.clear()
        await message.answer(
            f"Добавил «{dish.name}». Остаток пока 0, пополните кнопкой «{BTN_REPLENISH}»."
        )

    # --- Пополнить ------------------------------------------------------
    @router.message(F.text == BTN_REPLENISH)
    async def replenish_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        dishes = stock.list_dishes(conn)
        if not dishes:
            await message.answer(format_stock(dishes))
            return
        await message.answer("Что пополняем?", reply_markup=dishes_keyboard(dishes, "rep"))

    @router.callback_query(F.data.startswith("rep:"))
    async def replenish_pick(call: CallbackQuery, state: FSMContext) -> None:
        dish = stock.get_dish(conn, int(call.data.split(":")[1]))
        await state.set_state(Dialog.replenish_qty)
        await state.update_data(dish_id=dish.id)
        await call.message.answer(f"{dish.name}: сколько порций добавить?")
        await call.answer()

    @router.message(Dialog.replenish_qty, F.text)
    async def replenish_qty(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            qty = stock.parse_count(message.text, allow_zero=False)
            before = stock.get_dish(conn, data["dish_id"])
            dish = stock.replenish(conn, data["dish_id"], qty, who(message.from_user.id))
        except stock.StockError as e:
            await message.answer(str(e))
            return
        await state.clear()
        covered = qty - (dish.free - before.free)
        text = f"Готово: {dish.name} +{qty}, свободно {dish.free}."
        if covered:
            text += f"\n{covered} из них закрыли заказы, пробитые сверх остатка."
        if before.in_stop and not dish.in_stop:
            text += "\nБлюдо вышло из стопа. Снимите стоп и в iiko вручную."
        await message.answer(text)
        if before.in_stop and not dish.in_stop:
            await to_waiters(message.bot, f"✅ {dish.name} снова в продаже.")

    # --- Утренний пересчёт ------------------------------------------------
    async def ask_next(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        queue = data["queue"]
        if not queue:
            await state.clear()
            report = data["report"] or ["Всё сошлось, расхождений нет."]
            await message.answer("Пересчёт закончен.\n" + "\n".join(report))
            return
        dish = stock.get_dish(conn, queue[0])
        await message.answer(f"{dish.name}: сколько порций по факту? (в боте {dish.free})")

    @router.message(F.text == BTN_RECOUNT)
    async def recount_start(message: Message, state: FSMContext) -> None:
        dishes = stock.list_dishes(conn)
        if not dishes:
            await message.answer(format_stock(dishes))
            return
        await state.set_state(Dialog.recount_qty)
        await state.update_data(queue=[d.id for d in dishes], report=[])
        await ask_next(message, state)

    @router.message(Dialog.recount_qty, F.text)
    async def recount_qty(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        dish_id = data["queue"][0]
        try:
            actual = stock.parse_count(message.text, allow_zero=True)
            was_in_stop = stock.get_dish(conn, dish_id).in_stop
            diff = stock.recount(conn, dish_id, actual, who(message.from_user.id))
        except stock.StockError as e:
            await message.answer(str(e))
            return
        if actual == 0 and not was_in_stop:  # раздел 5: стало 0, значит стоп
            name = stock.get_dish(conn, dish_id).name
            await message.answer(stop_text(name), reply_markup=eta_keyboard(dish_id))
        report = data["report"]
        if diff:
            name = stock.get_dish(conn, dish_id).name
            report.append(f"• {name}: расхождение {diff:+d}")
        await state.update_data(queue=data["queue"][1:], report=report)
        await ask_next(message, state)

    # --- Продажа для проверки (раздел 4, пока без iiko) -------------------
    @router.message(F.text == BTN_SALE)
    async def sale_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        dishes = stock.list_dishes(conn)
        if not dishes:
            await message.answer(format_stock(dishes))
            return
        await message.answer(
            "Тест: какое блюдо пробили? Одно нажатие = одна порция.",
            reply_markup=dishes_keyboard(dishes, "sale"),
        )

    @router.callback_query(F.data.startswith("sale:"))
    async def sale_pick(call: CallbackQuery) -> None:
        await call.answer()
        result = stock.sell(conn, int(call.data.split(":")[1]), who(call.from_user.id))
        await report_sale(call.message, result)

    async def report_sale(message: Message, result: stock.SaleResult) -> None:
        """Уведомления раздела 5 после одной продажи."""
        dish = result.dish
        if result.oversold:
            await message.answer(
                f"🚨 Шеф, {dish.name} пробили, а порций нет. Остаток остаётся 0.\n"
                "Решите, что делать. Если сделаете порцию, добавьте её кнопкой "
                f"«{BTN_REPLENISH}»: она закроет этот заказ."
            )
            await to_waiters(message.bot, f"🚨 {dish.name} пробили, а порций нет. Подойдите к су-шефу.")
            return
        await message.answer(f"Пробито: {dish.name}, свободно {dish.free}.")
        if result.low_alert:
            await message.answer(f"⚠️ Шеф, {dish.name}: осталось {dish.free} порции.")
        if result.ran_out:
            await message.answer(stop_text(dish.name), reply_markup=eta_keyboard(dish.id))

    # --- Возврат в продажу (раздел 6) -------------------------------------
    @router.callback_query(F.data.startswith("eta:"))
    async def eta_pick(call: CallbackQuery) -> None:
        await call.answer()
        _, dish_id, hours = call.data.split(":")
        dish = stock.get_dish(conn, int(dish_id))
        due = stock.set_timer(conn, dish.id, int(hours), datetime.now())
        await call.message.answer(
            f"Понял. В {due:%H:%M} спрошу, готово ли {dish.name}."
        )
        await to_waiters(
            call.bot, f"⛔ {dish.name} в стопе, будет примерно через {hours_text(int(hours))}."
        )

    @router.callback_query(F.data.startswith("ready:"))
    async def ready_answer(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        _, dish_id, yes = call.data.split(":")
        dish = stock.get_dish(conn, int(dish_id))
        if yes == "1":
            await state.set_state(Dialog.replenish_qty)
            await state.update_data(dish_id=dish.id)
            await call.message.answer(f"{dish.name}: сколько порций готово?")
        else:
            await call.message.answer(
                f"{dish.name}: через сколько будет?", reply_markup=eta_keyboard(dish.id)
            )

    return router
