import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from nonebot.log import logger

from ..paths import cache_dir

GAMEINFO_URL = (
    "https://jsonschema.qpic.cn/2487025e6794be31c0e2e2b2cb4f5b22/"
    "f7d304f4c282ecdbe2fd1119cc115739/gameInfo"
)
_CACHE_TTL = 24 * 3600

_WEAPON_CN = {
    "classic": "标配", "shorty": "短炮", "frenzy": "狂怒", "ghost": "鬼魅",
    "sheriff": "正义", "stinger": "蜂刺", "spectre": "骇灵", "bucky": "雄鹿",
    "judge": "判官", "bulldog": "獠犬", "guardian": "戍卫", "phantom": "幻影",
    "vandal": "狂徒", "marshal": "飞将", "operator": "冥驹", "ares": "战神",
    "odin": "奥丁", "outlaw": "追猎", "tacticalknife": "近战武器",
}

# CDN 失效时的兜底赛季（抓包日期 2026-08，2026赛季 第四幕）
FALLBACK_SEASON_ID = "4f0864e2-40af-28a4-de2c-0e9e64e75f23"
FALLBACK_SEASON_NAME = "2026赛季 第四幕"

_data: Dict[str, Any] = {}
_loaded_ts: float = 0.0


def _cache_file() -> Path:
    try:
        return cache_dir() / "wegame_gameinfo.json"
    except Exception:
        return Path.cwd() / "wegame_gameinfo.json"


async def load_gameinfo(force: bool = False) -> Dict[str, Any]:
    """加载 gameInfo（内存缓存 → 网络 → 磁盘缓存）。"""
    global _data, _loaded_ts
    if not force and _data and time.time() - _loaded_ts < _CACHE_TTL:
        return _data

    fp = _cache_file()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GAMEINFO_URL,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = json.loads((await resp.read()).decode("utf-8", "replace"))
                    if isinstance(data, dict) and data.get("seasons"):
                        _data, _loaded_ts = data, time.time()
                        try:
                            fp.parent.mkdir(parents=True, exist_ok=True)
                            fp.write_text(json.dumps(data, ensure_ascii=False),
                                          encoding="utf-8")
                        except OSError as e:
                            logger.warning(f"gameInfo 写缓存失败: {e}")
                        return _data
    except Exception as e:
        logger.warning(f"拉取 gameInfo 失败: {e}")

    if fp.exists():
        try:
            saved = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("seasons"):
                _data, _loaded_ts = saved, time.time()
                return _data
        except (OSError, ValueError) as e:
            logger.warning(f"gameInfo 读缓存失败: {e}")
    return _data or {}


def seasons(data: Dict[str, Any]) -> List[Tuple[str, str, bool]]:
    """[(sid, 名称, 是否当前季)]，按接口顺序（新→旧）。"""
    out: List[Tuple[str, str, bool]] = []
    for s in data.get("seasons") or []:
        sid = str(s.get("id") or "")
        if sid:
            out.append((sid, str(s.get("name") or sid), bool(s.get("current"))))
    return out


def current_season(data: Dict[str, Any]) -> Tuple[str, str]:
    for sid, name, is_cur in seasons(data):
        if is_cur:
            return sid, name
    all_seasons = seasons(data)
    if all_seasons:
        return all_seasons[0][0], all_seasons[0][1]
    return FALLBACK_SEASON_ID, FALLBACK_SEASON_NAME


def _norm_map_key(map_id: str) -> str:
    m = (map_id or "").strip().lower()
    if not m.startswith("/game/maps/"):
        return ""
    parts = m.split("/")
    return f"/game/maps/{parts[3]}/{parts[3]}" if len(parts) > 3 else ""


def agent_meta(data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """guid(小写) → {name, alias_cn, color, avatar_url, picture_url}。"""
    out: Dict[str, Dict[str, str]] = {}
    for a in data.get("agents") or []:
        guid = str(a.get("guid") or "").lower()
        if not guid:
            continue
        name = a.get("name") or {}
        alias = a.get("alias") or {}
        out[guid] = {
            "name": str(name.get("cn") or ""),
            "alias_cn": str(alias.get("cn") or ""),
            "color": str(a.get("color") or ""),
            "avatar_url": str(a.get("avatar_url") or ""),
            "picture_url": str(a.get("picture_url") or ""),
        }
    return out


def map_meta(data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """归一化 mapId → {name, preview_url}（覆盖天枢云阙/盐海矿镇等新图）。"""
    out: Dict[str, Dict[str, str]] = {}
    for m in data.get("maps") or []:
        key = _norm_map_key(str(m.get("mapId") or ""))
        if not key:
            continue
        name = m.get("name") or {}
        out[key] = {
            "name": str(name.get("cn") or ""),
            "preview_url": str(m.get("preview_url") or ""),
        }
    return out


def weapon_meta(data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """武器 GUID/英文名(小写) → {name, type, picture_url}。"""
    out: Dict[str, Dict[str, str]] = {}
    for weapon in data.get("weapons") or []:
        guid = str(weapon.get("guid") or "").lower()
        if not guid:
            continue
        name = weapon.get("name") or {}
        kind = weapon.get("type") or {}
        english = str(name.get("en") or "")
        chinese = str(name.get("cn") or "")
        if not chinese or "�" in chinese:
            chinese = _WEAPON_CN.get(english.lower(), english)
        info = {
            "name": chinese,
            "type": str(kind.get("cn") or kind.get("en") or ""),
            "picture_url": str(weapon.get("picture_url") or ""),
        }
        out[guid] = info
        english_key = english.lower()
        if english_key:
            out[english_key] = info
        if english_key == "tacticalknife":
            out["melee"] = info
    return out


def agent_lookup(meta: Dict[str, Dict[str, str]], agent_id: str) -> Dict[str, str]:
    """按国服 characterId 找元数据（全等优先，再 8 字符前缀）。"""
    aid = (agent_id or "").strip().lower()
    if not aid:
        return {}
    hit = meta.get(aid)
    if hit:
        return hit
    for guid, m in meta.items():
        if guid.startswith(aid[:8]):
            return m
    return {}


def lookup_by_name(
    meta: Dict[str, Dict[str, str]], name: str
) -> Dict[str, str]:
    """按中文名反查特工（mval 的 record/list 只给 hero_name，需要这个回查头像/色）。

    两段式：
    1) 优先按 CN 名精确匹配（区别大小写也容错）；
    2) 未命中时按 alias CN 匹配（别名以英文为主，但 include 了部分中文花名）。
    """
    if not name:
        return {}
    q = (name or "").strip()
    # 第一段：CN 名精确
    for m in meta.values():
        if m.get("name") == q:
            return m
    # 第二段：别名兜底（忽略大小写）；排除 mval 历史粗劣填充（"特工-x"、"agent-x"）
    if q.startswith(("特工-", "agent-")):
        tail = q.split("-", 1)[1]
        for m in meta.values():
            if m.get("name") == tail:
                return m
    q_l = q.lower()
    for m in meta.values():
        alias_cn = m.get("alias_cn") or ""
        if alias_cn and alias_cn.lower() == q_l:
            return m
    return {}


async def resolve_season(client=None) -> Tuple[str, str]:
    """解析当前赛季 (sid, 名称)：CDN → 最近一场对局详情 → 兜底常量。"""
    data = await load_gameinfo()
    sid, name = current_season(data)
    if sid:
        return sid, name
    if client is not None:
        try:
            me = await client.resolve_own_subject()
            rows = await client.get_recent_battles(me.subject, size=1)
            if rows:
                row = rows[0]
                event_id = (row.get("apEventId") or row.get("ap_event_id")
                            if isinstance(row, dict) else row.event_id)
                detail = await client.get_battle_detail(str(event_id or ""))
                view = (detail.get("battle_detail") or {}).get("playerGameView") or {}
                sid2 = str(view.get("seasonId") or "")
                if sid2:
                    return sid2, "当前赛季"
        except Exception as e:
            # 鉴权类错误由上层调用方统一处理，这里只兜底解析失败
            logger.warning(f"从对局详情解析赛季失败: {e}")
    return FALLBACK_SEASON_ID, FALLBACK_SEASON_NAME
