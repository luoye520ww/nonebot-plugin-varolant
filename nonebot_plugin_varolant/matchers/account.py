from typing import Any, Tuple

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.log import logger
from nonebot.params import RegexGroup

from ..core import database, switch

_R = switch.plugin_group_rule

account_cmd = on_regex(
    r"^(?:/)?(?:瓦|无畏契约)\s*账号\s*$", rule=_R, priority=10, block=True
)
switch_account_cmd = on_regex(
    r"^(?:/)?(?:瓦|无畏契约)\s*切换账号\s+([\s\S]+?)\s*$",
    rule=_R, priority=10, block=True,
)
delete_account_cmd = on_regex(
    r"^(?:/)?(?:瓦|无畏契约)\s*删除账号\s+([\s\S]+?)\s*$",
    rule=_R, priority=10, block=True,
)


def _render_account_list(accounts) -> str:
    lines = []
    for acc in accounts:
        tags = ["🛒每日商店" if acc["has_shop"] else "·凭证不完整"]
        prefix = "✅当前" if acc["is_active"] else f"{acc['index'] + 1}."
        lines.append(f"{prefix} {acc['label']}（{' '.join(tags)}）")
    return "\n".join(lines)


@account_cmd.handle()
async def _handle_account_list(event: MessageEvent):
    user_id = event.get_user_id().strip()
    accounts = await database.list_accounts(user_id)
    if not accounts:
        await account_cmd.finish(
            "你还没有绑定任何账号\n"
            "发送「瓦app登录」扫码绑定第一个每日商店账号",
            reply_message=True,
        )
    body = _render_account_list(accounts)
    await account_cmd.finish(
        f"你已绑定 {len(accounts)} 个账号：\n"
        f"{body}\n\n"
        "切换：瓦 切换账号 <序号或昵称>\n"
        "删除：瓦 删除账号 <序号或昵称>\n"
        "新增：瓦app登录 qq / 瓦app登录 wx",
        reply_message=True,
    )


@switch_account_cmd.handle()
async def _handle_account_switch(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    user_id = event.get_user_id().strip()
    key = str(args[0]).strip() if args and args[0] else ""
    ok, label = await database.set_active_account(user_id, key)
    if not ok:
        await switch_account_cmd.finish(label, reply_message=True)
    logger.info(f"用户 {user_id} 切换当前账号 -> {label}")
    await switch_account_cmd.finish(
        f"已切换到账号「{label}」\n"
        "此后每日商店以该账号为准；战绩账号不受影响",
        reply_message=True,
    )


@delete_account_cmd.handle()
async def _handle_account_delete(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    user_id = event.get_user_id().strip()
    key = str(args[0]).strip() if args and args[0] else ""
    ok, label, remaining = await database.delete_account(user_id, key)
    if not ok:
        await delete_account_cmd.finish(label, reply_message=True)
    logger.info(f"用户 {user_id} 删除账号 {label}，剩余 {remaining}")
    if remaining > 0:
        await delete_account_cmd.finish(
            f"已删除账号「{label}」，还剩 {remaining} 个账号\n"
            "发送「瓦 账号」查看当前账号",
            reply_message=True,
        )
    await delete_account_cmd.finish(
        f"已删除账号「{label}」，你已没有任何绑定账号\n"
        "（商店监控列表与自动开关仍保留，重新绑定后继续生效）\n"
        "如需重新绑定：瓦app登录 qq / 瓦app登录 wx",
        reply_message=True,
    )
