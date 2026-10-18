"""Логика остатков без Telegram и без iiko.

Здесь только правила из logika-bota.md (разделы 3, 5, 6, 7 и 7в), чтобы их можно было
проверить тестами (плюс раздел 14: настройки, переименование, удаление). Telegram-кнопки (handlers.py) лишь вызывают эти функции.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_PORTIONS = 999  # защита от опечатки вроде «1000» вместо «10»
LOW_STOCK = 3       # раздел 5: порог для порционных блюд по умолчанию (меняется в настройках)
LOW_PIECES = 1      # раздел 7в: для штучных весовых — «остался 1 кусок»
MAX_GRAMS = 5000    # кусок тяжелее 5 кг — скорее всего опечатка
PORTION, WEIGHT = "portion", "weight"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
REMIND_MINUTES = 15  # раздел 6: су-шеф занят, напоминаем один раз (меняется в настройках)

# Раздел 14: что су-шеф может поменять кнопками. Значение по умолчанию и варианты.
SETTINGS = {
    "low_stock": (LOW_STOCK, (2, 3, 4, 5)),
    "remind_minutes": (REMIND_MINUTES, (10, 15, 20, 30)),
}


class StockError(ValueError):
    """Понятная су-шефу ошибка: бот покажет её текст как есть."""


@dataclass
class Dish:
    id: int
    name: str
    free: int
    in_work: int
    in_stop: bool
    kind: str = PORTION
    low_stock: int = LOW_STOCK  # «осталось мало»: для весовых всегда LOW_PIECES


@dataclass
class SaleResult:
    """Что случилось после продажи и о чём надо сообщить."""

    dish: Dish
    oversold: bool = False  # пробили, а свободных порций не было
    low_alert: bool = False  # стало 3 или меньше, сообщаем первый раз
    ran_out: bool = False    # стало 0, блюдо ушло в стоп


def _to_dish(conn: sqlite3.Connection, row: sqlite3.Row) -> Dish:
    low = LOW_PIECES if row["kind"] == WEIGHT else get_setting(conn, "low_stock")
    return Dish(
        row["id"], row["name"], row["free"], row["in_work"], bool(row["in_stop"]), row["kind"], low
    )


# --- Настройки кнопками (раздел 14) ---------------------------------------

def get_setting(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else SETTINGS[key][0]


def set_setting(conn: sqlite3.Connection, key: str, value: int, who: str) -> None:
    """Меняет настройку. Принимает только варианты с кнопок, чтобы не было опечаток."""
    if key not in SETTINGS or value not in SETTINGS[key][1]:
        raise StockError("Такого значения нет среди вариантов.")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, changed_by) VALUES (?, ?, ?)",
            (key, value, who),
        )


def add_dish(conn: sqlite3.Connection, name: str, kind: str = PORTION) -> Dish:
    """Добавляет блюдо в список контроля. Остаток начинается с 0.

    kind: portion — порционное, weight — штучное весовое (раздел 7в).
    """
    name = name.strip()
    if not name:
        raise StockError("Название пустое. Напиши, как блюдо называется в меню.")
    try:
        with conn:
            cur = conn.execute("INSERT INTO dishes (name, kind) VALUES (?, ?)", (name, kind))
    except sqlite3.IntegrityError:
        raise StockError(f"«{name}» уже есть в списке.") from None
    return get_dish(conn, cur.lastrowid)


def set_iiko_id(conn: sqlite3.Connection, dish_id: int, iiko_id: str) -> None:
    """Связь блюда с меню iiko (раздел 8): по этому коду бот ставит и снимает стоп."""
    with conn:
        conn.execute("UPDATE dishes SET iiko_id = ? WHERE id = ?", (iiko_id, dish_id))


def get_iiko_id(conn: sqlite3.Connection, dish_id: int) -> str | None:
    row = conn.execute("SELECT iiko_id FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    return row["iiko_id"] if row else None


def unlinked_dishes(conn: sqlite3.Connection) -> list[Dish]:
    """Блюда на контроле, которые ещё не связаны с iiko."""
    rows = conn.execute(
        "SELECT * FROM dishes WHERE archived = 0 AND iiko_id IS NULL ORDER BY name"
    ).fetchall()
    return [_to_dish(conn, r) for r in rows]


def get_dish(conn: sqlite3.Connection, dish_id: int) -> Dish:
    row = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    if row is None:
        raise StockError("Такого блюда нет в списке.")
    return _to_dish(conn, row)


def list_dishes(conn: sqlite3.Connection) -> list[Dish]:
    """Блюда на контроле. Удалённые (архив) не показываем."""
    rows = conn.execute("SELECT * FROM dishes WHERE archived = 0 ORDER BY name").fetchall()
    return [_to_dish(conn, r) for r in rows]


def rename_dish(conn: sqlite3.Connection, dish_id: int, new_name: str, who: str) -> Dish:
    """Переименование. Остатки и история остаются у того же блюда."""
    new_name = new_name.strip()
    if not new_name:
        raise StockError("Название пустое. Напишите новое название.")
    old = get_dish(conn, dish_id)
    try:
        with conn:
            conn.execute("UPDATE dishes SET name = ? WHERE id = ?", (new_name, dish_id))
            conn.execute(
                "INSERT INTO journal (dish_id, action, delta, free_after, who, comment) "
                "VALUES (?, 'rename', 0, ?, ?, ?)",
                (dish_id, old.free, who, f"было «{old.name}»"),
            )
    except sqlite3.IntegrityError:
        raise StockError(f"«{new_name}» уже есть в списке.") from None
    return get_dish(conn, dish_id)


def archive_dish(conn: sqlite3.Connection, dish_id: int, who: str) -> Dish:
    """Удаление блюда из контроля. Строку не стираем: журнал продаж ссылается на неё.

    Блюдо прячется из списков, таймеры и напоминания по нему снимаются, а к названию
    добавляется «[удалено N]», чтобы потом можно было завести блюдо с тем же именем.
    """
    dish = get_dish(conn, dish_id)
    with conn:
        conn.execute(
            "UPDATE dishes SET archived = 1, iiko_id = NULL, name = ? WHERE id = ?",
            (f"{dish.name} [удалено {dish_id}]", dish_id),
        )
        conn.execute("DELETE FROM timers WHERE dish_id = ?", (dish_id,))
        conn.execute("DELETE FROM reminders WHERE dish_id = ?", (dish_id,))
        conn.execute(
            "INSERT INTO journal (dish_id, action, delta, free_after, who, comment) "
            "VALUES (?, 'archive', 0, ?, ?, ?)",
            (dish_id, dish.free, who, f"удалено «{dish.name}», свободно было {dish.free}"),
        )
    return dish


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
            (free_after, covered, free_after, free_after, dish.low_stock, dish_id),
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
            (actual, actual, actual, dish.low_stock, dish_id),
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
    low_alert = 0 < free_after <= dish.low_stock and not already
    ran_out = free_after == 0
    with conn:
        conn.execute(
            "UPDATE dishes SET free = ?, in_work = in_work + 1, "
            "low_alerted = CASE WHEN ? <= ? THEN 1 ELSE low_alerted END, "
            "in_stop = CASE WHEN ? = 0 THEN 1 ELSE in_stop END "
            "WHERE id = ?",
            (free_after, free_after, dish.low_stock, free_after, dish_id),
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
    """Через N минут (настройка, обычно 15) напомнить один раз. kind: eta или ready."""
    due = now + timedelta(minutes=get_setting(conn, "remind_minutes"))
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO reminders (dish_id, kind, due_at) VALUES (?, ?, ?)",
            (dish_id, kind, due.strftime(TIME_FORMAT)),
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


# --- Штучные весовые блюда (раздел 7в) ------------------------------------
# «Свободно» у такого блюда = число свободных кусков. Счётчики в dishes ведут
# те же функции, что и для порций (replenish, sell, recount), а таблица pieces
# помнит, какой именно кусок где.

def parse_weights(text: str) -> list[int]:
    """«550 620 480» → [550, 620, 480]. Можно в килограммах: «0,550» → 550."""
    tokens = text.replace(";", " ").split()
    if not tokens:
        raise StockError("Напишите веса кусков в граммах через пробел, например 550 620 480.")
    grams = []
    for t in tokens:
        t = t.replace(",", ".")
        try:
            value = float(t)
        except ValueError:
            raise StockError(f"«{t}» не похоже на вес. Пример: 550 620 480.") from None
        g = round(value * 1000) if "." in t and value < 10 else round(value)
        if not 0 < g <= MAX_GRAMS:
            raise StockError(f"Вес {t} вне границ: от 1 до {MAX_GRAMS} г. Проверьте.")
        grams.append(g)
    return grams


def add_pieces(conn: sqlite3.Connection, dish_id: int, grams: list[int], who: str) -> Dish:
    """Пополнение весового блюда кусками. Правила те же, что у replenish:
    сначала закрываются заказы «сверх остатка», эти куски сразу уходят «в работу»."""
    before = get_dish(conn, dish_id)
    if before.kind != WEIGHT:
        raise StockError("Это порционное блюдо: пополняйте количеством порций.")
    after = replenish(conn, dish_id, len(grams), who)
    covered = len(grams) - (after.free - before.free)
    with conn:
        for i, g in enumerate(grams):
            conn.execute(
                "INSERT INTO pieces (dish_id, grams, state) VALUES (?, ?, ?)",
                (dish_id, g, "in_work" if i < covered else "free"),
            )
    return after


def free_pieces(conn: sqlite3.Connection, dish_id: int) -> list[tuple[int, int]]:
    """Свободные куски блюда: [(id куска, граммы)], от лёгкого к тяжёлому."""
    rows = conn.execute(
        "SELECT id, grams FROM pieces WHERE dish_id = ? AND state = 'free' ORDER BY grams, id",
        (dish_id,),
    ).fetchall()
    return [(r["id"], r["grams"]) for r in rows]


def recent_weights(conn: sqlite3.Connection, dish_id: int, limit: int = 6) -> list[int]:
    """Последние разные веса этого блюда: для быстрых кнопок при пополнении."""
    rows = conn.execute(
        "SELECT grams FROM pieces WHERE dish_id = ? GROUP BY grams ORDER BY MAX(id) DESC LIMIT ?",
        (dish_id, limit),
    ).fetchall()
    return [r["grams"] for r in rows]


def _piece(conn: sqlite3.Connection, piece_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM pieces WHERE id = ?", (piece_id,)).fetchone()
    if row is None or row["state"] != "free":
        raise StockError("Этого куска уже нет среди свободных.")
    return row


def sell_piece(conn: sqlite3.Connection, piece_id: int, who: str) -> SaleResult:
    """Пробили конкретный кусок: он уходит «в работу», счётчики как у порции."""
    piece = _piece(conn, piece_id)
    with conn:
        conn.execute("UPDATE pieces SET state = 'in_work' WHERE id = ?", (piece_id,))
    return sell(conn, piece["dish_id"], who)


def find_piece(conn: sqlite3.Connection, dish_id: int, grams: int) -> int | None:
    """Свободный кусок точно такого веса (для чека iiko). Нет — None, спросим су-шефа."""
    for piece_id, g in free_pieces(conn, dish_id):
        if g == grams:
            return piece_id
    return None


def piece_missing(conn: sqlite3.Connection, piece_id: int, who: str) -> Dish:
    """Пересчёт: куска нет на месте. Это расхождение −1, при 0 блюдо в стоп."""
    piece = _piece(conn, piece_id)
    dish = get_dish(conn, piece["dish_id"])
    with conn:
        conn.execute("UPDATE pieces SET state = 'lost' WHERE id = ?", (piece_id,))
    recount(conn, dish.id, dish.free - 1, who)
    with conn:
        conn.execute(
            "UPDATE journal SET comment = ? WHERE id = (SELECT MAX(id) FROM journal)",
            (f"расхождение: нет куска {piece['grams']} г",),
        )
    return get_dish(conn, dish.id)
