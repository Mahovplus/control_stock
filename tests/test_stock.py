"""Проверки правил из logika-bota.md без Telegram и без iiko.

Запуск: python -m pytest
"""

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
    assert make_router(conn, sous_chef_id=123) is not None


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
    assert (cfg.bot_token, cfg.sous_chef_id) == ("1:abc", 123)
