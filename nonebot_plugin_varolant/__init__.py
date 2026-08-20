from nonebot import get_driver, get_plugin_config, require
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="无畏契约助手",
    description="面向 QQ 群的国服无畏契约每日商店、皮肤监控与战绩卡片插件",
    usage="发送「瓦 帮助」查看完整指令图",
    type="application",
    homepage="https://github.com/luoye520ww/nonebot-plugin-varolant",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .core import database, monitor, mval, wegame  # noqa: E402

plugin_config = get_plugin_config(Config)

_MONITOR_JOB_ID = "varolant_daily_monitor"
_WEGAME_REFRESH_JOB_ID = "varolant_wegame_refresh"


async def _refresh_wegame_sessions() -> None:
    refreshed = 0
    for user_id, cfg in await database.list_wegame_configs():
        async def save(updated: dict, uid: str = user_id) -> None:
            await database.save_wegame_config(uid, updated)

        client = wegame.get_client(cfg, save)
        try:
            if await client.refresh_ticket():
                refreshed += 1
        except Exception as e:
            logger.warning(f"WeGame 登录态保活失败: {type(e).__name__}: {e}")
        finally:
            await client.close()
    if refreshed:
        logger.debug(f"WeGame 登录态保活完成: {refreshed} 个账号")


def _setup_scheduler() -> None:
    """按配置把每日监控挂到 apscheduler。"""
    hour, minute = 8, 1
    try:
        hour_s, minute_s = str(plugin_config.monitor_time or "08:01").split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        logger.warning(
            f"monitor_time 配置无效: {plugin_config.monitor_time}，回退为 08:01"
        )
        hour, minute = 8, 1
        plugin_config.monitor_time = "08:01"

    from apscheduler.triggers.cron import CronTrigger

    tz_name = plugin_config.timezone or "Asia/Shanghai"
    try:
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz_name)
    except Exception:
        logger.warning(f"timezone 配置无效: {tz_name}，回退为 Asia/Shanghai")
        tz_name = "Asia/Shanghai"
        plugin_config.timezone = tz_name
        trigger = CronTrigger(hour=hour, minute=minute, timezone="Asia/Shanghai")

    scheduler.add_job(
        monitor.daily_auto_check,
        trigger=trigger,
        args=[plugin_config],
        id=_MONITOR_JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _refresh_wegame_sessions,
        "interval",
        minutes=20,
        id=_WEGAME_REFRESH_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info(f"每日商店监控任务已注册: 每天 {hour:02d}:{minute:02d} ({tz_name})")

driver = get_driver()


@driver.on_startup
async def _on_startup():
    await database.init_db()
    _setup_scheduler()


@driver.on_shutdown
async def _on_shutdown():
    if scheduler.get_job(_MONITOR_JOB_ID):
        scheduler.remove_job(_MONITOR_JOB_ID)
    if scheduler.get_job(_WEGAME_REFRESH_JOB_ID):
        scheduler.remove_job(_WEGAME_REFRESH_JOB_ID)
    await mval.close_all()

from . import matchers  # noqa: E402,F401  导入即注册所有命令
