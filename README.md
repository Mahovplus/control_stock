# control_stock
Телеграмм-бот работающий в связке IIKO для отслеживания и контроля остатков позиционных блюд на кухне ресторана

flowchart TB
  iiko[iiko\n(касса/печать кухни)] -->|событие: напечатан кухонный чек\nпозиции, qty, ids| backend[Backend (наш сервис)]
  chef[Су-шеф / кухня] -->|команды в TG| tg[Telegram Bot]
  tg <--> |HTTP API| backend
  backend <--> db[(БД: остатки + события)]
  backend -->|уведомления| chat[Чат сотрудников (TG)]

  backend -.->|опционально: выставить/снять стоп-лист| iiko


  flowchart TB
  subgraph External[Внешние системы]
    iiko[iiko]
    tgapi[Telegram API]
  end

  subgraph OurSystem[Наша система]
    bot[Bot Service\n(команды, кнопки, UI)]
    api[Backend API\n(доменные правила, уведомления)]
    integr[iiko Integration\n(Webhook receiver ИЛИ Polling worker)]
    db[(PostgreSQL)\nstock, dishes, events]
  end

  iiko -->|webhook push\n(если возможно)| integr
  integr -->|polling pull\n(если webhook нет)| iiko

  integr -->|KitchenPrintEvent| api
  bot -->|HTTP| api

  api <--> db
  bot --> tgapi
  api --> tgapi

  api -.->|опционально:\nstoplist on/off| iiko
