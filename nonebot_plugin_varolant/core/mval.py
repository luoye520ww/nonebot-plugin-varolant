import asyncio
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
        f"acctype=qc; "
        f"openid={openid or 'null'}; "
        f"access_token={access_token}; "
        f"userId={user_id}; "
        f"accountType=5; "
        f"tid={tid}"
    )
    return {**_HEADERS, "cookie": cookie}


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
        import json
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
