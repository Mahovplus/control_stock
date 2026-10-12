"""Логика остатков без Telegram и без iiko.

Здесь только правила из logika-bota.md (разделы 3 и 7), чтобы их можно было
проверить тестами. Telegram-кнопки (handlers.py) лишь вызывают эти функции.
"""

import sqlite3
from dataclasses import dataclass

MAX_PORTIONS = 999  # защита от опечатки вроде «1000» вместо «10»


class StockError(ValueError):
    """Понятная су-шефу ошибка: бот покажет её текст как есть."""


@dataclass
class Dish:
    id: int
    name: str
    free: int
    in_work: int
    in_stop: bool


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
    """Пополнение (раздел 7): свободно + qty, блюдо выходит из стопа."""
    if qty <= 0:
        raise StockError("Пополнить можно минимум на 1 порцию.")
    dish = get_dish(conn, dish_id)
    with conn:  # всё внутри либо запишется целиком, либо не запишется вовсе
        conn.execute(
            "UPDATE dishes SET free = free + ?, in_stop = 0 WHERE id = ?", (qty, dish_id)
        )
        conn.execute(
            "INSERT INTO journal (dish_id, action, delta, free_after, who) "
            "VALUES (?, 'replenish', ?, ?, ?)",
            (dish_id, qty, dish.free + qty, who),
        )
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
        conn.execute("UPDATE dishes SET free = ? WHERE id = ?", (actual, dish_id))
        conn.execute(
            "INSERT INTO journal (dish_id, action, delta, free_after, who, comment) "
            "VALUES (?, 'recount', ?, ?, ?, ?)",
            (dish_id, diff, actual, who, "расхождение" if diff else None),
        )
    return diff
