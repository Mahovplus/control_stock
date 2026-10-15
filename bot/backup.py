"""Резервная копия базы раз в день (раздел 14 logika-bota.md).

Копия кладётся рядом с базой: data/backup/ostatki-2026-10-15.db.
Храним последние 14 копий, старые удаляем. Если компьютер сломается,
достаточно положить свежую копию на место data/ostatki.db.
"""

import sqlite3
from datetime import date
from pathlib import Path

KEEP_DAYS = 14


def backup_dir_for(db_path: str) -> Path | None:
    """Куда класть копии. Для базы в памяти (тесты) копий нет."""
    if db_path == ":memory:":
        return None
    return Path(db_path).resolve().parent / "backup"


def daily_backup(conn: sqlite3.Connection, folder: Path, today: date) -> Path | None:
    """Делает копию за сегодня, если её ещё нет. Возвращает путь новой копии или None."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"ostatki-{today:%Y-%m-%d}.db"
    if target.exists():
        return None
    tmp = target.with_suffix(".tmp")
    copy = sqlite3.connect(tmp)
    try:
        conn.backup(copy)  # штатное копирование SQLite: безопасно, даже пока бот пишет
    finally:
        copy.close()
    tmp.replace(target)  # копия появляется целиком, без полузаписанных файлов
    for old in sorted(folder.glob("ostatki-*.db"))[:-KEEP_DAYS]:
        old.unlink()
    return target
