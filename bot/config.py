"""Настройки бота из переменных окружения (файл .env на сервере).

Секреты (токен) не хранятся в коде: код лежит на GitHub, а .env — только на сервере.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    bot_token: str
    sous_chef_id: int
    db_path: str


def load_env_file(path: str = ".env") -> None:
    """Читает строки вида КЛЮЧ=значение из .env, если файл есть."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_config() -> Config:
    load_env_file()
    token = os.environ.get("BOT_TOKEN", "")
    chef = os.environ.get("SOUS_CHEF_ID", "")
    if not token or not chef.isdigit():
        raise SystemExit("Заполни BOT_TOKEN и SOUS_CHEF_ID в файле .env (образец: .env.example)")
    return Config(token, int(chef), os.environ.get("DB_PATH", "data/ostatki.db"))
