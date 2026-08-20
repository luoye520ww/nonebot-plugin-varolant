from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger

from ..core import database, switch
from ..core.render import build_shop_image
from ..core.store import extract_goods_list, request_store_api

shop_cmd = on_regex(
    r"^(?:/)?每日商店\s*$",
    rule=switch.plugin_group_rule,
    priority=10,
    block=True,
)


def _get_at_target(event: MessageEvent) -> str:
    """取消息里第一个 @ 的非机器人用户，没有则返回空串。"""
    try:
        for seg in event.get_message():
            if seg.type == "at":
                qq = str(seg.data.get("qq", ""))
                if qq and qq != str(event.self_id):
                    return qq
    except Exception as e:
        logger.error(f"获取被@用户ID失败: {e}")
    return ""


@shop_cmd.handle()
async def _handle_shop(event: MessageEvent):
    target_user_id = _get_at_target(event)
    if target_user_id:
        logger.info(f"检测到@用户，目标用户ID: {target_user_id}")

    user_id = target_user_id or event.get_user_id()
    user_config = await database.get_user_config(user_id)
    if not user_config:
        if target_user_id:
            await shop_cmd.finish(f"用户 {target_user_id} 未绑定账号", reply_message=True)
        else:
            await shop_cmd.finish("您尚未绑定每日商店账号，请先使用「瓦app登录」扫码绑定", reply_message=True)

    if not (user_config.get("userId") and user_config.get("tid")):
        await shop_cmd.finish(
            "当前账号缺少商店所需的登录凭证（userId/tid）\n"
            "请使用 瓦app登录 qq / 瓦app登录 wx 扫码补绑商店账号，"
            "或用「瓦 切换账号」切到已绑定商店的账号",
            reply_message=True,
        )

    logger.info(f"开始为用户 {user_id} 获取商店信息")

    # 先探测凭证，避免过期配置继续漏到图片生成链路
    response_data, err_msg, auth_invalid = await request_store_api(
        user_id,
        user_config,
        max_retries=1,
        timeout=10,
    )
    if not response_data:
        if auth_invalid:
            if target_user_id:
                await shop_cmd.finish(
                    f"用户 {target_user_id} 的登录凭证已过期，请对方重新使用 瓦app登录 绑定后再试",
                    reply_message=True,
                )
            else:
                await shop_cmd.finish("当前商店凭证已过期，请使用 瓦app登录 重新绑定后再试", reply_message=True)
        else:
            if target_user_id:
                await shop_cmd.finish(
                    f"获取用户 {target_user_id} 的商店信息失败: {err_msg or '请稍后重试'}",
                    reply_message=True,
                )
            else:
                await shop_cmd.finish(f"获取商店信息失败: {err_msg or '请稍后重试'}", reply_message=True)

    goods_list, parse_err = extract_goods_list(response_data)
    if parse_err:
        if target_user_id:
            await shop_cmd.finish(f"获取用户 {target_user_id} 的商店信息失败: {parse_err}", reply_message=True)
        else:
            await shop_cmd.finish(f"获取商店信息失败: {parse_err}", reply_message=True)

    if not goods_list:
        if target_user_id:
            await shop_cmd.finish(f"用户 {target_user_id} 今日商店暂无可用数据", reply_message=True)
        else:
            await shop_cmd.finish("今日商店暂无可用数据，请稍后再试", reply_message=True)

    image_bytes = await build_shop_image(user_id, user_config, goods_list)
    if image_bytes:
        await shop_cmd.finish(MessageSegment.image(image_bytes), reply_message=True)

    if target_user_id:
        await shop_cmd.finish(f"获取用户 {target_user_id} 的商店信息失败，请稍后重试", reply_message=True)
    else:
        await shop_cmd.finish("获取商店信息失败，请稍后重试", reply_message=True)
