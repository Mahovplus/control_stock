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
