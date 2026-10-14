"""База данных SQLite: открыть файл и создать таблицы.

Таблицы повторяют раздел 9 файла logika-bota.md:
- dishes: порционные блюда и их «карманы» (свободно, в работе, продано, списано);
- journal: каждое движение порции, чтобы всегда можно было понять, откуда цифра;
- oversells: пробили сверх остатка, ждём решения су-шефа (раздел 5);
- timers: когда спросить су-шефа «готово?» (раздел 6).
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dishes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    iiko_id     TEXT UNIQUE,                    -- код блюда в iiko, появится на шаге 3
    free        INTEGER NOT NULL DEFAULT 0 CHECK (free >= 0),  -- ниже нуля не уходим
    in_work     INTEGER NOT NULL DEFAULT 0 CHECK (in_work >= 0),
    sold        INTEGER NOT NULL DEFAULT 0,
    written_off INTEGER NOT NULL DEFAULT 0,
    in_stop     INTEGER NOT NULL DEFAULT 0,     -- 1 = блюдо в стоп-листе
    low_alerted INTEGER NOT NULL DEFAULT 0      -- 1 = «осталось 3» уже отправили
);

CREATE TABLE IF NOT EXISTS journal (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    dish_id   INTEGER NOT NULL REFERENCES dishes(id),
    action    TEXT NOT NULL,   -- replenish, recount, ...
    delta     INTEGER NOT NULL, -- на сколько изменилось «свободно»
    free_after INTEGER NOT NULL,
    who       TEXT,            -- Telegram ID того, кто сделал
    comment   TEXT
);

CREATE TABLE IF NOT EXISTS oversells (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    dish_id     INTEGER NOT NULL REFERENCES dishes(id),
    resolved_at TEXT                -- пусто = ещё ждёт пополнения
);

CREATE TABLE IF NOT EXISTS timers (
    dish_id INTEGER PRIMARY KEY REFERENCES dishes(id),  -- один таймер на блюдо
    due_at  TEXT NOT NULL           -- когда спросить «готово?», местное время
);
"""

# Колонки, добавленные после шага 1. У тех, у кого база уже создана,
# CREATE TABLE IF NOT EXISTS их не добавит, поэтому добавляем отдельно.
NEW_COLUMNS = {
    "dishes": [("low_alerted", "INTEGER NOT NULL DEFAULT 0")],
}


def connect(db_path: str) -> sqlite3.Connection:
    """Открывает базу (создаёт файл и папку, если их нет) и готовит таблицы."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # строки можно читать по имени колонки
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    return conn


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Обновляет старую базу: данные остаются, появляются новые колонки."""
    for table, columns in NEW_COLUMNS.items():
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    conn.commit()
