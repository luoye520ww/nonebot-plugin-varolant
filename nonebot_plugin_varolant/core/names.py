import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import aiohttp
from nonebot.log import logger

VAL_API_AGENTS = "https://valorant-api.com/v1/agents"
VAL_API_MAPS = "https://valorant-api.com/v1/maps"

_CACHE_TTL = 7 * 24 * 3600  # 一周

_CN_AGENTS = {
    "7c8a4701": "迷核",
    "92eeef5d": "禁灭",
    "df1cb487": "幻棱",
    "b444168c": "钛狐",
    "efba5359": "维斯",
    "1dbf2edd": "暮蝶",
    "0e38b510": "壹决",
    "5f8d3a7f": "铁臂",
    "add6443a": "捷风",
    "f94c3b30": "雷兹",
    "8e253930": "幽影",
    "9f0d8ba9": "炼狱",
    "eb93336a": "不死鸟",
    "569fdd95": "贤者",
    "320b2a48": "猎枭",
    "707eab51": "蝰蛇",
    "117ed9e3": "零",
    "a3bfb853": "芮娜",
    "1e58de9c": "奇乐",
    "22697a3d": "尚勃勒",
    "bb2a4828": "霓虹",
    "6f2a04ca": "斯凯",
    "7f94d92c": "夜露",
    "41fb69c1": "星礈",
    "601dbbe7": "K/O",
    "95b78ed7": "海神",
    "e370fa57": "盖可",
    "dade69b4": "黑梦",
    "cc8b64c8": "钢锁",
}

_FALLBACK_MAPS = {
    "/game/maps/ascent/ascent": "亚海悬城",
    "/game/maps/bind/bind": "源工重镇",
    "/game/maps/haven/haven": "隐世修所",
    "/game/maps/split/split": "霓虹町",
    "/game/maps/icebox/icebox": "森寒冬港",
    "/game/maps/breeze/breeze": "微风岛屿",
    "/game/maps/fracture/fracture": "裂变峡谷",
    "/game/maps/pearl/pearl": "深海明珠",
    "/game/maps/lotus/lotus": "莲华古城",
    "/game/maps/sunset/sunset": "日落之城",
    "/game/maps/abyss/abyss": "幽邃地窟",
    "/game/maps/rook/rook": "盐海矿镇",      # WeGame 代号 Rook（旧称侵蚀）
    "/game/maps/plummet/plummet": "天枢云阙",  # 2026 新图
    "/game/maps/infinity/infinity": "幽邃地窟",
    "/game/maps/juliett/juliett": "日落之城",
    "/game/maps/port/port": "森寒冬港",
    "/game/maps/foxtrot/foxtrot": "微风岛屿",
    "/game/maps/bonsai/bonsai": "霓虹町",
    "/game/maps/triad/triad": "隐世修所",
    "/game/maps/duality/duality": "源工重镇",
    "/game/maps/jam/jam": "莲华古城",
    "/game/maps/canyon/canyon": "裂变峡谷",
    "/game/maps/pitt/pitt": "深海明珠",
}

_TIER_NAMES = {
    0: "未定级", 1: "未定级", 2: "未定级",
    3: "黑铁 I", 4: "黑铁 II", 5: "黑铁 III",
    6: "青铜 I", 7: "青铜 II", 8: "青铜 III",
    9: "白银 I", 10: "白银 II", 11: "白银 III",
    12: "黄金 I", 13: "黄金 II", 14: "黄金 III",
    15: "白金 I", 16: "白金 II", 17: "白金 III",
    18: "钻石 I", 19: "钻石 II", 20: "钻石 III",
    21: "超凡 I", 22: "超凡 II", 23: "超凡 III",
    24: "神话 I", 25: "神话 II", 26: "神话 III",
    27: "源能战魂",
}

_agents: dict = {}
_maps: dict = {}
_loaded_ts: float = 0.0
_lock = asyncio.Lock()


def _cache_path() -> Path:
    try:
        from ..paths import cache_dir
        return cache_dir() / "val_name_maps.json"
    except Exception:
        return Path.cwd() / "val_name_maps.json"


def _normalize_map_key(map_id: str) -> str:
    m = (map_id or "").strip().lower()
    if not m.startswith("/game/maps/"):
        return ""
    parts = m.split("/")
    return f"/game/maps/{parts[3]}/{parts[3]}" if len(parts) > 3 else ""


def agent_name(agent_id: str) -> str:
    aid = (agent_id or "").strip().lower()
    if not aid:
        return "未知特工"
    for prefix, cn in _CN_AGENTS.items():
        if aid.startswith(prefix):
            return cn
    hit = _agents.get(aid)
    if hit:
        return hit
    return "特工-" + aid[:8]


def map_name(map_id: str) -> str:
    if not map_id:
        return "未知地图"
    hit = _maps.get(map_id.lower())
    if hit:
        return hit
    key = _normalize_map_key(map_id)
    if key and key in _FALLBACK_MAPS:
        return _FALLBACK_MAPS[key]
    short = map_id.rstrip("/").split("/")[-1] or map_id
    return short


def tier_name(tier: Optional[int]) -> str:
    if tier is None:
        return "未定级"
    return _TIER_NAMES.get(int(tier), f"段位{tier}")

# WeGame matchModeKey 通过 `GetMap`/对局数据得到，常见值如下；未收录时原样展示。
_MODE_NAMES = {
    "competitive": "竞技模式", "unrated": "一般模式", "spikerush": "爆能快攻",
    "deathmatch": "团队死斗", "ggteam": "武装升级", "onefa": "非排位",
    "gungame": "枪王之王", "hurm": "极速竞技", "swiftplay": "超速冲点",
    "premier": "冠军巡回赛", "custom": "自定义", "newmap": "一般模式",
}


def mode_name(mode_key: str) -> str:
    m = (mode_key or "").strip().lower()
    if not m:
        return "未知模式"
    if "#" in m:
        m = m.split("#")[-1]
    return _MODE_NAMES.get(m, m)


async def refresh_name_maps(force: bool = False) -> None:
    """从 valorant-api.com 拉全量特工/地图中文名，带磁盘缓存。"""
    global _agents, _maps, _loaded_ts
    if not force and _agents and time.time() - _loaded_ts < _CACHE_TTL:
        return
    async with _lock:
        if not force and _agents and time.time() - _loaded_ts < _CACHE_TTL:
            return
        cache_file = _cache_path()
        fresh_agents: dict = {}
        fresh_maps: dict = {}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(VAL_API_AGENTS, params={"language": "zh-CN"},
                                         timeout=aiohttp.ClientTimeout(total=20))
                if resp.status == 200:
                    body = await resp.json(content_type=None)
                    for a in body.get("data", []):
                        if a.get("isPlayableCharacter"):
                            fresh_agents[a["uuid"].lower()] = a.get("displayName") or a["uuid"][:8]
                resp2 = await session.get(VAL_API_MAPS, params={"language": "zh-CN"},
                                          timeout=aiohttp.ClientTimeout(total=20))
                if resp2.status == 200:
                    body2 = await resp2.json(content_type=None)
                    for m in body2.get("data", []):
                        url = (m.get("mapUrl") or m.get("assetPath") or "").lower()
                        if url:
                            fresh_maps[url] = m.get("displayName") or url.rsplit("/", 1)[-1]
        except Exception as e:
            logger.warning(f"拉取名映射失败: {e}")

        if fresh_agents or fresh_maps:
            _agents, _maps = fresh_agents, fresh_maps
            _loaded_ts = time.time()
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(
                    {"agents": _agents, "maps": _maps, "ts": _loaded_ts},
                    ensure_ascii=False), encoding="utf-8")
                logger.info(f"名映射刷新: 特工 {len(_agents)}，地图 {len(_maps)}")
            except OSError as e:
                logger.warning(f"写入名映射缓存失败: {e}")
            return

        # 网络失败 → 读磁盘缓存
        if cache_file.exists():
            try:
                saved = json.loads(cache_file.read_text(encoding="utf-8"))
                _agents, _maps = saved.get("agents", {}), saved.get("maps", {})
                _loaded_ts = saved.get("ts", 0.0)
                if _agents or _maps:
                    logger.info(f"名映射走磁盘缓存: 特工 {len(_agents)}，地图 {len(_maps)}")
                    return
            except (OSError, ValueError) as e:
                logger.warning(f"读取名映射缓存失败: {e}")

        # 最后兜底
        _agents.update({k: v for k, v in _CN_AGENTS.items() if k not in _agents})
        _maps.update(_FALLBACK_MAPS)
        _loaded_ts = time.time()
