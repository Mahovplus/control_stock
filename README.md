## Architecture (Context)

```mermaid
flowchart TB
  subgraph External["Внешние системы"]
    iiko["iiko"]
    tgapi["Telegram API"]
  end

  subgraph OurSystem["Наша система"]
    integr["iiko Integration<br/>webhook receiver или polling worker"]
    api["Backend API<br/>правила списания, пороги, идемпотентность"]
    bot["Bot Service<br/>команды, кнопки, UI"]
    db[("PostgreSQL<br/>stock, dishes, events")]
  end

  iiko -->|"push: webhook (если доступно)"| integr
  integr -->|"pull: polling (если webhook нет)"| iiko

  integr -->|"KitchenPrintEvent"| api
  bot -->|"HTTP"| api

  api <--> db
  bot --> tgapi
  api --> tgapi

  api -.->|"опционально: stoplist on/off"| iiko
