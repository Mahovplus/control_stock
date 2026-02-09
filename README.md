flowchart TB
  iiko[iiko\n(касса/печать кухни)] -->|событие: напечатан кухонный чек\nпозиции, qty, ids| backend[Backend (наш сервис)]
  chef[Су-шеф / кухня] -->|команды в TG| tg[Telegram Bot]
  tg <--> |HTTP API| backend
  backend <--> db[(БД: остатки + события)]
  backend -->|уведомления| chat[Чат сотрудников (TG)]

  backend -.->|опционально: выставить/снять стоп-лист| iiko
