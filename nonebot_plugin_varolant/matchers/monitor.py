from typing import Any, Tuple

from nonebot import get_plugin_config, on_regex
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.params import RegexGroup

from ..config import Config
from ..core import database, monitor, switch

plugin_config = get_plugin_config(Config)

monitor_cmd = on_regex(
    r"^(?:/)?商店监控(?:\s+([\s\S]+?))?\s*$",
    rule=switch.plugin_group_rule,
    priority=10,
    block=True,
)


def _unquote(text: str) -> str:
    return text.strip().strip('"“”').strip()


@monitor_cmd.handle()
async def _handle_monitor(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    user_id = event.get_user_id()
    rest = (str(args[0]).strip() if args and args[0] else "")

    if not rest:
        auto_check_status = (
            "已开启" if await database.get_auto_check(user_id) else "已关闭"
        )
        help_text = (
            "商店监控功能\n\n"
            "可用子命令：\n"
            "商店监控 添加 \"皮肤 武器\" - 添加监控项\n"
            "商店监控 删除 \"皮肤 武器\" - 删除监控项\n"
            "商店监控 列表 - 查看监控列表\n"
            "商店监控 查询 - 立即执行一次监控查询\n"
            "商店监控 开启 - 启用自动查询\n"
            "商店监控 关闭 - 停用自动查询\n\n"
            f"当前自动查询状态：{auto_check_status}\n"
            f"监控时间：{plugin_config.monitor_time}\n"
            f"时区：{plugin_config.timezone}"
        )
        await monitor_cmd.finish(help_text, reply_message=True)

    parts = rest.split(maxsplit=1)
    sub_command = parts[0].strip()

    if sub_command == "添加" and len(parts) >= 2:
        item_name = _unquote(parts[1])
        if not item_name:
            await monitor_cmd.finish("请提供商品名称，例如：商店监控 添加 \"侦察力量 幻象\"", reply_message=True)
        success = await database.add_watch_item(user_id, item_name)
        if success:
            await monitor_cmd.finish(f"已添加监控项 \"{item_name}\"", reply_message=True)
        else:
            await monitor_cmd.finish(f"监控项 \"{item_name}\" 已存在", reply_message=True)

    elif sub_command == "删除" and len(parts) >= 2:
        item_name = _unquote(parts[1])
        if not item_name:
            await monitor_cmd.finish("请提供商品名称，例如：商店监控 删除 \"侦察力量 幻象\"", reply_message=True)
        success = await database.remove_watch_item(user_id, item_name)
        if success:
            await monitor_cmd.finish(f"已从监控列表删除 \"{item_name}\"", reply_message=True)
        else:
            await monitor_cmd.finish(f"监控列表中不存在 \"{item_name}\"", reply_message=True)

    elif sub_command == "列表":
        watchlist = await database.get_watchlist(user_id)
        if not watchlist:
            await monitor_cmd.finish(
                "您的监控列表为空\n使用 商店监控 添加 \"商品名称\" 来添加监控项",
                reply_message=True,
            )
        else:
            items_text = "\n".join(
                [f"  - {item['item_name']}" for item in watchlist]
            )
            await monitor_cmd.finish(
                f"您的监控列表（{len(watchlist)}项）：\n{items_text}",
                reply_message=True,
            )

    elif sub_command == "查询":
        await monitor_cmd.send("正在执行监控查询，请稍候...", reply_message=True)
        try:
            session = ("private", user_id, event.message_id)
            if isinstance(event, GroupMessageEvent):
                session = ("group", str(event.group_id), event.message_id)
            await monitor.check_user_watchlist(user_id, plugin_config, session)
            await monitor_cmd.finish("监控查询完成", reply_message=True)
        except MatcherException:
            raise
        except Exception as e:
            logger.error(f"手动监控查询失败: {e}")
            await monitor_cmd.finish("监控查询失败，请稍后重试", reply_message=True)

    elif sub_command == "开启":
        await database.update_auto_check(user_id, 1)
        await monitor_cmd.finish(
            f"已开启自动查询\n"
            f"每天 {plugin_config.monitor_time} "
            f"({plugin_config.timezone}) 执行\n"
            "监控到上架后会自动通知你",
            reply_message=True,
        )

    elif sub_command == "关闭":
        await database.update_auto_check(user_id, 0)
        await monitor_cmd.finish("已关闭自动查询", reply_message=True)

    else:
        await monitor_cmd.finish("未知子命令，请使用 商店监控 查看帮助", reply_message=True)
