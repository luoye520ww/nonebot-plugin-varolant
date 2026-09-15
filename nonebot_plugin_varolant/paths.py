import hashlib
import re
import shutil
from functools import lru_cache
from pathlib import Path

from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as localstore

_PLUGIN_ROOT = Path(__file__).resolve().parent
_LEGACY_DATA_DIR = _PLUGIN_ROOT / "data"
_DATA_MIGRATED = False


@lru_cache(maxsize=1)
def _data_dir() -> Path:
    return localstore.get_plugin_data_dir()


@lru_cache(maxsize=1)
def _cache_dir() -> Path:
    return localstore.get_plugin_cache_dir()


def plugin_root() -> Path:
    """插件包根目录（nonebot_plugin_varolant/）。"""
    return _PLUGIN_ROOT


def data_dir() -> Path:
    global _DATA_MIGRATED
    path = _data_dir()
    if not _DATA_MIGRATED and _LEGACY_DATA_DIR.exists():
        for source in _LEGACY_DATA_DIR.iterdir():
            target = path / source.name
            if not target.exists():
                shutil.move(str(source), str(target))
        _DATA_MIGRATED = True
    return path


def cache_dir() -> Path:
    return _cache_dir()


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
