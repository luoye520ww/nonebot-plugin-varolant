import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp
from nonebot.log import logger

BASE_URL = "https://app.mval.qq.com"

_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": (
        "okhttp/4.10.0; okhttp3; okhttp4"
        " com.tencent.apps.valorant/2.7.1"
    ),
    "x-client-version": "2.7.1.10064",
}

AUTH_INVALID_CODES = {1001, 1003, 999999}
_auth_locks: Dict[str, asyncio.Lock] = {}


class MvalError(RuntimeError):
    """mval 接口错误。auth_invalid=True 表示凭证已失效。"""

    def __init__(self, message: str, auth_invalid: bool = False):
        super().__init__(message)
        self.auth_invalid = auth_invalid


@dataclass
class RoleBrief:
    """一个 mval 主角色（get_main_role_raw 返回）。"""

    role_id: str = ""
    role_name: str = ""
    tier_text: str = ""
    competitive_tier: int = 0
    scene: str = ""


@dataclass
class PlayerBrief:
    """一个玩家的身份摘要（mval 版，与 WeGame 版字段兼容）。"""

    role_id: str = ""
    name: str = ""
    tag: str = ""
    title: str = ""

    @property
    def display(self) -> str:
        if self.tag and "#" not in self.name:
            return f"{self.name}#{self.tag}"
        return self.name or "未知玩家"


@dataclass
class MatchRow:
    """一场对局的精简信息（用于战绩卡片）。"""

    event_id: str = ""
    won: Optional[bool] = None
    score1: int = 0
    score2: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    acs: float = 0.0
    agent_id: str = ""
    agent_name: str = ""  # 已有中文名时直接使用，跳过 names.agent_name
    map_id: str = ""
    map_name: str = ""
    mode_key: str = ""
    rank_tier_after: Optional[int] = None
    rr_earned: Optional[int] = None
    is_match_mvp: bool = False
    first_kills: int = 0
    start_time_ms: int = 0
    role_id: str = ""      # mval 对局的 role_id（查他人战绩用）
    scene: str = ""        # mval scene token（查他人战绩用）


def build_mval_headers(account: Dict[str, Any]) -> Dict[str, str]:
    """构造 mval 战绩接口的请求头。account 至少需要 userId/tid。"""
    user_id = str(account.get("userId") or "").strip()
    tid = str(account.get("tid") or "").strip()
    if not user_id or not tid:
        raise MvalError("账号缺少 userId/tid，无法调用战绩接口")
    openid = str(account.get("openid") or "").strip()
    uin = account.get("uin") or 0
    access_token = str(account.get("access_token") or "").strip() or "null"
    cookie = (
        f"clientType=9; "
        f"uin=o{uin}; "
        f"appid=102061775; "
        f"acctype={account.get('acctype') or 'qc'}; "
        f"openid={openid or 'null'}; "
        f"access_token={access_token}; "
        f"userId={user_id}; "
        f"accountType=5; "
        f"tid={tid}"
    )
    for name in ("ctt", "sk"):
        if account.get(name):
            cookie += f"; {name}={account[name]}"
    return {**_HEADERS, "cookie": cookie}


async def _auth_post(
    session: aiohttp.ClientSession,
    path: str,
    account: Dict[str, Any],
    body: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, str], int]:
    try:
        async with session.post(
            BASE_URL + path + "?source_game_zone=agame&game_zone=agame",
            json=body,
            headers=build_mval_headers(account),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            raw = await response.read()
            cookies = {name: item.value for name, item in response.cookies.items()}
            status = response.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise MvalError("掌瓦认证网络错误") from error
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as error:
        raise MvalError("掌瓦认证响应解析失败") from error
    return payload if isinstance(payload, dict) else {}, cookies, status


def _auth_ok(payload: Dict[str, Any]) -> bool:
    return bool(payload) and any(key in payload for key in ("result", "ret")) and (
        payload.get("result") in (None, 0, "0")
        and payload.get("ret") in (None, 0, "0")
    )


def _auth_invalid(payload: Dict[str, Any], status: int) -> bool:
    if status in {401, 403}:
        return True
    try:
        code = int(payload.get("result") or payload.get("ret") or 0)
    except (TypeError, ValueError):
        code = 0
    message = str(
        payload.get("msg") or payload.get("errMsg")
        or payload.get("error_message") or ""
    ).lower()
    return code in AUTH_INVALID_CODES or any(
        word in message for word in ("ticket expire", "auth web ticket", "登录失效")
    )


async def refresh_app_account(
    user_id: str, account: Dict[str, Any], *, force: bool = False,
) -> bool:
    """刷新掌瓦商店认证；全链验活成功后再原子保存。"""
    from . import database

    lock = _auth_locks.setdefault(str(user_id), asyncio.Lock())
    requested_ct = str(account.get("ct") or "")
    async with lock:
        current = await database.get_app_config(
            user_id, str(account.get("userId") or "")
        )
        if not current or current.get("userId") != account.get("userId"):
            return False
        if str(current.get("ct") or "") != requested_ct:
            return True
        account = current
        now = int(time.time())
        if not force and int(float(account.get("next_refresh_at") or 0)) > now:
            return True
        old_ct = str(account.get("ct") or "")
        if not old_ct:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                refresh_body = {
                    "config_params": {"lang_type": 0},
                    "ct": old_ct,
                    "local_is_new_user": 0,
                    "user_id": str(account.get("userId") or ""),
                    "source_game_zone": "agame",
                    "game_zone": "agame",
                }
                payload, cookies, status = await _auth_post(
                    session, "/go/auth/refresh_client_ticket", account, refresh_body
                )
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                info = data.get("ct_info") if isinstance(data.get("ct_info"), dict) else data
                new_ct = str(info.get("ct") or "")
                new_tid = str(cookies.get("tid") or info.get("wt") or "")
                if status != 200 or not _auth_ok(payload) or not new_ct or not new_tid:
                    raise MvalError(
                        "掌瓦票据刷新失败",
                        auth_invalid=_auth_invalid(payload, status),
                    )

                candidate = dict(account)
                candidate.update({"ct": new_ct, "tid": new_tid})
                tmp_payload, tmp_cookies, tmp_status = await _auth_post(
                    session,
                    "/go/auth/get_client_tmp_ticket",
                    candidate,
                    {"config_params": {"lang_type": 0}, "ct": new_ct},
                )
                if tmp_status != 200 or not _auth_ok(tmp_payload):
                    raise MvalError(
                        "掌瓦临时票据刷新失败",
                        auth_invalid=_auth_invalid(tmp_payload, tmp_status),
                    )
                candidate.update({
                    key: value for key, value in tmp_cookies.items()
                    if key in {"ctt", "sk"} and value
                })
                token_payload, _, token_status = await _auth_post(
                    session,
                    "/go/auth/refresh_third_token",
                    candidate,
                    {
                        "type": str(account.get("acctype") or "qc"),
                        "uuid": str(account.get("userId") or ""),
                        "openid": str(account.get("openid") or ""),
                        "source_game_zone": "agame",
                        "game_zone": "agame",
                    },
                )
                token_data = token_payload.get("data") if isinstance(
                    token_payload.get("data"), dict
                ) else {}
                access_token = str(token_data.get("access_token") or "")
                if token_status != 200 or not _auth_ok(token_payload) or not access_token:
                    raise MvalError(
                        "掌瓦第三方令牌刷新失败",
                        auth_invalid=_auth_invalid(token_payload, token_status),
                    )
                candidate["access_token"] = access_token

                verify_payload, _, verify_status = await _auth_post(
                    session,
                    "/go/mlol_store/agame/user_store",
                    candidate,
                    {"_t": int(time.time())},
                )
                if verify_status != 200 or not _auth_ok(verify_payload):
                    raise MvalError(
                        "掌瓦商店验活失败",
                        auth_invalid=_auth_invalid(verify_payload, verify_status),
                    )
        except MvalError as error:
            await database.save_app_config(user_id, str(account.get("userId") or ""), {
                "last_auth_error": str(error),
                "last_auth_error_at": int(time.time()),
                "needs_relogin": error.auth_invalid,
            }, expected_ct=old_ct)
            return False

        span = int(float(info.get("refresh_wt_span") or account.get("refresh_wt_span") or 1800))
        candidate.update({
            "expires": info.get("expires") or account.get("expires") or 0,
            "refresh_wt_span": span,
            "refresh_ct_span": info.get("refresh_ct_span") or account.get("refresh_ct_span") or 0,
            "auth_refreshed_at": now,
            "next_refresh_at": now + max(300, int(span * 0.65)),
            "needs_relogin": False,
            "last_auth_error": "",
            "last_auth_error_at": 0,
            "last_verified_at": now,
        })
        return await database.save_app_config(
            user_id, str(account.get("userId") or ""), candidate,
            expected_ct=old_ct,
        )


class MvalClient:
    """mval 战绩接口客户端。"""

    def __init__(self, account: Dict[str, Any], timeout: float = 15.0):
        self._account = dict(account)
        self._timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=build_mval_headers(self._account))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def _err_code(payload: Dict[str, Any]) -> int:
        try:
            return int(payload.get("ret") or payload.get("result") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _err_msg(payload: Dict[str, Any]) -> str:
        return str(
            payload.get("msg")
            or payload.get("errMsg")
            or payload.get("error_message")
            or ""
        )

    async def _post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = await self._session_get()
        url = BASE_URL + path
        try:
            async with session.post(
                url, json=(body or {"_t": int(time.time())}),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                text = (await resp.read()).decode("utf-8", "replace")
        except asyncio.TimeoutError as e:
            raise MvalError(f"mval 接口超时: {path}") from e
        except aiohttp.ClientError as e:
            raise MvalError(f"mval 网络错误: {e}") from e
        if resp.status != 200:
            raise MvalError(f"mval HTTP {resp.status}: {path}")
        try:
            data = json.loads(text)
        except ValueError as e:
            raise MvalError(f"mval 响应解析失败: {path}") from e
        code = self._err_code(data)
        if code in AUTH_INVALID_CODES or self._err_msg(data).lower() in {
            "ticket expire", "auth web ticket fail", "ticket expired",
        }:
            raise MvalError(
                "掌瓦 App 登录态已失效，请重新发送「瓦app登录」",
                auth_invalid=True,
            )
        if code != 0:
            raise MvalError(f"{self._err_msg(data) or f'错误码 {code}'}（{path}）")
        return data

    async def get_main_role(self) -> RoleBrief:
        """拉取当前账号的主角色 + scene token。"""
        data = await self._post("/go/account/get_main_role_raw")
        payload = data.get("data") or data
        roles = payload.get("list") or payload.get("roles") or []
        if not roles:
            raise MvalError("当前账号尚未在掌上无畏契约完成角色绑定")
        r = roles[0]
        return RoleBrief(
            role_id=str(r.get("game_role_id") or r.get("role_id") or ""),
            role_name=str(r.get("role_name") or r.get("name") or ""),
            tier_text=str(r.get("tier_text") or r.get("tier_name") or ""),
            competitive_tier=int(r.get("competitive_tier") or 0),
            scene=str(r.get("scene") or r.get("role_scene") or ""),
        )

    async def get_val_card(self, role_id: str, scene: str) -> Dict[str, Any]:
        """赛季名片：KDA/胜率/ACS/精准击败/回合胜率/KAST/时长/段位。"""
        return await self._post(
            "/go/mine/card/val_card",
            {"game_role_id": role_id, "scene": scene, "_t": int(time.time())},
        )

    async def get_recent_battles(
        self, role_id: str, scene: str, page_size: int = 20, max_pages: int = 5,
    ) -> List[Dict[str, Any]]:
        """拉取近期对局列表（自动翻页）。"""
        out: List[Dict[str, Any]] = []
        baton: Optional[str] = None
        for _ in range(max_pages):
            body: Dict[str, Any] = {
                "game_role_id": role_id, "scene": scene,
                "page_size": page_size, "page_no": len(out) // page_size + 1,
                "_t": int(time.time()),
            }
            if baton:
                body["baton"] = baton
            data = await self._post(
                "/go/agame/career/record/list"
                "?source_game_zone=agame&game_zone=agame",
                body,
            )
            page = (
                data.get("battle_list")
                or data.get("data", {}).get("battle_list")
                or data.get("list")
                or []
            )
            if not isinstance(page, list):
                page = []
            out.extend(page)
            baton = (
                data.get("next_baton")
                or data.get("data", {}).get("next_baton")
                or ""
            )
            if not baton or len(page) < page_size:
                break
            await asyncio.sleep(0.05)
        return out

    async def get_scoreboard(
        self, match_id: str, *, battle_id: str = "", scene: str = "",
    ) -> Dict[str, Any]:
        """单场 10 人记分板。"""
        body: Dict[str, Any] = {"match_id": match_id, "_t": int(time.time())}
        if battle_id:
            body["battle_id"] = battle_id
        if scene:
            body["scene"] = scene
        return await self._post(
            "/go/agame/career/record/scoreboard"
            "?source_game_zone=agame&game_zone=agame",
            body,
        )

_client_cache: Dict[str, MvalClient] = {}


def get_client(account: Dict[str, Any]) -> MvalClient:
    """按账号身份缓存客户端；凭证刷新后立即替换旧会话。"""
    normalized = dict(account)
    key = f"{account.get('userId', '')}|{account.get('openid', '')}"
    cli = _client_cache.get(key)
    if cli is None or cli._account != normalized:
        if cli is not None:
            # get_client 只在异步 matcher 中调用；异步关闭旧连接，避免刷新 token
            # 后仍复用旧请求头，也避免阻塞当前命令。
            asyncio.get_running_loop().create_task(cli.close())
        cli = MvalClient(normalized)
        _client_cache[key] = cli
    return cli


async def close_all() -> None:
    for c in list(_client_cache.values()):
        await c.close()
    _client_cache.clear()
