"""数据与缓存目录工具。

数据与缓存统一存放在插件包目录内（``nonebot_plugin_varolant/data`` 与
``nonebot_plugin_varolant/cache``），保证插件是一个完整体：把整个插件目录
丢进任意 NoneBot2 项目的 plugins 目录即可运行，用户数据（
``data/<QQ号>.json``）随插件目录一起走，不会散落在系统目录里。
"""

import hashlib
import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent


def plugin_root() -> Path:
    """插件包根目录（nonebot_plugin_varolant/）。"""
    return _PLUGIN_ROOT


def data_dir() -> Path:
    """持久化数据目录：<插件>/data。账号数据按 QQ 分文件存放。"""
    path = _PLUGIN_ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """缓存目录：<插件>/cache（二维码、素材图、名称映射等，可安全清空）。"""
    path = _PLUGIN_ROOT / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_user_dir(user_id: str) -> Path:
    """按用户隔离的临时目录，目录名做清洗避免路径穿越。"""
    raw = str(user_id or "").strip()
    if not raw:
        raise ValueError("user_id 为空，无法创建临时目录")

    normalized = re.sub(r"[^0-9A-Za-z_-]", "_", raw).strip("_")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    safe_segment = f"{(normalized[:32] or 'user')}_{digest}"

    base = cache_dir()
    user_dir = (base / safe_segment).resolve()
    if user_dir != base and base not in user_dir.parents:
        raise ValueError(f"检测到非法临时目录路径: {raw}")
    return user_dir


def temp_user_file(user_id: str, filename: str) -> Path:
    """在用户临时目录下构造文件路径，防止目录逃逸。"""
    safe_filename = Path(str(filename or "")).name
    if not safe_filename:
        raise ValueError("文件名为空，无法创建临时文件路径")

    user_dir = temp_user_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    file_path = (user_dir / safe_filename).resolve()
    if file_path.parent != user_dir:
        raise ValueError(f"检测到非法临时文件路径: {filename}")
    return file_path
