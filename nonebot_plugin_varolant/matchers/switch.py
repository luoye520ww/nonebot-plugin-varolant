from typing import Any, Tuple

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER, GroupMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.params import RegexGroup

from ..core import switch

_SWITCH_CMD = on_regex(
    r"^(?:/)?s\s+(开启|关闭|启用|停用|on|off)(?:\s*(?:瓦|无畏契约|valorant|val))?\s*$",
    permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN,
    priority=5,
    block=True,
)
_SWITCH_STATUS = on_regex(
    r"^/s\s+瓦\s*$",
    permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN,
    priority=5,
    block=True,
)

_ON_WORDS = {"开启", "启用", "on"}


@_SWITCH_CMD.handle()
async def _handle_switch(event: GroupMessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    action = str(args[0]).strip().lower() if args and args[0] else ""
    gid = event.group_id

    if action in _ON_WORDS:
        if switch.enable_group(gid):
            await _SWITCH_CMD.finish(
                "已在本群开启「无畏契约」插件。\n"
                "现在可以使用：瓦登录、瓦app登录、每日商店、商店监控、瓦 查战绩、瓦 队友、瓦 战报、瓦 地图、瓦 英雄池、瓦 开黑、瓦 击杀",
                reply_message=True,
            )
        await _SWITCH_CMD.finish("本群已开启过「无畏契约」插件，无需重复操作", reply_message=True)
    else:
        if switch.disable_group(gid):
            await _SWITCH_CMD.finish(
                "已在本群关闭「无畏契约」插件，群聊命令将不再响应。\n"
                "如需恢复请发送 /s 开启瓦",
                reply_message=True,
            )
        await _SWITCH_CMD.finish("本群原本就未开启「无畏契约」插件", reply_message=True)


@_SWITCH_STATUS.handle()
async def _handle_switch_status(event: GroupMessageEvent):
    state = "已开启" if switch.is_group_enabled(event.group_id) else "已关闭"
    await _SWITCH_STATUS.finish(
        f"本群「无畏契约」插件状态：{state}\n"
        "· /s 开启瓦 —— 启用插件\n"
        "· /s 关闭瓦 —— 停用插件",
        reply_message=True,
    )
