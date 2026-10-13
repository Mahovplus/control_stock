"""Кнопки и диалоги су-шефа в Telegram (раздел 7 logika-bota.md).

Каждый обработчик только разговаривает с человеком, а считает stock.py.
"""

import sqlite3

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
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

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_REPLENISH)],
        [KeyboardButton(text=BTN_RECOUNT), KeyboardButton(text=BTN_ADD)],
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


def make_router(conn: sqlite3.Connection, sous_chef_id: int) -> Router:
    router = Router()
    # Бот отвечает только су-шефу (раздел 2). Остальных просто не слышит.
    router.message.filter(F.from_user.id == sous_chef_id)
    router.callback_query.filter(F.from_user.id == sous_chef_id)

    def who(user_id: int) -> str:
        return str(user_id)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Здравствуйте, шеф! Я слежу за порционными блюдами. Выберите, что сделать.",
            reply_markup=MAIN_MENU,
        )

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
            dish = stock.replenish(conn, data["dish_id"], qty, who(message.from_user.id))
        except stock.StockError as e:
            await message.answer(str(e))
            return
        await state.clear()
        await message.answer(f"Готово: {dish.name} +{qty}, свободно {dish.free}.")

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
            diff = stock.recount(conn, dish_id, actual, who(message.from_user.id))
        except stock.StockError as e:
            await message.answer(str(e))
            return
        report = data["report"]
        if diff:
            name = stock.get_dish(conn, dish_id).name
            report.append(f"• {name}: расхождение {diff:+d}")
        await state.update_data(queue=data["queue"][1:], report=report)
        await ask_next(message, state)

    return router
