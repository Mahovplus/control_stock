# Бот учёта остатков на кухне

Telegram-бот для су-шефа: следит за остатками порционных блюд, предупреждает, когда блюдо заканчивается, и сообщает официантам о стопе.

Логика работы утверждена в [docs/logika-bota.md](docs/logika-bota.md). Код пишется строго по этому файлу.

## Что уже умеет (шаг 1 из раздела 12)

- 📋 Остатки: список блюд, свободно / в работе, отметка стопа.
- ➕ Пополнить: выбрать блюдо, ввести количество. Пополнение снимает стоп.
- 🔄 Утренний пересчёт: бот спрашивает факт по каждому блюду и показывает расхождения.
- 🍽 Добавить блюдо в список контроля.
- Бот отвечает только су-шефу (его Telegram ID в настройках).

iiko, уведомления и чат официантов появятся на следующих шагах.

## Как запустить

На Windows проще всего: заполнить `.env` (копия `.env.example`) и дважды кликнуть `zapusk.cmd`. Он сам перейдёт в папку проекта, поставит библиотеки и запустит бота.

Вручную:

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env      # вписать BOT_TOKEN и SOUS_CHEF_ID
python3 -m bot.main
```

## Как проверить

```bash
python3 -m pytest
```

Тесты не ходят в интернет: Telegram в них заменён «фальшивой сессией».

## Файлы

| Файл | Что делает |
|---|---|
| `bot/db.py` | Создаёт базу SQLite: блюда и журнал движений |
| `bot/stock.py` | Правила подсчёта: добавить, пополнить, пересчитать |
| `bot/handlers.py` | Кнопки и диалоги су-шефа в Telegram |
| `bot/config.py` | Читает токен и настройки из `.env` |
| `bot/main.py` | Запуск бота |

## Architecture (Context)

```mermaid
flowchart TB
  subgraph External["Внешние системы"]
    iiko["iiko"]
    tgapi["Telegram Bot API"]
    chat["Чат сотрудников (Telegram)"]
  end

  subgraph OurSystem["Наша система"]
    integr["iiko Integration Worker"]
    core["Core Backend"]
    db[("PostgreSQL")]
    outboxW["Outbox Worker"]
    bot["Bot Service"]
    parse["Validate & Parse (внутренний шаг бота)"]
  end

  iiko -->|"Webhook (push) или Polling (pull)"| integr
  integr -->|"Нормализованное событие (позиции, qty)"| core

  core -->|"Обновить остатки + записать событие"| db
  core -->|"Записать outbox-сообщение (нужно уведомить)"| db

  outboxW -->|"Прочитать pending outbox"| db
  outboxW -->|"sendMessage/editMessage"| tgapi
  tgapi -->|"Доставка"| chat

  tgapi -->|"Updates (команды/кнопки)"| bot
  bot -->|"передать на разбор"| parse
  parse -->|"Команда/действие"| bot
  bot -->|"Запрос/команда в Core"| core
