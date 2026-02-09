## Architecture (Context)

```mermaid
flowchart TB
  subgraph External["Внешние системы"]
    iiko["iiko"]
    tgapi["Telegram API"]
  end

  subgraph OurSystem["Наша система"]
    integr["iiko Integration<br/>webhook receiver или polling worker"]
    core["Backend Core API<br/>остатки, пороги, правила, идемпотентность"]
    notify["Notification/Bot Service<br/>формат сообщений, кнопки, отправка"]
    db[("PostgreSQL<br/>stock, events, outbox")]
  end

  iiko -->|"push: webhook (если доступно)"| integr
  integr -->|"pull: polling (если webhook нет)"| iiko

  integr -->|"Sale/KitchenPrint event"| core
  core <--> db

  core -->|"write outbox событие"| db
  notify -->|"read outbox (pending)"| db
  notify -->|"sendMessage/editMessage"| tgapi

  core -.->|"опционально: stoplist on/off"| iiko
