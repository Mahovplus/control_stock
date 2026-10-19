"""Кнопки и диалоги су-шефа в Telegram (разделы 5, 6, 7 и 14 logika-bota.md).

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
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bot import stock
from bot.iiko import CashOrder, IikoClient, IikoError, find_dish

BTN_LIST = "📋 Остатки"
BTN_REPLENISH = "➕ Пополнить"
BTN_RECOUNT = "🔄 Утренний пересчёт"
BTN_ADD = "🍽 Добавить блюдо"
BTN_SALE = "🧾 Продажа (тест)"  # проверка без кассы: продажу вводим руками
BTN_SETTINGS = "⚙️ Настройки"
MENU_BUTTONS = {BTN_LIST, BTN_REPLENISH, BTN_RECOUNT, BTN_ADD, BTN_SALE, BTN_SETTINGS}

WELCOME_PICTURE = Path(__file__).resolve().parent.parent / "assets" / "welcome.png"


def portions_word(n: int) -> str:
    """1 порция, 2 порции, 5 порций."""
    if n % 10 == 1 and n % 100 != 11:
        return "порция"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "порции"
    return "порций"


def welcome_text(low: int = stock.LOW_STOCK, minutes: int = stock.REMIND_MINUTES) -> str:
    """Памятка. Цифры берём из настроек. Подпись к фото: не больше 1024 символов (тест)."""
    return f"""Здравствуйте, шеф! 👋
Я помощник кухни: считаю порционные блюда, чтобы гостю не сказали «уже закончилось».

Кнопки:
• {BTN_LIST}: сколько свободно и сколько в работе.
• {BTN_REPLENISH}: приготовили партию, добавьте порции. Блюдо выйдет из стопа.
• {BTN_RECOUNT}: спрошу факт по каждому блюду и покажу расхождения.
• {BTN_ADD}: порционное или штучное весовое, где у каждого куска свой вес.
• {BTN_SETTINGS}: порог, напоминание, правка и удаление блюд.

Сам слежу и пишу:
• Осталось {low} {portions_word(low)}: предупрежу один раз.
• 0 порций: ставлю в стоп и сразу пишу официантам. Вам одна кнопка: через сколько будет.
• Не ответили: через {minutes} минут напомню один раз.
• Время вышло: спрошу «Готово?». «Да» вернёт блюдо в продажу.
• Пробили, а порций нет: срочно напишу вам и официантам.

Важно:
• Отвечаю только су-шефам из списка.
• Остаток не уходит ниже 0, решение всегда за вами.
• Стоп в iiko ставлю и снимаю сам, если подключены ключи iiko. Продажи пока вводите кнопкой «{BTN_SALE}».

Памятка всегда по команде /help."""


WELCOME_TEXT = welcome_text()  # с настройками по умолчанию

HOURS = (1, 2, 3)  # раздел 6: через сколько блюдо вернётся

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_REPLENISH)],
        [KeyboardButton(text=BTN_RECOUNT), KeyboardButton(text=BTN_ADD)],
        [KeyboardButton(text=BTN_SALE), KeyboardButton(text=BTN_SETTINGS)],
    ],
    resize_keyboard=True,
    is_persistent=True,  # Telegram не прячет меню, даже когда у сообщения свои кнопки
)


class Dialog(StatesGroup):
    """Где сейчас су-шеф в разговоре с ботом."""

    add_name = State()        # ждём название нового блюда
    add_kind = State()        # ждём тип: порционное или штучное весовое
    replenish_qty = State()   # ждём, сколько порций добавить
    recount_qty = State()     # ждём фактическое число при пересчёте
    rename_name = State()     # ждём новое название блюда
    write_off_comment = State()  # ждём, почему списали отказ гостя


def format_stock(dishes: list[stock.Dish], pieces: dict[int, list[int]] | None = None) -> str:
    """pieces: веса свободных кусков весовых блюд, {id блюда: [граммы]}."""
    if not dishes:
        return "Список пуст. Добавь порционные блюда кнопкой «🍽 Добавить блюдо»."
    lines = ["Остатки (свободно / в работе):"]
    for d in dishes:
        mark = " ⛔ стоп" if d.in_stop else ""
        free = str(d.free)
        if d.kind == stock.WEIGHT:
            grams = (pieces or {}).get(d.id, [])
            free = f"{d.free} шт" + (f" ({', '.join(map(str, grams))} г)" if grams else "")
        lines.append(f"• {d.name}: {free} / {d.in_work}{mark}")
    return "\n".join(lines)


def kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Порционное", callback_data=f"kind:{stock.PORTION}"),
        InlineKeyboardButton(text="Штучное весовое", callback_data=f"kind:{stock.WEIGHT}"),
    ]])


def grams_keyboard(dish_id: int, grams: list[int], prefix: str) -> InlineKeyboardMarkup | None:
    """Кнопки с весами по 3 в ряд. prefix:блюдо:граммы."""
    if not grams:
        return None
    buttons = [
        InlineKeyboardButton(text=f"{g} г", callback_data=f"{prefix}:{dish_id}:{g}") for g in grams
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 3] for i in range(0, len(buttons), 3)])


def pieces_keyboard(pieces: list[tuple[int, int]], prefix: str, label: str = "") -> InlineKeyboardMarkup:
    """Кнопка на каждый кусок. prefix:id куска."""
    buttons = [
        InlineKeyboardButton(text=f"{label}{g} г", callback_data=f"{prefix}:{pid}") for pid, g in pieces
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 3] for i in range(0, len(buttons), 3)])


WEIGHTS_HINT = "напишите веса кусков в граммах через пробел, например 550 620 480"


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


def refusal_keyboard(position_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Вернуть в остаток", callback_data=f"rfb:{position_id}"),
        InlineKeyboardButton(text="Списать", callback_data=f"rfw:{position_id}"),
    ]])


def stop_text(name: str, iiko_note: str) -> str:
    return f"⛔ Шеф, {name} закончилось, ставлю в стоп.\n{iiko_note}\nЧерез сколько будет?"


async def link_with_iiko(conn: sqlite3.Connection, iiko: IikoClient, dishes: list[stock.Dish]) -> list[str]:
    """Связывает блюда с меню iiko по точному названию (раздел 8). Возвращает названия,
    которые не нашлись: для них стоп в iiko придётся ставить вручную."""
    menu = await iiko.dishes()
    missing = []
    for dish in dishes:
        found = find_dish(menu, dish.name)
        if found is None:
            missing.append(dish.name)
            continue
        try:
            stock.set_iiko_id(conn, dish.id, found.id)
        except sqlite3.IntegrityError:  # это блюдо iiko уже связано с другим блюдом бота
            missing.append(dish.name)
    return missing


def as_ids(sous_chef_ids: int | list[int]) -> list[int]:
    """Один ID или список ID су-шефов → список."""
    return [sous_chef_ids] if isinstance(sous_chef_ids, int) else list(sous_chef_ids)


async def send_to_chefs(bot: Bot, chef_ids: list[int], text: str, **kwargs) -> None:
    """Пишет всем су-шефам. Кто ещё не нажал Start, тому Telegram не даст написать:
    это не должно мешать остальным."""
    for chef in chef_ids:
        try:
            await bot.send_message(chef, text, **kwargs)
        except TelegramAPIError as e:
            logging.warning("Не смог написать су-шефу %s: %s", chef, e)


async def notify_waiters(bot: Bot, waiters_chat_id: int | None, sous_chef_ids, text: str) -> None:
    """Пишет в чат официантов. Если не вышло, говорит об этом су-шефам, а не молчит."""
    chefs = as_ids(sous_chef_ids)
    if waiters_chat_id is None:
        await send_to_chefs(
            bot,
            chefs,
            "Чат официантов не настроен, им я не написал. "
            "Как настроить: WAITERS_CHAT_ID в файле .env.",
        )
        return
    try:
        await bot.send_message(waiters_chat_id, text)
    except TelegramAPIError as e:
        logging.warning("Не смог написать в чат официантов: %s", e)
        await send_to_chefs(
            bot,
            chefs,
            "Не смог написать в чат официантов. Проверьте, что бот добавлен в группу "
            "и WAITERS_CHAT_ID в .env верный.",
        )


def settings_keyboard(low: int, minutes: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Порог «осталось мало»: {low}", callback_data="set:low_stock")],
        [InlineKeyboardButton(text=f"Напоминание через: {minutes} мин", callback_data="set:remind_minutes")],
        [InlineKeyboardButton(text="✏️ Переименовать блюдо", callback_data="set:rename")],
        [InlineKeyboardButton(text="🗑 Удалить блюдо", callback_data="set:delete")],
    ])


SETTING_NAMES = {
    "low_stock": ("Порог «осталось мало» для порционных блюд", "порц."),
    "remind_minutes": ("Через сколько минут напомнить, если не ответили", "мин"),
}


def make_router(
    conn: sqlite3.Connection,
    sous_chef_ids: int | list[int],
    waiters_chat_id: int | None = None,
    iiko: IikoClient | None = None,
) -> Router:
    router = Router()
    chefs = as_ids(sous_chef_ids)
    # Бот отвечает только су-шефам из списка (разделы 2 и 14). Остальных просто не слышит.
    router.message.filter(F.from_user.id.in_(chefs))
    router.callback_query.filter(F.from_user.id.in_(chefs))

    def who(user_id: int) -> str:
        return str(user_id)

    async def to_waiters(bot: Bot, text: str) -> None:
        await notify_waiters(bot, waiters_chat_id, chefs, text)

    async def iiko_stop(dish: stock.Dish, on: bool) -> str:
        """Ставит (on) или снимает стоп в iiko. Возвращает строку для су-шефа."""
        action = "Поставьте в стоп" if on else "Снимите стоп"
        iiko_id = stock.get_iiko_id(conn, dish.id)
        if iiko is None:
            return f"{action} и в iiko вручную: iiko не подключена."
        if iiko_id is None:
            return f"{action} в iiko вручную: не нашёл «{dish.name}» в меню iiko."
        try:
            await (iiko.stop(iiko_id) if on else iiko.unstop(iiko_id))
        except IikoError as e:
            logging.warning("iiko: %s «%s»: %s", "стоп" if on else "снять стоп", dish.name, e)
            return f"Не получилось в iiko ({e}). {action} вручную."
        return "В iiko поставил в стоп." if on else "В iiko стоп снял."

    async def went_to_stop(bot: Bot, dish: stock.Dish) -> None:
        """Раздел 6: официанты узнают сразу, потом стоп в iiko, су-шефам одно сообщение
        с кнопками. Пишем всем су-шефам: время выберет тот, кто свободен."""
        await to_waiters(bot, f"⛔ {dish.name} в стопе. Когда вернётся, сообщу.")
        note = await iiko_stop(dish, on=True)
        await send_to_chefs(
            bot, chefs, stop_text(dish.name, note), reply_markup=eta_keyboard(dish.id)
        )
        stock.set_reminder(conn, dish.id, "eta", datetime.now())

    @router.message(Command("chatid"))
    async def chat_id(message: Message) -> None:
        # Напишите /chatid в группе официантов, чтобы узнать число для .env
        await message.answer(f"ID этого чата: {message.chat.id}")

    @router.errors()
    async def on_error(event: ErrorEvent, bot: Bot) -> None:
        """Любая поломка в кнопке: записать в лог и сказать су-шефу, а не молчать."""
        logging.error("Ошибка в обработчике", exc_info=event.exception)
        source = event.update.message or event.update.callback_query
        if source is None or source.from_user is None:
            return
        try:
            await bot.send_message(
                source.from_user.id,
                "⚠️ Что-то пошло не так, действие могло не записаться. Проверьте «📋 Остатки» "
                f"и повторите. Если повторится, пришлите Даниилу время и что нажали.\n"
                f"Ошибка: {type(event.exception).__name__}",
            )
        except TelegramAPIError:
            logging.exception("Не смог сообщить об ошибке")

    @router.message(Command("testchat"))
    async def test_chat(message: Message) -> None:
        # Проверка чата официантов без продаж: пробное сообщение и понятный ответ су-шефу
        if waiters_chat_id is None:
            await message.answer(
                "WAITERS_CHAT_ID не задан. Бот читает только файл .env (не .env.example): "
                "допишите туда строку WAITERS_CHAT_ID= и число из /chatid, потом перезапустите бота."
            )
            return
        try:
            await message.bot.send_message(
                waiters_chat_id, "✅ Проверка связи: бот остатков пишет в этот чат."
            )
        except TelegramAPIError as e:
            await message.answer(
                f"Не смог написать в чат {waiters_chat_id}. Ответ Telegram: {e.message}\n"
                "Проверьте, что бот добавлен в группу и число совпадает с ответом /chatid в группе."
            )
            return
        await message.answer(f"Отправил пробное сообщение в чат {waiters_chat_id}. Проверьте группу.")

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        text = welcome_text(
            stock.get_setting(conn, "low_stock"), stock.get_setting(conn, "remind_minutes")
        )
        if WELCOME_PICTURE.exists():
            await message.answer_photo(
                FSInputFile(WELCOME_PICTURE), caption=text, reply_markup=MAIN_MENU
            )
        else:  # картинку случайно удалили: памятка всё равно нужна
            await message.answer(text, reply_markup=MAIN_MENU)

    def stock_text() -> str:
        dishes = stock.list_dishes(conn)
        pieces = {
            d.id: [g for _, g in stock.free_pieces(conn, d.id)]
            for d in dishes if d.kind == stock.WEIGHT
        }
        return format_stock(dishes, pieces)

    @router.message(F.text == BTN_LIST)
    async def show_stock(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(stock_text())

    # --- Добавить блюдо -------------------------------------------------
    @router.message(F.text == BTN_ADD)
    async def add_start(message: Message, state: FSMContext) -> None:
        await state.set_state(Dialog.add_name)
        await message.answer("Как блюдо называется в меню? Напишите название.")

    @router.message(Dialog.add_name, F.text)
    async def add_name(message: Message, state: FSMContext) -> None:
        name = message.text.strip()
        if not name:
            await message.answer("Название пустое. Напишите, как блюдо называется в меню.")
            return
        await state.set_state(Dialog.add_kind)
        await state.update_data(name=name)
        await message.answer(
            f"«{name}»: какое это блюдо?\n"
            "Порционное: одинаковые порции. Штучное весовое: каждый кусок со своим весом, "
            "как ребро телёнка.",
            reply_markup=kind_keyboard(),
        )

    @router.callback_query(Dialog.add_kind, F.data.startswith("kind:"))
    async def add_kind(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        data = await state.get_data()
        try:
            dish = stock.add_dish(conn, data["name"], call.data.split(":")[1])
        except stock.StockError as e:
            await state.clear()
            await call.message.answer(str(e))
            return
        await state.clear()
        kind = "штучное весовое" if dish.kind == stock.WEIGHT else "порционное"
        await call.message.answer(
            f"Добавил «{dish.name}» ({kind}). Остаток пока 0, пополните кнопкой «{BTN_REPLENISH}»."
            + await iiko_link_note(dish)
        )

    async def iiko_link_note(dish: stock.Dish) -> str:
        """Связать новое блюдо с меню iiko и сказать су-шефу, получилось ли."""
        if iiko is None:
            return ""
        try:
            missing = await link_with_iiko(conn, iiko, [dish])
        except IikoError as e:
            return f"\nС iiko не связал ({e}). Стоп в iiko пока вручную."
        if missing:
            return (
                "\nВ меню iiko не нашёл блюдо с таким же названием. "
                "Стоп в iiko для него ставьте вручную или переименуйте точно как в iiko."
            )
        return "\nСвязал с меню iiko: стоп там буду ставить сам."

    # --- Пополнить ------------------------------------------------------
    @router.message(F.text == BTN_REPLENISH)
    async def replenish_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        dishes = stock.list_dishes(conn)
        if not dishes:
            await message.answer(format_stock(dishes))
            return
        await message.answer("Что пополняем?", reply_markup=dishes_keyboard(dishes, "rep"))

    async def ask_replenish(message: Message, state: FSMContext, dish: stock.Dish, question: str) -> None:
        """Спросить, сколько добавить. Для весового блюда — веса кусков и быстрые кнопки."""
        await state.set_state(Dialog.replenish_qty)
        await state.update_data(dish_id=dish.id)
        if dish.kind == stock.WEIGHT:
            recent = stock.recent_weights(conn, dish.id)
            hint = "\nИли нажмите вес, который уже был." if recent else ""
            await message.answer(
                f"{dish.name}: {WEIGHTS_HINT}.{hint}",
                reply_markup=grams_keyboard(dish.id, recent, "w"),
            )
        else:
            await message.answer(f"{dish.name}: {question}")

    @router.callback_query(F.data.startswith("rep:"))
    async def replenish_pick(call: CallbackQuery, state: FSMContext) -> None:
        dish = stock.get_dish(conn, int(call.data.split(":")[1]))
        await ask_replenish(call.message, state, dish, "сколько порций добавить?")
        await call.answer()

    @router.callback_query(F.data.startswith("w:"))
    async def replenish_weight_button(call: CallbackQuery) -> None:
        # Быстрая кнопка с весом: один кусок. Диалог не закрываем, можно жать ещё.
        await call.answer()
        _, dish_id, grams = call.data.split(":")
        before = stock.get_dish(conn, int(dish_id))
        dish = stock.add_pieces(conn, before.id, [int(grams)], who(call.from_user.id))
        await after_replenish(call.message, before, dish, 1, f" ({grams} г)")

    @router.message(Dialog.replenish_qty, F.text)
    async def replenish_qty(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            before = stock.get_dish(conn, data["dish_id"])
            if before.kind == stock.WEIGHT:
                grams = stock.parse_weights(message.text)
                dish = stock.add_pieces(conn, before.id, grams, who(message.from_user.id))
                qty, detail = len(grams), f" ({', '.join(map(str, grams))} г)"
            else:
                qty = stock.parse_count(message.text, allow_zero=False)
                dish = stock.replenish(conn, before.id, qty, who(message.from_user.id))
                detail = ""
        except stock.StockError as e:
            await message.answer(str(e))
            return
        await state.clear()
        await after_replenish(message, before, dish, qty, detail)

    async def after_replenish(
        message: Message, before: stock.Dish, dish: stock.Dish, qty: int, detail: str
    ) -> None:
        covered = qty - (dish.free - before.free)
        text = f"Готово: {dish.name} +{qty}{detail}, свободно {dish.free}."
        if covered:
            text += f"\n{covered} из них закрыли заказы, пробитые сверх остатка."
        if before.in_stop and not dish.in_stop:
            text += "\nБлюдо вышло из стопа. " + await iiko_stop(dish, on=False)
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
        if dish.kind == stock.WEIGHT:
            pieces = stock.free_pieces(conn, dish.id)
            if not pieces:  # проверять нечего
                await state.update_data(queue=queue[1:])
                await ask_next(message, state)
                return
            keyboard = pieces_keyboard(pieces, "lost", "нет ")
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text="✅ Остальное на месте", callback_data="rcok")]
            )
            await message.answer(
                f"{dish.name}: нажмите куски, которых нет. Потом «Остальное на месте».",
                reply_markup=keyboard,
            )
            return
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
        if stock.get_dish(conn, dish_id).kind == stock.WEIGHT:
            await message.answer("Для этого блюда отметьте куски кнопками выше.")
            return
        try:
            actual = stock.parse_count(message.text, allow_zero=True)
            was_in_stop = stock.get_dish(conn, dish_id).in_stop
            diff = stock.recount(conn, dish_id, actual, who(message.from_user.id))
        except stock.StockError as e:
            await message.answer(str(e))
            return
        if actual == 0 and not was_in_stop:  # раздел 5: стало 0, значит стоп
            await went_to_stop(message.bot, stock.get_dish(conn, dish_id))
        report = data["report"]
        if diff:
            name = stock.get_dish(conn, dish_id).name
            report.append(f"• {name}: расхождение {diff:+d}")
        await state.update_data(queue=data["queue"][1:], report=report)
        await ask_next(message, state)

    @router.callback_query(Dialog.recount_qty, F.data.startswith("lost:"))
    async def recount_piece_lost(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        piece_id = int(call.data.split(":")[1])
        grams = conn.execute("SELECT grams FROM pieces WHERE id = ?", (piece_id,)).fetchone()
        try:
            was = stock.get_dish(conn, (await state.get_data())["queue"][0])
            dish = stock.piece_missing(conn, piece_id, who(call.from_user.id))
        except stock.StockError as e:
            await call.message.answer(str(e))
            return
        data = await state.get_data()
        report = data["report"] + [f"• {dish.name}: нет куска {grams['grams']} г"]
        await state.update_data(report=report)
        await call.message.answer(f"Отметил: нет куска {grams['grams']} г. Свободно {dish.free}.")
        if dish.free == 0 and not was.in_stop:
            await went_to_stop(call.bot, dish)

    @router.callback_query(Dialog.recount_qty, F.data == "rcok")
    async def recount_pieces_done(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        data = await state.get_data()
        await state.update_data(queue=data["queue"][1:])
        await ask_next(call.message, state)

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
        dish = stock.get_dish(conn, int(call.data.split(":")[1]))
        pieces = stock.free_pieces(conn, dish.id) if dish.kind == stock.WEIGHT else []
        if pieces:  # весовое: какой кусок пробили (в чеке будет вес)
            await call.message.answer(
                f"{dish.name}: какой кусок пробили?", reply_markup=pieces_keyboard(pieces, "sp")
            )
            return
        result = stock.sell(conn, dish.id, who(call.from_user.id))
        await report_sale(call.bot, result, call.message)

    @router.callback_query(F.data.startswith("sp:"))
    async def sale_piece(call: CallbackQuery) -> None:
        await call.answer()
        try:
            result = stock.sell_piece(conn, int(call.data.split(":")[1]), who(call.from_user.id))
        except stock.StockError as e:
            await call.message.answer(str(e))
            return
        await report_sale(call.bot, result, call.message)

    async def report_sale(bot: Bot, result: stock.SaleResult, message: Message | None = None) -> None:
        """Уведомления раздела 5 после одной продажи. message — тест кнопкой: ответить, что пробито.
        Продажи с кассы бот не подтверждает, иначе су-шефу пришло бы сообщение на каждую порцию."""
        dish = result.dish
        if result.oversold:
            await send_to_chefs(
                bot,
                chefs,
                f"🚨 Шеф, {dish.name} пробили, а порций нет. Остаток остаётся 0.\n"
                "Решите, что делать. Если сделаете порцию, добавьте её кнопкой "
                f"«{BTN_REPLENISH}»: она закроет этот заказ.",
            )
            await to_waiters(bot, f"🚨 {dish.name} пробили, а порций нет. Подойдите к су-шефу.")
            return
        if message is not None:
            await message.answer(f"Пробито: {dish.name}, свободно {dish.free}.")
        if result.low_alert:
            if dish.kind == stock.WEIGHT:
                left = ", ".join(f"{g} г" for _, g in stock.free_pieces(conn, dish.id))
                await send_to_chefs(
                    bot, chefs, f"⚠️ Шеф, {dish.name}: остался {dish.free} кусок ({left})."
                )
            else:
                await send_to_chefs(
                    bot, chefs, f"⚠️ Шеф, {dish.name}: осталось {dish.free} {portions_word(dish.free)}."
                )
        if result.ran_out:
            await went_to_stop(bot, dish)

    # --- Продажи с кассы iiko (раздел 4) -----------------------------------
    async def apply_cash(bot: Bot, orders: list[CashOrder], since: datetime) -> None:
        """Заказы с кассы → карманы блюд. Каждую позицию считаем один раз (cash_positions).

        Отправили на кухню → как продажа; чек оплачен → продано; удалили после кухни →
        вопрос су-шефу. Позиции, отправленные до since, и блюда не на контроле пропускаем.
        """
        for order in orders:
            for item in order.items:
                dish = stock.dish_by_iiko_id(conn, item.product_id)
                if dish is None:
                    continue
                deleted = item.deleted or order.status == "Deleted"
                pos = stock.cash_position(conn, item.position_id)
                if pos is None:
                    if deleted or item.printed is None or item.printed < since:
                        continue  # удалили до кухни, ещё не отправили или было до бота
                    if dish.kind == stock.WEIGHT:  # в чеке вес в кг: 0,55 = кусок 550 г
                        grams = round(item.amount * 1000)
                        result = stock.cash_piece_sale(conn, item.position_id, order.id, dish.id, grams)
                        if result is None:
                            await ask_piece(bot, item.position_id)
                        else:
                            await report_sale(bot, result)
                    elif item.amount != int(item.amount) or item.amount < 1:
                        logging.warning("iiko: %s ×%s, не целое число порций", dish.name, item.amount)
                        continue
                    else:
                        sales = stock.cash_sale(
                            conn, item.position_id, order.id, dish.id, int(item.amount)
                        )
                        for result in sales:
                            await report_sale(bot, result)
                if deleted:
                    stock.cash_unpicked(conn, item.position_id)
                    if stock.cash_refused(conn, item.position_id, item.note or "без комментария"):
                        await ask_refusal(bot, item.position_id)
                elif order.status == "Closed":
                    stock.cash_paid(conn, item.position_id)

    async def ask_piece(bot: Bot, position_id: str) -> None:
        """Раздел 7в, п. 6: в чеке вес, а куска с точно таким весом нет."""
        pos = stock.cash_position(conn, position_id)
        dish = stock.get_dish(conn, pos.dish_id)
        pieces = stock.free_pieces(conn, dish.id)
        have = ", ".join(f"{g}" for _, g in pieces)
        await send_to_chefs(
            bot, chefs,
            f"⚖️ Шеф, {dish.name}: пробили {pos.grams / 1000:.3f}".replace(".", ",")
            + f", а у меня {have} г. Какой снять?",
            reply_markup=pieces_keyboard(pieces, f"cp:{position_id}"),
        )

    @router.callback_query(F.data.startswith("cp:"))
    async def piece_pick(call: CallbackQuery) -> None:
        await call.answer()
        _, position_id, piece_id = call.data.split(":")
        try:
            result = stock.piece_chosen(conn, position_id, int(piece_id), who(call.from_user.id))
        except stock.StockError as e:
            await call.message.answer(str(e))
            return
        await report_sale(call.bot, result, call.message)

    async def ask_refusal(bot: Bot, position_id: str) -> None:
        pos = stock.cash_position(conn, position_id)
        dish = stock.get_dish(conn, pos.dish_id)
        await send_to_chefs(
            bot, chefs,
            f"↩️ Шеф, {dish.name} ×{pos.counted} удалили из заказа после кухни.\n"
            f"В iiko: {pos.note}.\nЧто делаем с порцией?",
            reply_markup=refusal_keyboard(position_id),
        )

    router.apply_cash = apply_cash  # зовёт опрос кассы (cash.py)

    @router.callback_query(F.data.startswith("rfb:"))
    async def refusal_back(call: CallbackQuery) -> None:
        await call.answer()
        try:
            before, dish, n = stock.refusal_return(
                conn, call.data.split(":", 1)[1], who(call.from_user.id)
            )
        except stock.StockError as e:
            await call.message.answer(str(e))
            return
        await after_replenish(call.message, before, dish, n, " (вернули после отказа)")

    @router.callback_query(F.data.startswith("rfw:"))
    async def refusal_write_off(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        try:
            dish = stock.refusal_write_off(conn, call.data.split(":", 1)[1], who(call.from_user.id))
        except stock.StockError as e:
            await call.message.answer(str(e))
            return
        await state.set_state(Dialog.write_off_comment)
        await state.update_data(dish_id=dish.id)
        await call.message.answer(f"Списал: {dish.name}. Напишите одним сообщением, почему.")

    @router.message(Dialog.write_off_comment, F.text, ~F.text.in_(MENU_BUTTONS))
    async def write_off_comment(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        await state.clear()
        stock.add_write_off_comment(conn, data["dish_id"], message.text)
        await message.answer("Записал причину списания.")

    # --- Возврат в продажу (раздел 6) -------------------------------------
    @router.callback_query(F.data.startswith("eta:"))
    async def eta_pick(call: CallbackQuery) -> None:
        await call.answer()
        _, dish_id, hours = call.data.split(":")
        dish = stock.get_dish(conn, int(dish_id))
        stock.clear_reminder(conn, dish.id, "eta")
        stock.clear_reminder(conn, dish.id, "ready")
        due = stock.set_timer(conn, dish.id, int(hours), datetime.now())
        await call.message.answer(
            f"Понял. В {due:%H:%M} спрошу, готово ли {dish.name}."
        )
        await to_waiters(
            call.bot, f"⏱ {dish.name} будет примерно через {hours_text(int(hours))}."
        )

    @router.callback_query(F.data.startswith("ready:"))
    async def ready_answer(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        _, dish_id, yes = call.data.split(":")
        dish = stock.get_dish(conn, int(dish_id))
        stock.clear_reminder(conn, dish.id, "ready")
        if yes == "1":
            await ask_replenish(call.message, state, dish, "сколько порций готово?")
        else:
            await call.message.answer(
                f"{dish.name}: через сколько будет?", reply_markup=eta_keyboard(dish.id)
            )
            stock.set_reminder(conn, dish.id, "eta", datetime.now())

    # --- Настройки, переименование, удаление (раздел 14) -------------------
    async def show_settings(message: Message) -> None:
        await message.answer(
            "Настройки. Нажмите, что поменять.",
            reply_markup=settings_keyboard(
                stock.get_setting(conn, "low_stock"), stock.get_setting(conn, "remind_minutes")
            ),
        )

    @router.message(F.text == BTN_SETTINGS)
    async def settings_open(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show_settings(message)

    @router.callback_query(F.data.in_({"set:low_stock", "set:remind_minutes"}))
    async def setting_pick(call: CallbackQuery) -> None:
        await call.answer()
        key = call.data.split(":")[1]
        title, unit = SETTING_NAMES[key]
        now = stock.get_setting(conn, key)
        row = [
            InlineKeyboardButton(
                text=f"{'✅ ' if v == now else ''}{v}", callback_data=f"setv:{key}:{v}"
            )
            for v in stock.SETTINGS[key][1]
        ]
        await call.message.answer(
            f"{title}. Сейчас {now} {unit}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[row])
        )

    @router.callback_query(F.data.startswith("setv:"))
    async def setting_value(call: CallbackQuery) -> None:
        await call.answer()
        _, key, value = call.data.split(":")
        try:
            stock.set_setting(conn, key, int(value), who(call.from_user.id))
        except (stock.StockError, ValueError):
            await call.message.answer("Такого значения нет среди вариантов.")
            return
        title, unit = SETTING_NAMES[key]
        await call.message.answer(f"Готово. {title}: {value} {unit}")

    @router.callback_query(F.data.in_({"set:rename", "set:delete"}))
    async def dish_manage_pick(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        await state.clear()
        dishes = stock.list_dishes(conn)
        if not dishes:
            await call.message.answer(format_stock(dishes))
            return
        rename = call.data == "set:rename"
        await call.message.answer(
            "Какое блюдо переименовать?" if rename else "Какое блюдо удалить из контроля?",
            reply_markup=dishes_keyboard(dishes, "ren" if rename else "del"),
        )

    @router.callback_query(F.data.startswith("ren:"))
    async def rename_start(call: CallbackQuery, state: FSMContext) -> None:
        await call.answer()
        dish = stock.get_dish(conn, int(call.data.split(":")[1]))
        await state.set_state(Dialog.rename_name)
        await state.update_data(dish_id=dish.id)
        await call.message.answer(f"Новое название для «{dish.name}»? Остатки сохранятся.")

    @router.message(Dialog.rename_name, F.text)
    async def rename_finish(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            old = stock.get_dish(conn, data["dish_id"])
            dish = stock.rename_dish(conn, old.id, message.text, who(message.from_user.id))
        except stock.StockError as e:
            await message.answer(str(e))  # диалог не сбрасываем: можно написать другое
            return
        await state.clear()
        note = "" if stock.get_iiko_id(conn, dish.id) else await iiko_link_note(dish)
        await message.answer(f"Готово: «{old.name}» теперь «{dish.name}».{note}")

    @router.callback_query(F.data.startswith("del:"))
    async def delete_ask(call: CallbackQuery) -> None:
        await call.answer()
        dish = stock.get_dish(conn, int(call.data.split(":")[1]))
        warn = ""
        if dish.free or dish.in_work:
            warn = (
                f"\nВ боте ещё есть: свободно {dish.free}, в работе {dish.in_work}. "
                "После удаления бот перестанет их считать."
            )
        await call.message.answer(
            f"Удалить «{dish.name}» из контроля? История в журнале сохранится.{warn}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Да, удалить", callback_data=f"delok:{dish.id}"),
                InlineKeyboardButton(text="Нет", callback_data="delno"),
            ]]),
        )

    @router.callback_query(F.data.startswith("delok:"))
    async def delete_confirm(call: CallbackQuery) -> None:
        await call.answer()
        try:
            dish = stock.archive_dish(conn, int(call.data.split(":")[1]), who(call.from_user.id))
        except stock.StockError as e:
            await call.message.answer(str(e))
            return
        await call.message.answer(f"Удалил «{dish.name}» из контроля.")

    @router.callback_query(F.data == "delno")
    async def delete_cancel(call: CallbackQuery) -> None:
        await call.answer()
        await call.message.answer("Хорошо, ничего не удаляю.")

    return router
