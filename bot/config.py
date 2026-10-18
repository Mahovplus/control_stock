"""Настройки бота из переменных окружения (файл .env на сервере).

Секреты (токен) не хранятся в коде: код лежит на GitHub, а .env — только на сервере.
"""

import os
from dataclasses import dataclass

MAX_CHEFS = 4
# Ключи iiko Cloud API: либо все три, либо ни одного
IIKO_KEYS = ("IIKO_API_KEY", "IIKO_APP_ID", "IIKO_CLIENT_SECRET")


@dataclass
class Config:
    bot_token: str
    sous_chef_ids: list[int]  # 1–4 су-шефа (раздел 14)
    db_path: str
    waiters_chat_id: int | None = None  # чат официантов; пусто = не настроен
    iiko: dict | None = None  # ключи Cloud API; пусто = стоп в iiko вручную


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
    # Несколько су-шефов через запятую: 111111111,222222222
    chefs = [c.strip() for c in chef.split(",") if c.strip()]
    # Говорим точно, что не так: человеку без опыта так проще найти ошибку
    problems = []
    if not token:
        problems.append("BOT_TOKEN пустой: впишите токен от @BotFather")
    if not chefs or not all(c.isdigit() for c in chefs):
        problems.append(
            "SOUS_CHEF_ID должен быть числом из @userinfobot "
            f"(несколько су-шефов через запятую), сейчас там: «{chef}»"
        )
    elif len(chefs) > MAX_CHEFS:
        problems.append(f"SOUS_CHEF_ID: не больше {MAX_CHEFS} су-шефов, сейчас {len(chefs)}")
    waiters = os.environ.get("WAITERS_CHAT_ID", "")
    if waiters and not waiters.lstrip("-").isdigit():
        problems.append(
            f"WAITERS_CHAT_ID должен быть числом (у групп с минусом), сейчас там: «{waiters}»"
        )
    iiko_keys = {k: os.environ.get(k, "").strip() for k in IIKO_KEYS}
    if any(iiko_keys.values()) and not all(iiko_keys.values()):
        missing = ", ".join(k for k, v in iiko_keys.items() if not v)
        problems.append(f"Для iiko не хватает: {missing} (или удалите все ключи IIKO_, тогда без iiko)")
    if problems and not os.path.exists(".env"):
        problems.insert(0, f"Нет файла .env в папке {os.getcwd()} (образец: .env.example)")
    if problems:
        raise SystemExit("Не могу запустить бота:\n- " + "\n- ".join(problems))
    return Config(
        token,
        [int(c) for c in dict.fromkeys(chefs)],  # повтор одного ID не считаем
        os.environ.get("DB_PATH", "data/ostatki.db"),
        int(waiters) if waiters else None,
        _iiko_settings(iiko_keys) if all(iiko_keys.values()) else None,
    )


def _iiko_settings(keys: dict) -> dict:
    """Аргументы для IikoClient. Организацию и группу касс можно не указывать."""
    return {
        "api_key": keys["IIKO_API_KEY"],
        "app_id": keys["IIKO_APP_ID"],
        "client_secret": keys["IIKO_CLIENT_SECRET"],
        "organization_id": os.environ.get("IIKO_ORG_ID", "").strip() or None,
        "terminal_group_id": os.environ.get("IIKO_TERMINAL_GROUP_ID", "").strip() or None,
    }
