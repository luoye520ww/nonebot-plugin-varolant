"""插件配置定义。

插件已内置开箱即用的默认值，无需配置 ``.env``。如有特殊部署需求，
仍可使用 ``varolant_`` 前缀覆盖对应设置，例如：

.. code-block:: ini

    VAROLANT_DEFAULT_LOGIN_MODE=wx
    VAROLANT_MONITOR_TIME=08:01
    VAROLANT_TIMEZONE=Asia/Shanghai
    VAROLANT_BOT_ID=123456789

所有配置项均使用 ``VAROLANT_`` 前缀，避免与其他插件配置冲突。
"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """varolant 插件配置。"""

    varolant_default_login_mode: str = Field(
        default="wx",
        description="发送「瓦app登录」不带参数时默认使用的掌瓦 App 登录方式（qq / wx）",
    )
    varolant_monitor_time: str = Field(
        default="08:01",
        description="每日商店监控的执行时间，格式 HH:MM",
    )
    varolant_timezone: str = Field(
        default="Asia/Shanghai",
        description="监控定时任务使用的时区",
    )
    varolant_bot_id: str = Field(
        default="",
        description="监控通知使用的机器人 QQ 号；留空则自动取当前在线的第一个机器人",
    )
    varolant_login_callback_url: str = Field(
        default="http://connect.qq.com",
        description="QQ 扫码登录的业务回调地址（s_url），一般无需修改",
    )
    varolant_login_u1_url: str = Field(
        default="http://connect.qq.com",
        description="QQ 扫码登录轮询的 u1 地址，一般无需修改",
    )


def normalize_login_mode(mode: str) -> str:
    """把用户输入的登录方式归一化成 ``qq`` / ``wx``，无法识别时返回空串。"""
    value = str(mode or "").strip().lower()
    if value in {"qq", "q"}:
        return "qq"
    if value in {"wx", "w", "wechat", "weixin", "微信"}:
        return "wx"
    return ""
