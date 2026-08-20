from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from nonebot import get_bots
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger

from ..config import Config
from . import database
from .store import get_shop_items_raw

# 通知目标会话：(类型, 目标 ID, 被引用消息 ID)；None 表示定时私聊本人
SessionTarget = Optional[Tuple[str, str, Optional[int]]]


async def check_user_watchlist(
    user_id: str, config: Config, session: SessionTarget = None
) -> Optional[List[Dict[str, Any]]]:
    """检查单个用户的监控列表，返回命中的商品（未命中返回 None）。

    命中时会顺带把通知发出去：默认私聊本人；调用方传入 ``session``
    时（例如群内手动查询）发到来源会话。
    """
    logger.info(f"开始检查用户 {user_id} 的监控列表")

    user_config = await database.get_user_config(user_id)
    if not user_config:
        logger.warning(f"用户 {user_id} 未绑定配置，跳过监控")
        return None

    watchlist = await database.get_watchlist(user_id)
    if not watchlist:
        logger.info(f"用户 {user_id} 监控列表为空")
        return None

    goods_list = await get_shop_items_raw(user_id, user_config)
    if not goods_list:
        logger.info(f"用户 {user_id} 商店数据为空或获取失败")
        return None

    matched_items: List[Dict[str, Any]] = []
    watchlist_names = [item["item_name"] for item in watchlist]

    for goods in goods_list:
        goods_name = goods.get("goods_name", "")
        for watch_name in watchlist_names:
            # 双向包含匹配， skins 命名经常一长一短
            if watch_name in goods_name or goods_name in watch_name:
                matched_items.append(
                    {"name": goods_name, "price": goods.get("rmb_price", "0")}
                )
                logger.info(f"匹配成功: {goods_name}")
                break

    if not matched_items:
        logger.info(f"用户 {user_id} 今日无监控商品上架")
        return None

    logger.info(f"用户 {user_id} 命中 {len(matched_items)} 个监控商品")
    await send_notification(user_id, matched_items, config, session)
    return matched_items


def _pick_bot(config: Config):
    """选择发通知用的机器人实例。"""
    bots = get_bots()
    if not bots:
        return None
    if config.bot_id and config.bot_id in bots:
        return bots[config.bot_id]
    # 未配置或配置的号当前不在线时，退化为第一个在线机器人
    return next(iter(bots.values()))


async def send_notification(
    user_id: str,
    matched_items: List[Dict[str, Any]],
    config: Config,
    session: SessionTarget = None,
) -> None:
    """把监控命中结果发给用户，默认私聊，可指定来源会话。"""
    try:
        bot = _pick_bot(config)
        if bot is None:
            logger.error("当前没有在线的机器人，无法发送监控通知")
            return

        current_date = datetime.now().strftime("%Y-%m-%d")
        items_text = "\n".join(
            f"  - {item['name']} ({item['price']})" for item in matched_items
        )
        matched_names = [item["name"] for item in matched_items]

        notification_text = (
            f"{current_date} 商店监控通知\n\n"
            f"以下监控商品已上架：\n"
            f"{items_text}\n\n"
            f"请使用「每日商店」查看详情\n\n"
            f"匹配商品：{', '.join(matched_names)}"
        )
        sent_to_group = False
        if session and session[0] == "group":
            from . import switch

            if switch.is_group_enabled(session[1]):
                await bot.call_api(
                    "send_msg",
                    message_type="group",
                    group_id=int(session[1]),
                    message=(MessageSegment.reply(session[2]) + notification_text
                             if session[2] is not None else notification_text),
                )
                logger.info(f"已发送通知到群 {session[1]}（用户 {user_id}）")
                sent_to_group = True
            else:
                logger.info(f"群 {session[1]} 未开启插件，监控通知改为私聊用户 {user_id}")
        if not sent_to_group:
            private_message = notification_text
            if session and session[0] == "private" and session[2] is not None:
                private_message = MessageSegment.reply(session[2]) + notification_text
            await bot.call_api(
                "send_private_msg", user_id=int(user_id), message=private_message
            )
            logger.info(f"已发送通知给用户 {user_id}")
    except Exception as e:
        logger.error(f"发送通知失败: {e}")


async def daily_auto_check(config: Config) -> None:
    """定时任务入口：遍历所有打开自动监控的用户。"""
    logger.info("开始执行每日自动监控任务")
    try:
        users = await database.list_auto_check_users()
        if not users:
            logger.info("当前没有开启自动监控的用户")
            return

        logger.info(f"自动监控用户数量: {len(users)}")
        for user_id in users:
            try:
                await check_user_watchlist(user_id, config)
            except Exception as e:
                logger.error(f"检查用户 {user_id} 监控列表时出错: {e}")
                continue
    except Exception as e:
        logger.error(f"每日自动监控任务执行失败: {e}")
