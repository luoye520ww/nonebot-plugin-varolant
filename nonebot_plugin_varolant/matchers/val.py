import asyncio
from typing import Any, Optional, Tuple

from nonebot import get_plugin_config, on_regex
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.params import RegexGroup

from ..config import Config, normalize_login_mode
from ..core import database, switch, wegame
from ..core.qq_login import generate_qr_code, get_final_cookies, wait_for_login_result
from ..core.render import build_help_image
from ..core.store import test_config_validity
from ..core.wechat_login import cancel_wechat_tasks, start_wechat_login
from ..core.wegame_login import generate_wegame_qr, wait_wegame_login

plugin_config = get_plugin_config(Config)
CLEAR_ALIASES = {"清除", "清空", "解绑", "clear", "reset", "remove", "delete"}
HELP_ALIASES = {"帮助", "help", "?", "？"}

val_cmd = on_regex(
    r"^(?:/)?(?:瓦登录|无畏契约登录)(?:\s+([\s\S]+?))?\s*$",
    rule=switch.plugin_group_rule, priority=10, block=True,
)
app_cmd = on_regex(
    r"^(?:/)?(?:瓦app登录|无畏契约app登录)(?:\s+([\s\S]+?))?\s*$",
    rule=switch.plugin_group_rule, priority=10, block=True,
)
help_cmd = on_regex(
    r"^(?:/)?(?:瓦|无畏契约)\s*帮助\s*$",
    rule=switch.plugin_group_rule, priority=10, block=True,
)


async def _wegame_bind(user_id: str) -> None:
    ctx = await generate_wegame_qr()
    if not ctx:
        await val_cmd.finish("生成 WeGame 登录二维码失败，请稍后重试", reply_message=True)
        return
    session = ctx["session"]
    try:
        await val_cmd.send(Message([
            MessageSegment.image(ctx["qr_bytes"]),
            MessageSegment.text("请使用微信在 60 秒内扫码并确认登录 WeGame"),
        ]), reply_message=True)
        result = await wait_wegame_login(ctx, timeout=60)
        if not result:
            await val_cmd.finish("WeGame 登录失败或二维码已超时，请重新发送「瓦登录」", reply_message=True)
            return
        client = wegame.get_client(result)
        try:
            role = await client.resolve_own_subject()
        finally:
            await client.close()
        result.update({"subject": role.subject, "nickname": role.name})
        await database.save_wegame_config(user_id, result)
        await val_cmd.finish(
            f"WeGame 登录成功！\n角色：{role.name or '已识别'}\n"
            "登录态将由插件自动续期。\n"
            "战绩、双方 10 人详情、战报、地图、英雄池、开黑和武器数据均使用此登录态。\n"
            "每日商店请另用「瓦app登录」。",
            reply_message=True,
        )
    finally:
        await session.close()


async def _qq_app_bind(user_id: str) -> None:
    await app_cmd.send("正在生成掌瓦 App QQ 登录二维码，请稍候...", reply_message=True)
    ctx = await generate_qr_code(plugin_config.login_callback_url,
                                 plugin_config.login_u1_url)
    if not ctx:
        await app_cmd.finish("生成登录二维码失败，请稍后重试", reply_message=True)
        return
    session = ctx["session"]
    try:
        await app_cmd.send(Message([
            MessageSegment.image(ctx["qr_bytes"]),
            MessageSegment.text("请在 30 秒内扫码登录掌瓦 App"),
        ]), reply_message=True)
        login_data = await wait_for_login_result(
            session=session, ptqrtoken=ctx["ptqrtoken"],
            login_sig=ctx.get("login_sig", ""),
            login_u1=ctx.get("u1_url", plugin_config.login_u1_url),
            referer_url=ctx.get("login_url", ""),
            pt_openlogin_data=ctx.get("pt_openlogin_data", ""),
            aegis_uid=ctx.get("aegis_uid", ""), jsver=ctx.get("jsver", "28d22679"),
            pt_uistyle=ctx.get("pt_uistyle", "35"), ptlang=ctx.get("ptlang", "2052"),
            timeout=30,
        )
        final = await get_final_cookies(login_data) if login_data else None
        if not final:
            await app_cmd.finish("掌瓦 App 登录失败或超时，请重试", reply_message=True)
            return
        count = await database.save_user_config(
            user_id, final["userId"], final["tid"], final.get("nickname"),
            openid=final.get("openid", ""), uin=int(final.get("uin") or 0),
            access_token=final.get("access_token", ""),
        )
        await app_cmd.finish(
            f"掌瓦 App 登录成功，已设为当前商店账号（共 {count} 个）。\n"
            "此登录仅用于每日商店与商店监控；战绩功能请使用「瓦登录」。",
            reply_message=True,
        )
    finally:
        await session.close()


async def _wx_app_bind(user_id: str) -> None:
    ctx = await start_wechat_login(user_id)
    if not ctx:
        await app_cmd.finish("获取掌瓦 App 微信二维码失败，请稍后重试", reply_message=True)
        return
    await app_cmd.send(Message([
        MessageSegment.image(ctx["qr_bytes"]),
        MessageSegment.text("请使用微信扫码登录掌瓦 App（30 秒内有效）"),
    ]), reply_message=True)
    try:
        result: Optional[dict] = await ctx["task"]
    except asyncio.CancelledError:
        return
    if not result or not all(result.get(k) for k in ("userId", "tid")):
        await app_cmd.finish("掌瓦 App 登录失败或二维码已过期，请重试", reply_message=True)
        return
    count = await database.save_user_config(
        user_id, result["userId"], result["tid"], result.get("nickname"),
        openid=result.get("openid", ""), uin=int(result.get("uin") or 0),
        access_token=result.get("access_token", ""),
    )
    await app_cmd.finish(
        f"掌瓦 App 登录成功，已设为当前商店账号（共 {count} 个）。\n"
        "此登录仅用于每日商店与商店监控；战绩功能请使用「瓦登录」。",
        reply_message=True,
    )


def _help_image() -> bytes:
    return build_help_image(plugin_config.default_login_mode,
                            plugin_config.monitor_time, plugin_config.timezone)


@help_cmd.handle()
async def _handle_help_shortcut():
    await help_cmd.finish(MessageSegment.image(_help_image()), reply_message=True)


@val_cmd.handle()
async def _handle_wegame(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    user_id = event.get_user_id().strip()
    raw = str(args[0]).strip() if args and args[0] else ""
    lower = raw.lower()
    if raw and (raw in CLEAR_ALIASES or lower in CLEAR_ALIASES):
        cleared = await database.clear_wegame_config(user_id)
        await val_cmd.finish("已清除 WeGame 战绩登录态" if cleared else "当前未绑定 WeGame", reply_message=True)
    if raw and (raw in HELP_ALIASES or lower in HELP_ALIASES):
        await val_cmd.finish(MessageSegment.image(_help_image()), reply_message=True)
    if raw and lower not in {"wx", "wechat", "微信", "wegame"}:
        await val_cmd.finish(
            "参数无效：瓦登录 / 瓦登录 wx / 瓦登录 清除\n"
            "每日商店登录：瓦app登录",
            reply_message=True,
        )
    if not raw:
        cfg = await database.get_wegame_config(user_id)
        if cfg:
            async def save(updated: dict) -> None:
                await database.save_wegame_config(user_id, updated)

            client = wegame.get_client(cfg, save)
            try:
                role = await client.resolve_own_subject()
                await val_cmd.finish(
                    f"WeGame 已登录：{role.name or cfg.get('nickname') or '已识别角色'}\n"
                    f"自动续期：{'已启用' if cfg.get('ct') else '需重新扫码一次启用'}\n"
                    "如需刷新登录态，请发送「瓦登录 wx」",
                    reply_message=True,
                )
            except wegame.WegameError as e:
                if not e.auth_invalid:
                    logger.warning(f"WeGame 登录态检查失败: {e}")
            finally:
                await client.close()
    try:
        await _wegame_bind(user_id)
    except MatcherException:
        raise
    except Exception as e:
        logger.error(f"WeGame 登录异常: {type(e).__name__}: {e}")
        await val_cmd.finish("WeGame 登录过程出错，请稍后重试", reply_message=True)


@app_cmd.handle()
async def _handle_app(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    user_id = event.get_user_id().strip()
    raw = str(args[0]).strip() if args and args[0] else ""
    lower = raw.lower()
    if raw and (raw in CLEAR_ALIASES or lower in CLEAR_ALIASES):
        cancel_wechat_tasks(user_id)
        cleared = await database.clear_app_configs(user_id)
        await app_cmd.finish("已清除全部掌瓦 App 商店账号" if cleared else "当前没有掌瓦 App 商店账号", reply_message=True)
    if raw and (raw in HELP_ALIASES or lower in HELP_ALIASES):
        await app_cmd.finish(MessageSegment.image(_help_image()), reply_message=True)
    mode = normalize_login_mode(raw) if raw else (
        normalize_login_mode(plugin_config.default_login_mode) or "wx"
    )
    if raw and not mode:
        await app_cmd.finish("参数无效：瓦app登录 qq / 瓦app登录 wx / 瓦app登录 清除", reply_message=True)
    if not raw:
        cfg = await database.get_user_config(user_id)
        if cfg and await test_config_validity(user_id, cfg):
            await app_cmd.finish(
                f"掌瓦 App 商店账号已绑定（共 {cfg.get('accounts_count', 1)} 个）。\n"
                "此登录仅用于每日商店；战绩请使用「瓦登录」。",
                reply_message=True,
            )
    try:
        if mode == "wx":
            await _wx_app_bind(user_id)
        else:
            await _qq_app_bind(user_id)
    except MatcherException:
        raise
    except Exception as e:
        logger.error(f"掌瓦 App 登录异常: {type(e).__name__}: {e}")
        await app_cmd.finish("掌瓦 App 登录过程出错，请稍后重试", reply_message=True)
