"""Связь с iiko через Cloud API (раздел 8 logika-bota.md).

Бот здесь только ставит и снимает стоп и берёт список блюд, чтобы связать
свои блюда с меню iiko. Всё проверено на демо-стенде 18.10.2026
(заметки: bot-ostatkov/iiko-stend.md).

Ключи берутся из .env (IIKO_API_KEY, IIKO_APP_ID, IIKO_CLIENT_SECRET). Если их нет,
бот работает как раньше и просит су-шефа ставить стоп в iiko вручную.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

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
