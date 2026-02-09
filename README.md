```mermaid
flowchart TB
  iiko{iiko\nПечать чека} -->|push webhook ИЛИ pull polling| svc[Сервис / BOT]
  chef[Су-шеф] -->|Пополнение / корректировки| svc
  svc -->|Оповещения: ограничение / стоп| chat[Чат сотрудников]
  svc -->|write| db[(БД)]
  db -->|read| svc
  svc -.->|опционально: выставить/снять стоп-лист| iiko
