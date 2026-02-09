## Architecture (Context)

```mermaid
flowchart TB
  %% ========== EXTERNAL ==========
  subgraph External["Внешние системы"]
    iiko["iiko (POS/RMS)<br/>чеки / кухонная печать"]
    tgapi["Telegram Bot API"]
    staffChat["Чат сотрудников<br/>(Telegram group)"]
    staffUser["Сотрудник<br/>(мобильный клиент TG)"]
  end

  %% ========== OUR SYSTEM ==========
  subgraph OurSystem["Наша система"]
    integr["iiko Integration Worker<br/>Webhook receiver или Polling"]
    core["Core Backend API<br/>домен: списания, остатки, пороги"]
    db[("PostgreSQL<br/>stock, sales_events, outbox, inbox")]
    outboxWorker["Outbox Publisher<br/>читает outbox -> отправляет уведомления"]
    bot["Bot Service<br/>Updates + команды + кнопки"]
    formatter["Message Formatter<br/>шаблоны, локализация"]
    retryStore[("Retry/DLQ<br/>таблица/очередь")]
  end

  %% ========== IIKO -> CORE ==========
  iiko -->|"push: webhook (если доступно)"| integr
  integr -->|"pull: polling (если webhook нет)"| iiko
  integr -->|"normalize event<br/>KitchenPrint/Sale"| core

  %% ========== CORE + DB ==========
  core -->|"write sales_event"| db
  core -->|"update stock"| db
  core -->|"write outbox message<br/>LowStock/OutOfStock"| db
  core -->|"write inbox record<br/>idempotency key"| db

  %% ========== OUTBOX -> TELEGRAM ==========
  outboxWorker -->|"read pending outbox"| db
  outboxWorker -->|"render request"| formatter
  formatter -->|"text + buttons payload"| outboxWorker
  outboxWorker -->|"sendMessage/editMessage"| tgapi
  tgapi -->|"delivers message"| staffChat

  %% ========== STAFF INTERACTION -> BOT -> CORE ==========
  staffUser -->|"reads/writes"| staffChat
  staffUser -->|"commands/clicks"| tgapi
  tgapi -->|"updates webhook OR long polling"| bot
  bot -->|"validate + parse command"| bot
  bot -->|"HTTP: query/command<br/>get stock / ack alert"| core
  core -->|"read stock/events"| db
  core -->|"optional: write outbox (reply)"| db
  bot -->|"sendMessage/answerCallbackQuery"| tgapi

  %% ========== FAILURES / RETRIES ==========
  outboxWorker -.->|"on error -> schedule retry"| retryStore
  retryStore -.->|"retry later"| outboxWorker
