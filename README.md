## Architecture (Context)

```mermaid
flowchart TB
  iiko["iiko<br/>касса / печать кухни"] -->|"событие: напечатан кухонный чек<br/>позиции, qty, ids"| backend["Backend<br/>наш сервис"]
  chef["Су-шеф / кухня"] -->|"команды в TG"| bot["Telegram Bot<br/>UI"]
  bot <-->|"HTTP"| backend
  backend <--> db[("БД<br/>остатки + события")]
  backend -->|"уведомления"| chat["Чат сотрудников (TG)"]

  backend -.->|"опционально: выставить/снять стоп-лист"| iiko

