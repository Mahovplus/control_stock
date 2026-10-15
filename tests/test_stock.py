"""Проверки правил из logika-bota.md без Telegram и без iiko.

Запуск: python -m pytest
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from bot import stock
from bot.db import connect
from bot.handlers import format_stock, make_router


@pytest.fixture
def conn():
    return connect(":memory:")  # база в памяти: каждый тест с чистого листа


def test_new_dish_starts_at_zero(conn):
    dish = stock.add_dish(conn, "  Вителло тоннато ")
    assert (dish.name, dish.free, dish.in_work) == ("Вителло тоннато", 0, 0)


def test_duplicate_and_empty_names_rejected(conn):
    stock.add_dish(conn, "Вителло тоннато")
    with pytest.raises(stock.StockError):
        stock.add_dish(conn, "Вителло тоннато")
    with pytest.raises(stock.StockError):
        stock.add_dish(conn, "   ")


def test_replenish_adds_and_leaves_stop(conn):
    dish = stock.add_dish(conn, "Перчики тоннато")
    conn.execute("UPDATE dishes SET in_stop = 1 WHERE id = ?", (dish.id,))
    after = stock.replenish(conn, dish.id, 10, who="1")
    assert after.free == 10
    assert after.in_stop is False  # раздел 7: пополнение снимает стоп
    row = conn.execute("SELECT action, delta, free_after FROM journal").fetchone()
    assert tuple(row) == ("replenish", 10, 10)


def test_recount_returns_difference_and_logs_it(conn):
    dish = stock.add_dish(conn, "Вителло тоннато")
    stock.replenish(conn, dish.id, 10, who="1")
    diff = stock.recount(conn, dish.id, 8, who="1")
    assert diff == -2
    assert stock.get_dish(conn, dish.id).free == 8
    row = conn.execute(
        "SELECT action, delta, comment FROM journal WHERE action = 'recount'"
    ).fetchone()
    assert tuple(row) == ("recount", -2, "расхождение")


def test_free_never_below_zero_in_database(conn):
    """Раздел 5: остаток в базе не уходит ниже 0 даже при ошибке в коде."""
    dish = stock.add_dish(conn, "Маргарита")
    with pytest.raises(Exception):
        with conn:
            conn.execute("UPDATE dishes SET free = -1 WHERE id = ?", (dish.id,))
    assert stock.get_dish(conn, dish.id).free == 0


@pytest.mark.parametrize("text, zero_ok, expected", [("5", False, 5), (" 12 ", False, 12), ("0", True, 0)])
def test_parse_count_ok(text, zero_ok, expected):
    assert stock.parse_count(text, allow_zero=zero_ok) == expected


@pytest.mark.parametrize("text, zero_ok", [("пять", False), ("-3", False), ("0", False), ("2.5", True), ("1000", True)])
def test_parse_count_rejects(text, zero_ok):
    with pytest.raises(stock.StockError):
        stock.parse_count(text, allow_zero=zero_ok)


def test_format_stock_shows_stop(conn):
    dish = stock.add_dish(conn, "Маргарита")
    conn.execute("UPDATE dishes SET in_stop = 1 WHERE id = ?", (dish.id,))
    assert "Маргарита: 0 / 0 ⛔ стоп" in format_stock(stock.list_dishes(conn))


def test_router_builds(conn):
    assert make_router(conn, [123, 456]) is not None


def test_config_explains_wrong_chef_id(tmp_path, monkeypatch):
    """Типичная ошибка: вместо числового ID вписали имя бота."""
    from bot.config import load_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("BOT_TOKEN=1:abc\nSOUS_CHEF_ID= maritozzocontrol_bot\n", encoding="utf-8")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("SOUS_CHEF_ID", raising=False)
    with pytest.raises(SystemExit) as e:
        load_config()
    assert "SOUS_CHEF_ID должен быть числом" in str(e.value)
    assert "Нет файла .env" not in str(e.value)


def test_config_reads_env_with_spaces(tmp_path, monkeypatch):
    from bot.config import load_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("BOT_TOKEN= 1:abc\nSOUS_CHEF_ID= 123\n", encoding="utf-8")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("SOUS_CHEF_ID", raising=False)
    cfg = load_config()
    assert (cfg.bot_token, cfg.sous_chef_ids) == ("1:abc", [123])


def test_config_reads_several_chefs(tmp_path, monkeypatch):
    from bot.config import load_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("BOT_TOKEN=1:abc\nSOUS_CHEF_ID=123, 456,123\n", encoding="utf-8")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("SOUS_CHEF_ID", raising=False)
    assert load_config().sous_chef_ids == [123, 456]  # повтор убран

    (tmp_path / ".env").write_text("BOT_TOKEN=1:abc\nSOUS_CHEF_ID=1,2,3,4,5\n", encoding="utf-8")
    monkeypatch.delenv("SOUS_CHEF_ID", raising=False)
    with pytest.raises(SystemExit) as e:
        load_config()
    assert "не больше 4" in str(e.value)


def test_sale_moves_portion_to_work_and_alerts_once(conn):
    dish = stock.add_dish(conn, "Вителло тоннато")
    stock.replenish(conn, dish.id, 5, who="1")
    r = stock.sell(conn, dish.id, who="1")
    assert (r.dish.free, r.dish.in_work, r.low_alert) == (4, 1, False)
    assert stock.sell(conn, dish.id, who="1").low_alert is True   # стало 3
    assert stock.sell(conn, dish.id, who="1").low_alert is False  # 2: уже говорили
    stock.sell(conn, dish.id, who="1")
    r = stock.sell(conn, dish.id, who="1")
    assert r.ran_out and r.dish.in_stop and r.dish.free == 0


def test_low_alert_again_only_after_replenish_above_threshold(conn):
    dish = stock.add_dish(conn, "Вителло тоннато")
    stock.replenish(conn, dish.id, 4, who="1")
    assert stock.sell(conn, dish.id, who="1").low_alert
    stock.replenish(conn, dish.id, 1, who="1")  # 4: выше 3, можно предупреждать снова
    assert stock.sell(conn, dish.id, who="1").low_alert


def test_oversell_keeps_zero_and_replenish_covers_it_first(conn):
    dish = stock.add_dish(conn, "Вителло тоннато")
    r = stock.sell(conn, dish.id, who="1")
    assert r.oversold and r.dish.free == 0 and r.dish.in_work == 0
    assert stock.pending_oversells(conn, dish.id) == 1

    conn.execute("UPDATE dishes SET in_stop = 1 WHERE id = ?", (dish.id,))
    after = stock.replenish(conn, dish.id, 1, who="1")
    assert (after.free, after.in_work, after.in_stop) == (0, 1, True)  # не продать дважды
    assert stock.pending_oversells(conn, dish.id) == 0

    after = stock.replenish(conn, dish.id, 3, who="1")
    assert (after.free, after.in_stop) == (3, False)


def test_timer_fires_once_and_is_cleared_by_replenish(conn):
    dish = stock.add_dish(conn, "Вителло тоннато")
    now = datetime(2026, 10, 14, 18, 0)
    assert stock.set_timer(conn, dish.id, 2, now) == datetime(2026, 10, 14, 20, 0)
    assert stock.take_due_timers(conn, now + timedelta(hours=1)) == []
    assert [d.id for d in stock.take_due_timers(conn, now + timedelta(hours=2))] == [dish.id]
    assert stock.take_due_timers(conn, now + timedelta(hours=3)) == []

    stock.set_timer(conn, dish.id, 1, now)
    stock.replenish(conn, dish.id, 2, who="1")  # пополнили раньше: спрашивать не нужно
    assert stock.take_due_timers(conn, now + timedelta(hours=5)) == []


def test_old_database_from_step_1_gets_new_column(tmp_path):
    """У Даниила база уже создана шагом 1: данные должны остаться."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE dishes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,"
        " iiko_id TEXT UNIQUE, free INTEGER NOT NULL DEFAULT 0 CHECK (free >= 0),"
        " in_work INTEGER NOT NULL DEFAULT 0 CHECK (in_work >= 0), sold INTEGER NOT NULL DEFAULT 0,"
        " written_off INTEGER NOT NULL DEFAULT 0, in_stop INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO dishes (name, free) VALUES ('Маргарита', 7);"
    )
    old.commit()
    old.close()
    conn = connect(str(path))
    dish = stock.list_dishes(conn)[0]
    assert (dish.name, dish.free) == ("Маргарита", 7)
    assert stock.sell(conn, dish.id, who="1").dish.free == 6


def test_config_reads_waiters_chat(tmp_path, monkeypatch):
    from bot.config import load_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "BOT_TOKEN=1:abc\nSOUS_CHEF_ID=123\nWAITERS_CHAT_ID= -100500\n", encoding="utf-8"
    )
    for key in ("BOT_TOKEN", "SOUS_CHEF_ID", "WAITERS_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    assert load_config().waiters_chat_id == -100500


@pytest.mark.parametrize("text, expected", [
    ("550 620 480", [550, 620, 480]), ("0,550 0.62", [550, 620]), ("550; 600", [550, 600]),
])
def test_parse_weights(text, expected):
    assert stock.parse_weights(text) == expected


@pytest.mark.parametrize("text", ["", "пятьсот", "0", "6000", "-5"])
def test_parse_weights_rejects(text):
    with pytest.raises(stock.StockError):
        stock.parse_weights(text)


def test_pieces_keep_counters_in_sync(conn):
    dish = stock.add_dish(conn, "Ребро телёнка", stock.WEIGHT)
    stock.add_pieces(conn, dish.id, [550, 620, 480], who="1")
    assert stock.get_dish(conn, dish.id).free == 3
    assert stock.find_piece(conn, dish.id, 560) is None  # точного веса нет: спросим су-шефа
    r = stock.sell_piece(conn, stock.find_piece(conn, dish.id, 620), who="1")
    assert (r.dish.free, r.dish.in_work) == (2, 1)
    assert [g for _, g in stock.free_pieces(conn, dish.id)] == [480, 550]
    assert stock.recent_weights(conn, dish.id) == [480, 620, 550]


def test_weight_oversell_then_piece_goes_to_work(conn):
    dish = stock.add_dish(conn, "Ребро телёнка", stock.WEIGHT)
    assert stock.sell(conn, dish.id, who="1").oversold  # пробили, а кусков нет
    after = stock.add_pieces(conn, dish.id, [550, 600], who="1")
    assert (after.free, after.in_work) == (1, 1)
    states = [r[0] for r in conn.execute("SELECT state FROM pieces ORDER BY id")]
    assert states == ["in_work", "free"]


def test_portion_dish_refuses_pieces(conn):
    dish = stock.add_dish(conn, "Вителло тоннато")
    with pytest.raises(stock.StockError):
        stock.add_pieces(conn, dish.id, [500], who="1")


def test_rename_keeps_stock_and_checks_duplicates(conn):
    a = stock.add_dish(conn, "Тирамису")
    stock.add_dish(conn, "Панна котта")
    stock.replenish(conn, a.id, 5, "1")
    assert stock.rename_dish(conn, a.id, " Тирамису классик ", "1").name == "Тирамису классик"
    assert stock.get_dish(conn, a.id).free == 5
    with pytest.raises(stock.StockError, match="уже есть"):
        stock.rename_dish(conn, a.id, "Панна котта", "1")


def test_archived_dish_hidden_and_name_free_again(conn):
    a = stock.add_dish(conn, "Тирамису")
    stock.replenish(conn, a.id, 2, "1")
    stock.set_timer(conn, a.id, 1, datetime(2026, 1, 1, 12))
    stock.archive_dish(conn, a.id, "1")
    assert stock.list_dishes(conn) == []
    assert stock.take_due_timers(conn, datetime(2026, 1, 2)) == []  # таймер снят
    assert stock.add_dish(conn, "Тирамису").id != a.id  # имя можно занять снова
    last = conn.execute("SELECT action, comment FROM journal WHERE dish_id = ? ORDER BY id DESC", (a.id,)).fetchone()
    assert last["action"] == "archive" and "свободно было 2" in last["comment"]


def test_settings_change_threshold_and_reminder(conn):
    a = stock.add_dish(conn, "Тирамису")
    assert stock.get_setting(conn, "low_stock") == 3
    stock.set_setting(conn, "low_stock", 5, "1")
    stock.replenish(conn, a.id, 6, "1")
    assert stock.sell(conn, a.id, "1").low_alert  # 5 — уже «мало»
    with pytest.raises(stock.StockError):
        stock.set_setting(conn, "low_stock", 50, "1")  # только варианты с кнопок
    stock.set_setting(conn, "remind_minutes", 30, "1")
    now = datetime(2026, 1, 1, 12)
    stock.set_reminder(conn, a.id, "eta", now)
    assert stock.take_due_reminders(conn, now + timedelta(minutes=29)) == []
    assert len(stock.take_due_reminders(conn, now + timedelta(minutes=30))) == 1


def test_daily_backup_once_a_day_keeps_14(tmp_path):
    from datetime import date

    from bot.backup import KEEP_DAYS, daily_backup

    conn = connect(str(tmp_path / "ostatki.db"))
    stock.add_dish(conn, "Тирамису")
    folder = tmp_path / "backup"
    first = daily_backup(conn, folder, date(2026, 10, 1))
    assert first.name == "ostatki-2026-10-01.db"
    assert daily_backup(conn, folder, date(2026, 10, 1)) is None  # второй раз за день не копирует
    copy = sqlite3.connect(first)
    assert copy.execute("SELECT name FROM dishes").fetchone()[0] == "Тирамису"
    copy.close()
    for day in range(2, 21):
        daily_backup(conn, folder, date(2026, 10, day))
    names = sorted(p.name for p in folder.glob("ostatki-*.db"))
    assert len(names) == KEEP_DAYS and names[0] == "ostatki-2026-10-07.db"
