"""Логика остатков без Telegram и без iiko.

Здесь только правила из logika-bota.md (разделы 3, 5, 6 и 7), чтобы их можно было
проверить тестами. Telegram-кнопки (handlers.py) лишь вызывают эти функции.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_PORTIONS = 999  # защита от опечатки вроде «1000» вместо «10»
LOW_STOCK = 3       # раздел 5: один порог для всех блюд
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
REMIND_AFTER = timedelta(minutes=15)  # раздел 6: су-шеф занят, напоминаем один раз


class StockError(ValueError):
    """Понятная су-шефу ошибка: бот покажет её текст как есть."""


@dataclass
class Dish:
    id: int
    name: str
    free: int
    in_work: int
    in_stop: bool


@dataclass
class SaleResult:
    """Что случилось после продажи и о чём надо сообщить."""

    dish: Dish
    oversold: bool = False  # пробили, а свободных порций не было
    low_alert: bool = False  # стало 3 или меньше, сообщаем первый раз
    ran_out: bool = False    # стало 0, блюдо ушло в стоп


def _to_dish(row: sqlite3.Row) -> Dish:
    return Dish(row["id"], row["name"], row["free"], row["in_work"], bool(row["in_stop"]))


def add_dish(conn: sqlite3.Connection, name: str) -> Dish:
    """Добавляет порционное блюдо в список контроля. Остаток начинается с 0."""
    name = name.strip()
    if not name:
        raise StockError("Название пустое. Напиши, как блюдо называется в меню.")
    try:
        with conn:
            cur = conn.execute("INSERT INTO dishes (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        raise StockError(f"«{name}» уже есть в списке.") from None
    return get_dish(conn, cur.lastrowid)


def get_dish(conn: sqlite3.Connection, dish_id: int) -> Dish:
    row = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    if row is None:
        raise StockError("Такого блюда нет в списке.")
    return _to_dish(row)


def list_dishes(conn: sqlite3.Connection) -> list[Dish]:
    rows = conn.execute("SELECT * FROM dishes ORDER BY name").fetchall()
    return [_to_dish(r) for r in rows]


def parse_count(text: str, allow_zero: bool) -> int:
    """Превращает ответ су-шефа («5», « 12 ») в число и проверяет границы."""
    text = text.strip()
    if not text.isdigit():
        raise StockError("Нужно целое число порций, например 5.")
    n = int(text)
    if n == 0 and not allow_zero:
        raise StockError("Пополнить можно минимум на 1 порцию.")
    if n > MAX_PORTIONS:
        raise StockError(f"Слишком много: больше {MAX_PORTIONS}. Проверь число.")
    return n


def replenish(conn: sqlite3.Connection, dish_id: int, qty: int, who: str) -> Dish:
    """Пополнение (разделы 5 и 7).

    Сначала порции закрывают позиции «сверх остатка»: они уходят «в работу»,
    свободно не растёт, чтобы порцию не продали второй раз. Остальное идёт
    в «свободно». Если свободно стало больше 0, блюдо выходит из стопа.
    """
    if qty <= 0:
        raise StockError("Пополнить можно минимум на 1 порцию.")
    dish = get_dish(conn, dish_id)
    waiting = [r["id"] for r in conn.execute(
        "SELECT id FROM oversells WHERE dish_id = ? AND resolved_at IS NULL ORDER BY id",
        (dish_id,),
    )]
    covered = min(qty, len(waiting))
    to_free = qty - covered
    free_after = dish.free + to_free
    with conn:  # всё внутри либо запишется целиком, либо не запишется вовсе
        conn.execute(
            "UPDATE dishes SET free = ?, in_work = in_work + ?, "
            "in_stop = CASE WHEN ? > 0 THEN 0 ELSE in_stop END, "
            "low_alerted = CASE WHEN ? > ? THEN 0 ELSE low_alerted END "
            "WHERE id = ?",
            (free_after, covered, free_after, free_after, LOW_STOCK, dish_id),
        )
        for oversell_id in waiting[:covered]:
            conn.execute(
                "UPDATE oversells SET resolved_at = datetime('now', 'localtime') WHERE id = ?",
                (oversell_id,),
            )
        conn.execute(
            "INSERT INTO journal (dish_id, action, delta, free_after, who, comment) "
            "VALUES (?, 'replenish', ?, ?, ?, ?)",
            (dish_id, to_free, free_after, who,
             f"{covered} закрыли «сверх остатка»" if covered else None),
        )
        if free_after > 0:  # блюдо в продаже: спрашивать и напоминать не о чем
            conn.execute("DELETE FROM timers WHERE dish_id = ?", (dish_id,))
            conn.execute("DELETE FROM reminders WHERE dish_id = ?", (dish_id,))
    return get_dish(conn, dish_id)


def recount(conn: sqlite3.Connection, dish_id: int, actual: int, who: str) -> int:
    """Утренний пересчёт (раздел 7): ставит фактическое число.

    Возвращает расхождение: факт минус то, что было в боте.
    Плюс — порций оказалось больше, минус — меньше.
    """
    if actual < 0:
        raise StockError("Остаток не может быть меньше 0.")
    dish = get_dish(conn, dish_id)
    diff = actual - dish.free
    with conn:
        conn.execute(
            "UPDATE dishes SET free = ?, "
            "in_stop = CASE WHEN ? = 0 THEN 1 ELSE in_stop END, "  # 0 = в стоп
            "low_alerted = CASE WHEN ? > ? THEN 0 ELSE low_alerted END "
            "WHERE id = ?",
            (actual, actual, actual, LOW_STOCK, dish_id),
        )
        conn.execute(
            "INSERT INTO journal (dish_id, action, delta, free_after, who, comment) "
            "VALUES (?, 'recount', ?, ?, ?, ?)",
            (dish_id, diff, actual, who, "расхождение" if diff else None),
        )
    return diff


def sell(conn: sqlite3.Connection, dish_id: int, who: str) -> SaleResult:
    """Пробили одну порцию (раздел 4): свободно −1, в работе +1.

    Пока iiko не подключена, продажу вводит су-шеф кнопкой для проверки.
    Если свободных порций нет, ниже 0 не уходим: записываем «сверх остатка».
    """
    dish = get_dish(conn, dish_id)
    if dish.free == 0:
        with conn:
            conn.execute("INSERT INTO oversells (dish_id) VALUES (?)", (dish_id,))
            conn.execute(
                "INSERT INTO journal (dish_id, action, delta, free_after, who, comment) "
                "VALUES (?, 'oversell', 0, 0, ?, 'сверх остатка, ждёт решения')",
                (dish_id, who),
            )
        return SaleResult(get_dish(conn, dish_id), oversold=True)

    free_after = dish.free - 1
    already = conn.execute(
        "SELECT low_alerted FROM dishes WHERE id = ?", (dish_id,)
    ).fetchone()["low_alerted"]
    low_alert = 0 < free_after <= LOW_STOCK and not already
    ran_out = free_after == 0
    with conn:
        conn.execute(
            "UPDATE dishes SET free = ?, in_work = in_work + 1, "
            "low_alerted = CASE WHEN ? <= ? THEN 1 ELSE low_alerted END, "
            "in_stop = CASE WHEN ? = 0 THEN 1 ELSE in_stop END "
            "WHERE id = ?",
            (free_after, free_after, LOW_STOCK, free_after, dish_id),
        )
        conn.execute(
            "INSERT INTO journal (dish_id, action, delta, free_after, who) "
            "VALUES (?, 'sale', -1, ?, ?)",
            (dish_id, free_after, who),
        )
    return SaleResult(get_dish(conn, dish_id), low_alert=low_alert, ran_out=ran_out)


def pending_oversells(conn: sqlite3.Connection, dish_id: int) -> int:
    """Сколько позиций «сверх остатка» по блюду ещё ждут решения."""
    return conn.execute(
        "SELECT COUNT(*) FROM oversells WHERE dish_id = ? AND resolved_at IS NULL",
        (dish_id,),
    ).fetchone()[0]


# --- Таймер «когда будет готово» (раздел 6) ------------------------------

def set_timer(conn: sqlite3.Connection, dish_id: int, hours: int, now: datetime) -> datetime:
    """Запоминает, когда спросить «готово?». Новый таймер заменяет старый."""
    due = now + timedelta(hours=hours)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO timers (dish_id, due_at) VALUES (?, ?)",
            (dish_id, due.strftime(TIME_FORMAT)),
        )
    return due


def take_due_timers(conn: sqlite3.Connection, now: datetime) -> list[Dish]:
    """Блюда, по которым пора спросить «готово?». Таймер срабатывает один раз."""
    rows = conn.execute(
        "SELECT dish_id FROM timers WHERE due_at <= ? ORDER BY due_at",
        (now.strftime(TIME_FORMAT),),
    ).fetchall()
    with conn:
        for r in rows:
            conn.execute("DELETE FROM timers WHERE dish_id = ?", (r["dish_id"],))
    return [get_dish(conn, r["dish_id"]) for r in rows]


# --- Напоминание, если су-шеф не ответил (раздел 6) -----------------------

def set_reminder(conn: sqlite3.Connection, dish_id: int, kind: str, now: datetime) -> None:
    """Через 15 минут напомнить один раз. kind: eta (время) или ready (готово?)."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO reminders (dish_id, kind, due_at) VALUES (?, ?, ?)",
            (dish_id, kind, (now + REMIND_AFTER).strftime(TIME_FORMAT)),
        )


def clear_reminder(conn: sqlite3.Connection, dish_id: int, kind: str) -> None:
    """Су-шеф ответил: напоминать не нужно."""
    with conn:
        conn.execute("DELETE FROM reminders WHERE dish_id = ? AND kind = ?", (dish_id, kind))


def take_due_reminders(conn: sqlite3.Connection, now: datetime) -> list[tuple[Dish, str]]:
    """Напоминания, время которых пришло. Каждое срабатывает один раз."""
    rows = conn.execute(
        "SELECT dish_id, kind FROM reminders WHERE due_at <= ? ORDER BY due_at",
        (now.strftime(TIME_FORMAT),),
    ).fetchall()
    with conn:
        for r in rows:
            conn.execute(
                "DELETE FROM reminders WHERE dish_id = ? AND kind = ?", (r["dish_id"], r["kind"])
            )
    return [(get_dish(conn, r["dish_id"]), r["kind"]) for r in rows]
