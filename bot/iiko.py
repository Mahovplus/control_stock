"""Связь с iiko через Cloud API (раздел 8 logika-bota.md).

Бот здесь ставит и снимает стоп, берёт список блюд, чтобы связать свои блюда
с меню iiko, и читает заказы с кассы (раздел 4). Всё проверено на демо-стенде 18.10.2026
(заметки: bot-ostatkov/iiko-stend.md).

Ключи берутся из .env (IIKO_API_KEY, IIKO_APP_ID, IIKO_CLIENT_SECRET). Если их нет,
бот работает как раньше и просит су-шефа ставить стоп в iiko вручную.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp

BASE_URL = "https://api-ru.iiko.services"
TOKEN_LIFETIME = 50 * 60  # токен живёт около часа, обновляем заранее
STATUS_TRIES = 6           # сколько раз спросить, выполнила ли касса команду
STATUS_PAUSE = 2           # секунд между вопросами


class IikoError(Exception):
    """Понятная су-шефу ошибка iiko: бот покажет её текст как есть."""


@dataclass
class IikoDish:
    id: str
    name: str
    code: str  # артикул в iiko, например 00005


@dataclass
class CashItem:
    """Позиция заказа с кассы."""
    position_id: str
    product_id: str
    amount: float
    printed: datetime | None  # когда отправили на кухню; None — ещё не отправили
    deleted: bool
    note: str  # тип удаления и комментарий официанта, если удалили


@dataclass
class CashOrder:
    id: str
    status: str  # New, Bill, Closed, Deleted
    items: list[CashItem]


def _iiko_time(value: str | None) -> datetime | None:
    """«2026-10-18 09:15:35.864» → datetime. Время кассы, как на компьютере с iikoFront."""
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S") if value else None


def _cash_order(raw: dict) -> CashOrder | None:
    order = raw.get("order")
    if not order:  # заказ ещё создаётся или с ошибкой
        return None
    items = []
    for i in order.get("items") or []:
        product = i.get("product") or {}
        if i.get("type") != "Product" or not i.get("positionId") or not product.get("id"):
            continue  # услуги, комбо и прочее бот не считает
        deleted = i.get("deleted")
        note = ""
        if deleted:
            method = deleted.get("deletionMethod") or {}
            removal = (method.get("removalType") or {}).get("name") or ""
            comment = " ".join((method.get("comment") or "").split()).strip(" .")  # без переносов строк
            note = ", ".join(x for x in (removal, comment) if x)
        items.append(CashItem(
            i["positionId"], product["id"], float(i.get("amount") or 0),
            _iiko_time(i.get("whenPrinted")), bool(deleted), note,
        ))
    return CashOrder(raw["id"], order.get("status") or "", items)


def same_name(a: str, b: str) -> bool:
    """«Лосось на гриле» = «лосось  на грИле»: без регистра, лишних пробелов и разницы ё/е."""
    def norm(s: str) -> str:
        return " ".join(s.lower().replace("ё", "е").split())
    return norm(a) == norm(b)


def find_dish(dishes: list["IikoDish"], name: str) -> "IikoDish | None":
    """Блюдо iiko с таким же названием. Если совпадений нет или их несколько, None."""
    found = [d for d in dishes if same_name(d.name, name)]
    return found[0] if len(found) == 1 else None


class IikoClient:
    def __init__(
        self, api_key: str, app_id: str, client_secret: str,
        organization_id: str | None = None, terminal_group_id: str | None = None,
    ):
        self._login = {"apiKey": api_key, "appId": app_id, "clientSecret": client_secret}
        self.organization_id = organization_id
        self.terminal_group_id = terminal_group_id
        self._tables: list[str] | None = None
        self._token: str | None = None
        self._token_at = 0.0

    async def _post(self, path: str, body: dict, auth: bool = True) -> dict:
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {await self._get_token()}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.post(BASE_URL + path, json=body, headers=headers) as r:
                    data = await r.json(content_type=None) or {}
                    if r.status != 200:
                        raise IikoError(
                            f"iiko ответила {r.status}: {data.get('errorDescription') or data}"
                        )
                    return data
        except aiohttp.ClientError as e:
            raise IikoError(f"нет связи с iiko ({e})") from None
        except asyncio.TimeoutError:
            raise IikoError("iiko не ответила за 30 секунд") from None

    async def _get_token(self) -> str:
        if self._token is None or time.monotonic() - self._token_at > TOKEN_LIFETIME:
            # v2: новый ключ Cloud API входит только так (v1 его не принимает)
            data = await self._post("/api/v2/access_token", self._login, auth=False)
            self._token, self._token_at = data["token"], time.monotonic()
        return self._token

    async def _ids(self) -> tuple[str, str]:
        """Организация и группа касс. Если в .env не указаны, берём первые доступные."""
        if self.organization_id is None:
            data = await self._post("/api/1/organizations", {})
            orgs = data.get("organizations") or []
            if not orgs:
                raise IikoError("ключ не видит ни одной организации")
            self.organization_id = orgs[0]["id"]
        if self.terminal_group_id is None:
            data = await self._post(
                "/api/1/terminal_groups", {"organizationIds": [self.organization_id]}
            )
            groups = [g for org in data.get("terminalGroups", []) for g in org.get("items", [])]
            if not groups:
                raise IikoError("нет группы касс, к которой у ключа есть доступ")
            self.terminal_group_id = groups[0]["id"]
        return self.organization_id, self.terminal_group_id

    async def dishes(self) -> list[IikoDish]:
        """Блюда из меню iiko. Старый /api/1/nomenclature на стенде отдавал пустой список,
        поэтому берём новый метод номенклатуры."""
        items, offset = [], 0
        while True:
            data = await self._post("/api/nomenclature/v2/product/list", {
                "filters": [
                    {"field": "type", "op": "eq", "value": "DISH"},
                    {"field": "isDeleted", "op": "eq", "value": False},
                ],
                "limit": 1000,
                "offset": offset,
            })
            page = data.get("items") or []
            items += [IikoDish(p["productId"], p["name"], p.get("productArticle") or "") for p in page]
            if len(page) < 1000:
                return items
            offset += 1000

    async def stop(self, product_id: str) -> None:
        """Поставить блюдо в стоп (остаток 0) и дождаться, что касса выполнила команду."""
        await self._stop_command("/api/1/stop_lists/add", {"productId": product_id, "balance": 0})

    async def unstop(self, product_id: str) -> None:
        """Снять блюдо со стопа."""
        await self._stop_command("/api/1/stop_lists/remove", {"productId": product_id})

    async def _stop_command(self, path: str, item: dict) -> None:
        org, group = await self._ids()
        data = await self._post(path, {
            "organizationId": org, "terminalGroupId": group, "items": [item],
        })
        # Команда идёт на кассу. Если касса выключена, она так и останется «в процессе».
        for _ in range(STATUS_TRIES):
            await asyncio.sleep(STATUS_PAUSE)
            status = await self._post("/api/1/commands/status", {
                "organizationId": org, "correlationId": data["correlationId"],
            })
            state = status.get("state")
            if state == "Success":
                return
            if state == "Error":
                raise IikoError(f"касса не выполнила команду: {status.get('exception') or status}")
        logging.warning("iiko: команда %s без ответа кассы", data["correlationId"])
        raise IikoError("касса не ответила. Возможно, iikoFront выключен")

    async def _tables_ids(self, org: str, group: str) -> list[str]:
        if self._tables is None:
            data = await self._post("/api/1/reserve/available_restaurant_sections", {
                "terminalGroupIds": [group],
            })
            self._tables = [
                t["id"] for s in data.get("restaurantSections") or [] for t in s.get("tables") or []
            ]
            if not self._tables:
                raise IikoError("в iiko нет ни одного стола, заказы читать неоткуда")
        return self._tables

    async def orders(self) -> list[CashOrder]:
        """Заказы со всех столов за последние сутки: открытые, оплаченные и удалённые.

        На стенде облако видело заказ с кассы только после того, как его попросили
        прислать заказы по столам (init_by_table). Поэтому сначала просим, потом читаем.
        Бывает, что касса отвечает на просьбу ошибкой, а заказы всё равно приходят,
        поэтому ошибку только пишем в лог.
        """
        org, group = await self._ids()
        # Если iikoFront выключен, облако отдало бы старые заказы без ошибки. Проверяем сами.
        alive = await self._post("/api/1/terminal_groups/is_alive", {
            "organizationIds": [org], "terminalGroupIds": [group],
        })
        if not any(a.get("isAlive") for a in alive.get("isAliveStatus") or []):
            raise IikoError("касса iikoFront не на связи (выключена или нет интернета)")
        tables = await self._tables_ids(org, group)
        data = await self._post("/api/1/order/init_by_table", {
            "organizationId": org, "terminalGroupId": group, "tableIds": tables,
        })
        for _ in range(STATUS_TRIES):
            await asyncio.sleep(STATUS_PAUSE)
            status = await self._post("/api/1/commands/status", {
                "organizationId": org, "correlationId": data["correlationId"],
            })
            if status.get("state") != "InProgress":
                if status.get("state") == "Error":
                    logging.info("iiko: касса ответила на запрос заказов: %s", status.get("exception"))
                break
        since = datetime.now() - timedelta(days=1)
        data = await self._post("/api/1/order/by_table", {
            "organizationIds": [org], "tableIds": tables,
            "statuses": ["New", "Bill", "Closed", "Deleted"],
            "dateFrom": since.strftime("%Y-%m-%d %H:%M:%S.000"),
        })
        return [o for o in map(_cash_order, data.get("orders") or []) if o]
