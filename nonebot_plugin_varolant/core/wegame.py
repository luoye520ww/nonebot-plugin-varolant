import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

BASE_URL = "https://www.wegame.com.cn/api/v1/"
AUTH_REFRESH_URL = (
    "https://www.wegame.com.cn/api/middle/clientapi/auth/refresh_client_ticket"
)
FROM_SRC = "valorant_web"
AccountUpdate = Callable[[Dict[str, Any]], Awaitable[Optional[bool]]]
_REFRESH_LOCKS: Dict[str, asyncio.Lock] = {}
_LATEST_ACCOUNTS: Dict[str, Dict[str, Any]] = {}


class WegameError(RuntimeError):
    def __init__(self, message: str, auth_invalid: bool = False):
        super().__init__(message)
        self.auth_invalid = auth_invalid


@dataclass
class WegameRole:
    subject: str = ""
    name: str = ""
    level: int = 0
    card: str = ""


def _cookie_header(account: Dict[str, Any]) -> str:
    names = (
        "tgp_id", "tgp_ticket", "tgp_env", "tgp_user_type",
        "tgp_third_openid", "_qimei_uuid42", "_qimei_fingerprint",
        "_qimei_q36", "_qimei_h38",
    )
    parts = [f"{name}={account[name]}" for name in names if account.get(name)]
    if not any(p.startswith("tgp_ticket=") for p in parts):
        raise WegameError("账号缺少 tgp_ticket，请重新发送「瓦登录」")
    return "; ".join(parts)


def _headers(account: Dict[str, Any]) -> Dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://www.wegame.com.cn",
        "referer": "https://www.wegame.com.cn/helper/valorant/",
        "trpc-caller": "wegame.pallas.web.Valorant",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "cookie": _cookie_header(account),
    }


def next_refresh_at(now: int, span: Any, key: str = "") -> int:
    """在短票据有效期约 65% 处刷新，并加入稳定的小抖动。"""
    try:
        seconds = max(int(float(span)), 600)
    except (TypeError, ValueError):
        seconds = 1800
    jitter = (sum(key.encode("utf-8")) % 7 - 3) / 100
    delay = max(300, min(int(seconds * (0.65 + jitter)), int(seconds * 0.9)))
    return int(now) + delay


class WegameClient:
    def __init__(self, account: Dict[str, Any], timeout: float = 15.0,
                 on_account_update: Optional[AccountUpdate] = None,
                 account_key: str = ""):
        self.account = dict(account)
        self.timeout = timeout
        self.on_account_update = on_account_update
        self.account_key = account_key or str(account.get("tgp_id") or id(self))
        self._session: Optional[aiohttp.ClientSession] = None
        self._refresh_checked = False
        current = _LATEST_ACCOUNTS.get(self.account_key)
        if current is None or self._int(current.get("auth_refreshed_at")) <= self._int(
            self.account.get("auth_refreshed_at")
        ):
            _LATEST_ACCOUNTS[self.account_key] = dict(self.account)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=_headers(self.account))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _refresh_due(self) -> bool:
        if not self.account.get("ct"):
            return False
        scheduled = self._int(self.account.get("next_refresh_at"))
        if scheduled:
            return time.time() >= scheduled
        refreshed = self._int(self.account.get("auth_refreshed_at"))
        span = self._int(self.account.get("refresh_wt_span"), 1800)
        return not refreshed or time.time() >= next_refresh_at(
            refreshed, span, self.account_key
        )

    async def _persist(self, state: Dict[str, Any], expected_ct: str) -> bool:
        if not self.on_account_update:
            return True
        payload = dict(state)
        payload["_expected_ct"] = expected_ct
        result = await self.on_account_update(payload)
        return result is not False

    async def _record_auth_error(self, message: str, *, relogin: bool = False) -> None:
        state = dict(self.account)
        state.update({
            "last_auth_error": message,
            "last_auth_error_at": int(time.time()),
            "needs_relogin": relogin,
        })
        await self._persist(state, str(self.account.get("ct") or ""))

    async def _validate_candidate(self, candidate: Dict[str, Any]) -> bool:
        payload: Dict[str, Any] = {"from_src": FROM_SRC}
        subject = str(candidate.get("subject") or "")
        if subject:
            payload["subject"] = subject
        try:
            async with aiohttp.ClientSession(headers=_headers(candidate)) as session:
                async with session.post(
                    BASE_URL + "wegame.pallas.game.ValBattle/GetRoleInfo",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    raw = await response.read()
            if response.status != 200:
                return False
            body = json.loads(raw.decode("utf-8", "replace"))
            if not isinstance(body, dict) or not body:
                return False
            if not any(key in body for key in ("result", "error_code", "data")):
                return False
            result = body.get("result")
            if isinstance(result, dict):
                return int(result.get("error_code") or 0) == 0
            return int(result or body.get("error_code") or 0) == 0
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False

    async def refresh_ticket(self, force: bool = False) -> bool:
        if not self.account.get("ct"):
            return False
        lock = _REFRESH_LOCKS.setdefault(self.account_key, asyncio.Lock())
        initial_ct = str(self.account.get("ct") or "")
        async with lock:
            latest = _LATEST_ACCOUNTS.get(self.account_key)
            if latest and str(latest.get("ct") or "") != initial_ct:
                self.account = dict(latest)
                await self.close()
                return True
            if not force and not self._refresh_due():
                return True

            session = await self._get_session()
            payload = {
                "config_params": {"lang_type": 0},
                "ct": initial_ct,
                "local_is_new_user": 0,
                "user_id": str(self.account.get("tgp_id") or ""),
            }
            try:
                async with session.post(
                    AUTH_REFRESH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    raw = await response.read()
                    response_cookies = {
                        name: morsel.value for name, morsel in response.cookies.items()
                    }
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await self._record_auth_error("network_error")
                return False
            if response.status != 200:
                await self._record_auth_error(
                    f"http_{response.status}", relogin=response.status in {401, 403}
                )
                return False
            try:
                body = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                await self._record_auth_error("invalid_response")
                return False
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            invalid = (
                body.get("code") not in (None, 0, "0")
                or data.get("result") not in (None, 0, "0")
                or data.get("is_timeout") in (1, "1", True)
                or data.get("state") == "login_from_another_client"
            )
            if invalid:
                await self._record_auth_error("ticket_expired", relogin=True)
                return False

            info = data.get("ct_info") if isinstance(data.get("ct_info"), dict) else data
            ticket = response_cookies.get("tgp_ticket") or info.get("wt")
            new_ct = info.get("ct")
            if not ticket or not new_ct:
                await self._record_auth_error("incomplete_refresh")
                return False
            candidate = dict(self.account)
            candidate["tgp_ticket"] = str(ticket)
            candidate["ct"] = str(new_ct)
            if response_cookies.get("tgp_id"):
                candidate["tgp_id"] = response_cookies["tgp_id"]
            for key in ("refresh_wt_span", "refresh_ct_span", "expires", "server_time"):
                if info.get(key) not in (None, ""):
                    candidate[key] = info[key]
            now = int(time.time())
            candidate.update({
                "auth_refreshed_at": now,
                "next_refresh_at": next_refresh_at(
                    now, candidate.get("refresh_wt_span"), self.account_key
                ),
                "needs_relogin": False,
                "last_auth_error": "",
                "last_auth_error_at": 0,
                "last_verified_at": now,
            })
            if not await self._validate_candidate(candidate):
                await self._record_auth_error("readback_failed")
                return False
            if not await self._persist(candidate, initial_ct):
                latest = _LATEST_ACCOUNTS.get(self.account_key)
                if latest:
                    self.account = dict(latest)
                return False
            self.account = candidate
            _LATEST_ACCOUNTS[self.account_key] = dict(candidate)
            await self.close()
            return True

    async def _post(self, endpoint: str, body: Optional[Dict[str, Any]] = None,
                    retry_auth: bool = True) -> Dict[str, Any]:
        payload = dict(body or {})
        payload.setdefault("from_src", FROM_SRC)
        if not self._refresh_checked:
            self._refresh_checked = True
            await self.refresh_ticket()
        session = await self._get_session()
        try:
            async with session.post(
                BASE_URL + endpoint,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                raw = await response.read()
        except asyncio.TimeoutError as e:
            raise WegameError(f"WeGame 接口超时：{endpoint.rsplit('/', 1)[-1]}") from e
        except aiohttp.ClientError as e:
            raise WegameError(f"WeGame 网络错误：{e}") from e
        if response.status in {401, 403} and retry_auth:
            if await self.refresh_ticket(force=True):
                return await self._post(endpoint, body, retry_auth=False)
        if response.status in {401, 403}:
            await self._record_auth_error("http_auth_invalid", relogin=True)
            raise WegameError("WeGame 登录态已失效", auth_invalid=True)
        if response.status != 200:
            raise WegameError(f"WeGame HTTP {response.status}")
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as e:
            raise WegameError("WeGame 响应解析失败") from e
        result = data.get("result")
        if isinstance(result, dict):
            code = int(result.get("error_code") or 0)
            message = str(result.get("error_message") or "")
        else:
            code = int(result or data.get("error_code") or 0)
            message = str(data.get("error_message") or data.get("msg") or "")
        if code:
            lower = message.lower()
            invalid = code in {401, 403, 1001, 1003, 10001, 10002} or any(
                word in lower for word in ("login", "ticket", "auth", "登录")
            )
            if invalid and retry_auth and await self.refresh_ticket(force=True):
                return await self._post(endpoint, body, retry_auth=False)
            if invalid:
                await self._record_auth_error("api_auth_invalid", relogin=True)
            raise WegameError(message or f"WeGame 错误码 {code}", auth_invalid=invalid)
        nested = data.get("data")
        return nested if isinstance(nested, dict) else data

    async def get_role_info(self, subject: str = "") -> WegameRole:
        body: Dict[str, Any] = {}
        if subject:
            body["subject"] = subject
        data = await self._post("wegame.pallas.game.ValBattle/GetRoleInfo", body)
        role = data.get("role_info") or {}
        resolved = WegameRole(
            subject=str(role.get("subject") or subject),
            name=str(role.get("name") or ""),
            level=int(role.get("level") or 0),
            card=str(role.get("card") or ""),
        )
        if not resolved.subject:
            raise WegameError("WeGame 未返回角色 Subject")
        return resolved

    async def resolve_own_subject(self) -> WegameRole:
        cached = str(self.account.get("subject") or "")
        role = await self.get_role_info(cached)
        return role

    async def get_battle_list(self, subject: str = "", size: int = 10,
                              queue_id: str = "") -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {"size": max(1, min(int(size), 50))}
        if subject:
            body["subject"] = subject
        if queue_id:
            body["queueID"] = queue_id
        data = await self._post("wegame.pallas.game.ValBattle/GetBattleList", body)
        rows = data.get("battles") or data.get("data") or []
        return rows if isinstance(rows, list) else []

    async def get_recent_battles(self, subject: str = "", size: int = 10) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {}
        if subject:
            body["subject"] = subject
        data = await self._post("wegame.pallas.game.ValBattle/GetRecentBattles", body)
        rows = data.get("battles") or []
        return rows[:size] if isinstance(rows, list) else []

    async def get_battle_detail(self, event_id: str, subject: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {"apEventId": event_id}
        if subject:
            body["subject"] = subject
        return await self._post("wegame.pallas.game.ValBattle/GetBattleDetail", body)

    async def get_round_list(self, event_id: str) -> List[Dict[str, Any]]:
        data = await self._post("wegame.pallas.game.ValBattle/GetRoundList", {
            "apEventId": event_id,
            "from_src": "valorant_web",
        })
        rows = data.get("rounds") or []
        return rows if isinstance(rows, list) else []

    async def get_battle_report(self, sid: str, subject: str = "",
                                queue_id: str = "255") -> Dict[str, Any]:
        body: Dict[str, Any] = {"sid": sid, "queueID": queue_id}
        if subject:
            body["subject"] = subject
        return await self._post("wegame.pallas.game.ValBattle/GetBattleReport", body)

    async def get_radar(self, sid: str, subject: str,
                        queue_id: str = "255") -> Dict[str, Any]:
        return await self._post("wegame.pallas.game.ValBattle/GetRadarInfo", {
            "target_subject": subject, "sid": sid, "queue_id": queue_id,
        })

    async def get_maps(self, sid: str, subject: str = "") -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {"sid": sid}
        if subject:
            body["subject"] = subject
        data = await self._post("wegame.pallas.game.ValBattle/GetMap", body)
        rows = data.get("maps") or []
        return rows if isinstance(rows, list) else []

    async def get_champions(self, subject: str = "") -> List[Dict[str, Any]]:
        body = {"subject": subject} if subject else {}
        data = await self._post("wegame.pallas.game.ValBattle/GetChampion", body)
        rows = data.get("characterStats") or []
        return rows if isinstance(rows, list) else []

    async def get_friends(self, sid: str, subject: str) -> List[Dict[str, Any]]:
        data = await self._post("wegame.web.val.ValCareerData/GetWithFriendsBattleStats", {
            "target_subject": subject, "sid": sid,
        })
        rows = data.get("friends_battle_stats") or []
        return rows if isinstance(rows, list) else []

    async def get_weapons(self) -> List[Dict[str, Any]]:
        data = await self._post("wegame.pallas.game.ValBattle/GetWeapon")
        rows = data.get("weapons") or []
        return rows if isinstance(rows, list) else []

    async def get_my_room(self) -> Dict[str, Any]:
        data = await self._post("wegame.pallas.val.ValTeamUp/GetMyRoom")
        room = data.get("my_room") or {}
        return room if isinstance(room, dict) else {}

    async def get_user_game_info(self) -> Dict[str, Any]:
        return await self._post("wegame.pallas.val.ValTeamUp/GetUserGameInfo")

    async def get_match_info(self) -> Dict[str, Any]:
        return await self._post("wegame.pallas.game.ValAssist/GetMatchInfo")

    async def find_live_event_id(self) -> str:
        """尽力从房间/助手接口找当前对局事件 ID；字段变化时递归兼容。"""
        def find(obj: Any) -> str:
            if isinstance(obj, dict):
                for key in ("apEventId", "ap_event_id", "event_id", "eventId"):
                    value = obj.get(key)
                    if value:
                        return str(value)
                for value in obj.values():
                    found = find(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for value in obj:
                    found = find(value)
                    if found:
                        return found
            return ""

        for getter in (self.get_match_info, self.get_user_game_info, self.get_my_room):
            try:
                event_id = find(await getter())
                if event_id:
                    return event_id
            except WegameError as e:
                if e.auth_invalid:
                    raise
        rows = await self.get_battle_list(size=5)
        for row in rows:
            battle = row.get("battle") if isinstance(row.get("battle"), dict) else row
            completed = battle.get("isCompleted", row.get("isCompleted", 1))
            if str(completed).strip().lower() in {"0", "false", "no", ""}:
                return str(row.get("ap_event_id") or row.get("apEventId")
                           or battle.get("apEventId") or "")
        return ""


def get_client(account: Dict[str, Any],
               on_account_update: Optional[AccountUpdate] = None,
               account_key: str = "") -> WegameClient:
    return WegameClient(
        account, on_account_update=on_account_update, account_key=account_key
    )
