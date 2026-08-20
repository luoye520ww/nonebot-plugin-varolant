import asyncio
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nonebot.log import logger

from ..paths import data_dir

_LOCK = asyncio.Lock()

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_user_id(user_id: str) -> str:
    uid = re.sub(r"[^0-9A-Za-z_-]", "", str(user_id or ""))
    if not uid:
        raise ValueError("user_id 为空或非法")
    return uid


def _file(user_id: str) -> Path:
    return data_dir() / f"{_safe_user_id(user_id)}.json"


def _skeleton(user_id: str) -> Dict[str, Any]:
    return {
        "qq": str(user_id),
        "accounts": [],
        "wegame": {},
        "active": 0,
        "auto_check": 0,
        "watchlist": [],
    }


def _load(user_id: str) -> Dict[str, Any]:
    path = _file(user_id)
    if not path.exists():
        return _skeleton(user_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a dict")
    except Exception as e:
        logger.error(f"读取用户数据失败 {path.name}: {e}，已按空数据处理")
        return _skeleton(user_id)
    base = _skeleton(user_id)
    base.update({k: v for k, v in data.items() if k in base})
    if not isinstance(base["accounts"], list):
        base["accounts"] = []
    if not isinstance(base["watchlist"], list):
        base["watchlist"] = []
    return base


def _save(user_id: str, data: Dict[str, Any]) -> None:
    path = _file(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def _active_account(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    accounts: List[Dict[str, Any]] = data.get("accounts") or []
    if not accounts:
        return None
    idx = int(data.get("active") or 0)
    if not 0 <= idx < len(accounts):
        idx = 0
        data["active"] = 0
    return accounts[idx]


def account_label(acc: Dict[str, Any], index: int) -> str:
    """账号展示名：昵称 > userId 尾巴 > 序号。"""
    name = (acc.get("nickname") or "").strip()
    if name:
        return name
    uid = (acc.get("userId") or "").strip()
    if uid:
        return f"ID:{uid[-6:]}"
    return f"账号{index + 1}"

async def init_db() -> None:
    """初始化数据目录并把旧版 SQLite 数据迁移为 per-QQ JSON（幂等）。"""
    data_dir()
    await asyncio.to_thread(_migrate_legacy_sqlite)
    logger.debug("varolant 数据目录初始化完成")


async def get_user_config(user_id: str) -> Optional[Dict[str, Any]]:
    """读取当前账号凭证（附带 auto_check / 账号序号），未绑定返回 None。"""
    async with _LOCK:
        data = _load(user_id)
    acc = _active_account(data)
    if acc is None:
        return None
    accounts: List[Dict[str, Any]] = data["accounts"]
    return {
        "userId": acc.get("userId") or "",
        "tid": acc.get("tid") or "",
        "openid": acc.get("openid") or "",
        "uin": int(acc.get("uin") or 0),
        "access_token": acc.get("access_token") or "",
        "nickname": acc.get("nickname") or "",
        "auto_check": int(data.get("auto_check") or 0),
        "account_index": accounts.index(acc),
        "accounts_count": len(accounts),
    }


async def get_wegame_config(user_id: str) -> Optional[Dict[str, Any]]:
    """读取 WeGame 战绩登录态；未绑定返回 None。"""
    async with _LOCK:
        cfg = dict(_load(user_id).get("wegame") or {})
    if not cfg.get("tgp_ticket"):
        return None
    return cfg


async def save_wegame_config(user_id: str, config: Dict[str, Any]) -> None:
    """保存 WeGame 战绩登录态，不影响掌瓦商店账号。"""
    allowed = {
        "tgp_id", "tgp_ticket", "tgp_env", "tgp_user_type",
        "tgp_third_openid", "_qimei_uuid42", "_qimei_fingerprint",
        "_qimei_q36", "_qimei_h38", "subject", "nickname", "ct",
        "refresh_wt_span", "refresh_ct_span", "auth_refreshed_at",
    }
    async with _LOCK:
        data = _load(user_id)
        old = dict(data.get("wegame") or {})
        for key in allowed:
            value = config.get(key)
            if value not in (None, ""):
                old[key] = str(value)
        old["updated_at"] = _now()
        data["wegame"] = old
        _save(user_id, data)


async def list_wegame_configs() -> List[Tuple[str, Dict[str, Any]]]:
    """读取需要自动续期的 WeGame 登录态。"""
    async with _LOCK:
        result = []
        for path in data_dir().glob("*.json"):
            if path.name == "switch.json":
                continue
            user_id = path.stem
            cfg = dict(_load(user_id).get("wegame") or {})
            if cfg.get("tgp_ticket") and cfg.get("ct"):
                result.append((user_id, cfg))
        return result


async def clear_wegame_config(user_id: str) -> bool:
    """仅清除 WeGame 战绩登录态。"""
    async with _LOCK:
        data = _load(user_id)
        if not data.get("wegame"):
            return False
        data["wegame"] = {}
        _save(user_id, data)
        return True


async def save_user_config(
    user_id: str,
    userId: str,
    tid: str,
    nickname: Optional[str] = None,
    *,
    openid: str = "",
    uin: int = 0,
    access_token: str = "",
) -> int:
    """保存/更新一个账号，并设为当前账号。返回账号总数。

    - 同一 userId 重复绑定：保留 openid/uin/access_token 字段并按入参覆盖
    - 新 userId：追加为新账号
    """
    async with _LOCK:
        data = _load(user_id)
        accounts: List[Dict[str, Any]] = data["accounts"]
        target: Optional[Dict[str, Any]] = None
        for acc in accounts:
            if acc.get("userId") and acc["userId"] == userId:
                target = acc
                break

        if target is None:
            target = {
                "userId": userId,
                "tid": tid,
                "openid": openid,
                "uin": int(uin or 0),
                "access_token": access_token,
                "nickname": nickname or "",
                "created_at": _now(),
                "updated_at": _now(),
            }
            accounts.append(target)
        else:
            target["tid"] = tid
            if openid:
                target["openid"] = openid
            if uin:
                target["uin"] = int(uin)
            if access_token:
                target["access_token"] = access_token
            if nickname:
                target["nickname"] = nickname
            target["updated_at"] = _now()

        data["active"] = accounts.index(target)
        _save(user_id, data)
        return len(accounts)


async def clear_user_config(user_id: str) -> bool:
    """清除该 QQ 的全部数据（所有账号 + 监控列表），返回是否真的删掉了。"""
    async with _LOCK:
        path = _file(user_id)
        if not path.exists():
            return False
        path.unlink()
        return True


async def clear_app_configs(user_id: str) -> bool:
    """仅清除掌瓦 App 商店账号，保留 WeGame 与监控设置。"""
    async with _LOCK:
        data = _load(user_id)
        if not data.get("accounts"):
            return False
        data["accounts"] = []
        data["active"] = 0
        _save(user_id, data)
        return True


async def list_auto_check_users() -> List[str]:
    """所有开启了每日自动监控的 QQ 号。"""
    users: List[str] = []
    for path in sorted(data_dir().glob("*.json")):
        if path.name == "switch.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("auto_check") or 0) == 1:
                users.append(str(data.get("qq") or path.stem))
        except Exception:
            continue
    return users


async def update_auto_check(user_id: str, status: int) -> None:
    """更新每日自动监控开关（用户级，不要求已绑定账号）。"""
    async with _LOCK:
        data = _load(user_id)
        data["auto_check"] = 1 if status else 0
        _save(user_id, data)
    logger.info(f"用户 {user_id} 自动查询状态更新为: {status}")


async def get_auto_check(user_id: str) -> int:
    """读取用户级自动监控开关；即使账号已删空也能正确返回。"""
    async with _LOCK:
        data = _load(user_id)
    return 1 if int(data.get("auto_check") or 0) else 0


async def add_watch_item(user_id: str, item_name: str) -> bool:
    """添加监控项；已存在返回 False。"""
    async with _LOCK:
        data = _load(user_id)
        watchlist: List[Dict[str, Any]] = data["watchlist"]
        if any(w.get("item_name") == item_name for w in watchlist):
            return False
        watchlist.append({"item_name": item_name, "created_at": _now()})
        _save(user_id, data)
        return True


async def remove_watch_item(user_id: str, item_name: str) -> bool:
    """删除监控项；不存在返回 False。"""
    async with _LOCK:
        data = _load(user_id)
        watchlist: List[Dict[str, Any]] = data["watchlist"]
        rest = [w for w in watchlist if w.get("item_name") != item_name]
        if len(rest) == len(watchlist):
            return False
        data["watchlist"] = rest
        _save(user_id, data)
        return True


async def get_watchlist(user_id: str) -> List[Dict[str, Any]]:
    """获取该 QQ 全部监控项。"""
    async with _LOCK:
        data = _load(user_id)
    return list(data.get("watchlist") or [])

async def list_accounts(user_id: str) -> List[Dict[str, Any]]:
    """账号列表（每项附带 index / is_active / has_shop / has_stats）。"""
    async with _LOCK:
        data = _load(user_id)
        accounts: List[Dict[str, Any]] = data["accounts"]
        active = int(data.get("active") or 0)
        result = []
        for i, acc in enumerate(accounts):
            result.append({
                "index": i,
                "label": account_label(acc, i),
                "userId": acc.get("userId") or "",
                "nickname": acc.get("nickname") or "",
                "is_active": i == active,
                "has_shop": bool(acc.get("userId") and acc.get("tid")),
                "has_stats": False,
            })
        return result


def _find_account(
    accounts: List[Dict[str, Any]], key: str
) -> Optional[int]:
    """按 1 起始序号 / 昵称子串 / userId 子串 找账号，返回下标。"""
    key = (key or "").strip()
    if not key:
        return None
    if key.isdigit():
        idx = int(key) - 1
        return idx if 0 <= idx < len(accounts) else None
    key_l = key.lower()
    for i, acc in enumerate(accounts):
        if key_l in (acc.get("nickname") or "").lower():
            return i
    for i, acc in enumerate(accounts):
        if key_l in (acc.get("userId") or "").lower():
            return i
    return None


async def set_active_account(user_id: str, key: str) -> Tuple[bool, str]:
    """切换当前账号。返回 (是否成功, 账号名或错误原因)。"""
    async with _LOCK:
        data = _load(user_id)
        accounts: List[Dict[str, Any]] = data["accounts"]
        if not accounts:
            return False, "你还没有绑定任何账号，请先 瓦登录"
        idx = _find_account(accounts, key)
        if idx is None:
            return False, f"找不到账号「{key}」，发送「瓦 账号」查看列表"
        data["active"] = idx
        _save(user_id, data)
        return True, account_label(accounts[idx], idx)


async def delete_account(user_id: str, key: str) -> Tuple[bool, str, int]:
    """删除指定账号。返回 (是否成功, 账号名或错误原因, 剩余账号数)。"""
    async with _LOCK:
        data = _load(user_id)
        accounts: List[Dict[str, Any]] = data["accounts"]
        if not accounts:
            return False, "你还没有绑定任何账号", 0
        idx = _find_account(accounts, key)
        if idx is None:
            return False, f"找不到账号「{key}」，发送「瓦 账号」查看列表", len(accounts)
        removed = accounts.pop(idx)
        active = int(data.get("active") or 0)
        if not accounts:
            data["active"] = 0
        elif idx < active:
            data["active"] = active - 1
        elif idx == active:
            data["active"] = min(active, len(accounts) - 1)
        # 账号删空也保留文件：监控列表 / 自动开关仍是有效用户数据，
        # 重新绑定账号后不应丢失
        _save(user_id, data)
        return True, account_label(removed, idx), len(accounts)

def _legacy_db_candidates() -> List[Path]:
    """旧版 varolant.db 可能出现的位置。"""
    candidates: List[Path] = [data_dir() / "varolant.db"]
    try:
        from nonebot_plugin_localstore import get_data_dir

        candidates.append(get_data_dir("nonebot_plugin_varolant") / "varolant.db")
    except Exception:
        pass
    return candidates


def _migrate_legacy_sqlite() -> None:
    for db_path in _legacy_db_candidates():
        if not db_path.exists():
            continue
        migrated = 0
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                user_cols = {
                    r[1] for r in conn.execute("PRAGMA table_info(valo_users)")
                }
            except sqlite3.Error:
                conn.close()
                continue
            if user_cols:
                sql = (
                    "SELECT user_id, userId, tid, nickname, auto_check"
                    " FROM valo_users"
                )
                for row in conn.execute(sql):
                    uid = str(row["user_id"])
                    if _file(uid).exists():
                        continue  # 已有新格式数据，不覆盖
                    data = _skeleton(uid)
                    data["accounts"] = [{
                        "userId": row["userId"] or "",
                        "tid": row["tid"] or "",
                        "openid": "",
                        "uin": 0,
                        "access_token": "",
                        "nickname": row["nickname"] or "",
                        "created_at": "",
                        "updated_at": "",
                    }]
                    data["active"] = 0
                    data["auto_check"] = int(row["auto_check"] or 0)
                    try:
                        for w in conn.execute(
                            "SELECT item_name, created_at FROM valo_watchlist WHERE user_id = ?",
                            (uid,),
                        ):
                            data["watchlist"].append({
                                "item_name": w["item_name"],
                                "created_at": w["created_at"] or "",
                            })
                    except sqlite3.Error:
                        pass
                    _save(uid, data)
                    migrated += 1
            conn.close()
        except Exception as e:
            logger.error(f"迁移旧版数据库 {db_path} 失败: {e}")
            continue
        backup = db_path.with_suffix(".db.migrated")
        try:
            db_path.replace(backup)
            logger.info(
                f"旧版数据库已迁移为 per-QQ JSON（{migrated} 个用户），"
                f"原库备份为 {backup.name}"
            )
        except OSError as e:
            logger.warning(f"旧版数据库备份失败: {e}")

    # 旧位置的群开关文件一并搬过来
    try:
        from nonebot_plugin_localstore import get_data_dir

        old_switch = get_data_dir("nonebot_plugin_varolant") / "switch.json"
        new_switch = data_dir() / "switch.json"
        if old_switch.exists() and not new_switch.exists():
            new_switch.write_bytes(old_switch.read_bytes())
            old_switch.unlink()
            logger.info("群开关配置已迁移到插件 data 目录")
    except Exception:
        pass
