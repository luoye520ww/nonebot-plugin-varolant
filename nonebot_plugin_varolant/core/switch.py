import json
from typing import List

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.log import logger
from nonebot.rule import Rule

from ..paths import data_dir

_SWITCH_FILE = "switch.json"


def _file():
    return data_dir() / _SWITCH_FILE


def _load() -> List[str]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning(f"读取群开关状态失败，按空白名单处理: {e}")
        return []
    wl = data.get("whitelist")
    if not isinstance(wl, list):
        return []
    return [str(g).strip() for g in wl if str(g).strip()]


def _save(whitelist: List[str]) -> None:
    path = _file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"whitelist": whitelist}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def enable_group(group_id) -> bool:
    """把群加入白名单。返回 True 表示本次新开启。"""
    gid = str(group_id).strip()
    wl = _load()
    if gid in wl:
        return False
    wl.append(gid)
    _save(wl)
    return True


def disable_group(group_id) -> bool:
    """把群移出白名单。返回 True 表示本次确实关闭了。"""
    gid = str(group_id).strip()
    wl = _load()
    if gid not in wl:
        return False
    wl.remove(gid)
    _save(wl)
    return True


def is_group_enabled(group_id) -> bool:
    return str(group_id).strip() in _load()


async def _enabled_check(event: MessageEvent) -> bool:
    """nonebot Rule 检查：私聊放行；群聊只放行白名单内的群。"""
    if isinstance(event, GroupMessageEvent):
        return is_group_enabled(event.group_id)
    return True

plugin_group_rule = Rule(_enabled_check)
