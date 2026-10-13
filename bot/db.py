"""База данных SQLite: открыть файл и создать таблицы.

Таблицы повторяют раздел 9 файла logika-bota.md:
- dishes: порционные блюда и их «карманы» (свободно, в работе, продано, списано);
- journal: каждое движение порции, чтобы всегда можно было понять, откуда цифра.
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
    in_stop     INTEGER NOT NULL DEFAULT 0      -- 1 = блюдо в стоп-листе
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
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Открывает базу (создаёт файл и папку, если их нет) и готовит таблицы."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # строки можно читать по имени колонки
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
