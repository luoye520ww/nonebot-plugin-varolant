"""插件配置定义。

插件已内置开箱即用的默认值，无需配置 ``.env``。如有特殊部署需求，
仍可使用 ``varolant_`` 前缀覆盖对应设置，例如：

.. code-block:: ini

    VAROLANT_DEFAULT_LOGIN_MODE=wx
    VAROLANT_MONITOR_TIME=08:01
    VAROLANT_TIMEZONE=Asia/Shanghai
    VAROLANT_BOT_ID=123456789

字段同时兼容不带 ``VAROLANT_`` 前缀的写法（``DEFAULT_LOGIN_MODE=…``）。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class Config(BaseModel):
    """varolant 插件配置。"""

    default_login_mode: str = Field(
        default="wx",
        description="发送「瓦app登录」不带参数时默认使用的掌瓦 App 登录方式（qq / wx）",
    )
    monitor_time: str = Field(
        default="08:01",
        description="每日商店监控的执行时间，格式 HH:MM",
    )
    timezone: str = Field(
        default="Asia/Shanghai",
        description="监控定时任务使用的时区",
    )
    bot_id: str = Field(
        default="",
        description="监控通知使用的机器人 QQ 号；留空则自动取当前在线的第一个机器人",
    )
    login_callback_url: str = Field(
        default="http://connect.qq.com",
        description="QQ 扫码登录的业务回调地址（s_url），一般无需修改",
    )
    login_u1_url: str = Field(
        default="http://connect.qq.com",
        description="QQ 扫码登录轮询的 u1 地址，一般无需修改",
    )

    # NoneBot 只会从环境中收集模型真实声明的字段。把带前缀字段显式声明出来，
    # 再在校验后覆盖兼容裸名；仅用 validation_alias 会导致 VAROLANT_* 被静默忽略。
    varolant_default_login_mode: Optional[str] = Field(default=None, exclude=True)
    varolant_monitor_time: Optional[str] = Field(default=None, exclude=True)
    varolant_timezone: Optional[str] = Field(default=None, exclude=True)
    varolant_bot_id: Optional[str] = Field(default=None, exclude=True)
    varolant_login_callback_url: Optional[str] = Field(default=None, exclude=True)
    varolant_login_u1_url: Optional[str] = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _apply_prefixed_values(self) -> "Config":
        """带 ``VAROLANT_`` 前缀的配置优先于兼容裸名。"""
        for name in (
            "default_login_mode",
            "monitor_time",
            "timezone",
            "bot_id",
            "login_callback_url",
            "login_u1_url",
        ):
            value = getattr(self, f"varolant_{name}")
            if value is not None:
                setattr(self, name, value)
        return self


def normalize_login_mode(mode: str) -> str:
    """把用户输入的登录方式归一化成 ``qq`` / ``wx``，无法识别时返回空串。"""
    value = str(mode or "").strip().lower()
    if value in {"qq", "q"}:
        return "qq"
    if value in {"wx", "w", "wechat", "weixin", "微信"}:
        return "wx"
    return ""
