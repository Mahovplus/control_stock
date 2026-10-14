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
    waiters_chat_id: int | None = None  # чат официантов; пусто = не настроен


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
    # Говорим точно, что не так: человеку без опыта так проще найти ошибку
    problems = []
    if not token:
        problems.append("BOT_TOKEN пустой: впишите токен от @BotFather")
    if not chef.isdigit():
        problems.append(
            f"SOUS_CHEF_ID должен быть числом из @userinfobot, сейчас там: «{chef}»"
        )
    waiters = os.environ.get("WAITERS_CHAT_ID", "")
    if waiters and not waiters.lstrip("-").isdigit():
        problems.append(
            f"WAITERS_CHAT_ID должен быть числом (у групп с минусом), сейчас там: «{waiters}»"
        )
    if problems and not os.path.exists(".env"):
        problems.insert(0, f"Нет файла .env в папке {os.getcwd()} (образец: .env.example)")
    if problems:
        raise SystemExit("Не могу запустить бота:\n- " + "\n- ".join(problems))
    return Config(
        token,
        int(chef),
        os.environ.get("DB_PATH", "data/ostatki.db"),
        int(waiters) if waiters else None,
    )
