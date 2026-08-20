import asyncio
import re
import secrets
import time
from typing import Any, Dict, Optional

import aiohttp

WEGAME_HOME = "https://www.wegame.com.cn/helper/valorant/"
QR_CONNECT = "https://open.weixin.qq.com/connect/qrconnect"
QR_IMAGE = "https://open.weixin.qq.com/connect/qrcode/{uuid}"
QR_POLL = "https://lp.open.weixin.qq.com/connect/l/qrconnect"
CALLBACK = "https://www.wegame.com.cn/login/callback.html"
LOGIN_BY_WECHAT = "https://www.wegame.com.cn/api/middle/clientapi/auth/login_by_wechat"
APP_ID = "wx911818d5d92affa8"
REDIRECT_URI = f"{CALLBACK}?t=wx&c=0&a=0"
QR_STYLE = "https://wegame.gtimg.com/g.55555-r.c4663/lib/login-sdk/qrcode.css"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def _cookie(session: aiohttp.ClientSession, name: str) -> str:
    for url in (WEGAME_HOME, CALLBACK, "https://www.wegame.com.cn"):
        item = session.cookie_jar.filter_cookies(url).get(name)
        if item and item.value:
            return item.value
    return ""


async def generate_wegame_qr() -> Optional[Dict[str, Any]]:
    """按 WeGame 网页链路生成微信二维码，并保留同一登录会话。"""
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        headers={"user-agent": UA},
    )
    state = secrets.token_hex(4)
    params = {
        "appid": APP_ID,
        "scope": "snsapi_login",
        "redirect_uri": REDIRECT_URI,
        "login_type": "jssdk",
        "self_redirect": "true",
        "state": state,
        "style": "black",
        "href": QR_STYLE,
    }
    try:
        async with session.get(WEGAME_HOME) as response:
            await response.read()
            response.raise_for_status()
        async with session.get(
            QR_CONNECT,
            params=params,
            headers={
                "accept": "text/html,application/xhtml+xml",
                "referer": "https://www.wegame.com.cn/",
            },
        ) as response:
            page = await response.text(errors="ignore")
            response.raise_for_status()
            referer = str(response.url)
        match = re.search(r"/connect/qrcode/([A-Za-z0-9_-]+)", page)
        if not match:
            match = re.search(r"uuid\s*[:=]\s*['\"]([^'\"]+)", page)
        if not match:
            raise RuntimeError("微信二维码页面缺少 uuid")
        uuid = match.group(1)
        async with session.get(
            QR_IMAGE.format(uuid=uuid),
            headers={"accept": "image/*,*/*;q=0.8", "referer": referer},
        ) as response:
            qr_bytes = await response.read()
            response.raise_for_status()
        if not qr_bytes:
            raise RuntimeError("微信二维码内容为空")
        return {
            "session": session,
            "qr_bytes": qr_bytes,
            "uuid": uuid,
            "state": state,
        }
    except Exception:
        await session.close()
        return None


async def wait_wegame_login(
    ctx: Dict[str, Any], timeout: int = 60
) -> Optional[Dict[str, Any]]:
    """等待微信确认，并按抓包中的 WeGame 接口换取登录 Cookie。"""
    session: aiohttp.ClientSession = ctx["session"]
    started = time.monotonic()
    last: Optional[int] = None
    while time.monotonic() - started < timeout:
        params: Dict[str, Any] = {"uuid": ctx["uuid"]}
        if last is not None:
            params["last"] = last
        try:
            async with session.get(
                QR_POLL,
                params=params,
                headers={"accept": "*/*", "referer": "https://open.weixin.qq.com/"},
            ) as response:
                text = await response.text(errors="ignore")
            code_match = re.search(r"wx_errcode\s*=\s*(\d+)", text)
            wx_code_match = re.search(r"wx_code\s*=\s*['\"]([^'\"]*)", text)
            if not code_match:
                await asyncio.sleep(1)
                continue
            last = int(code_match.group(1))
            if last in (0, 405) and wx_code_match and wx_code_match.group(1):
                return await _exchange_ticket(
                    session, wx_code_match.group(1), str(ctx["state"])
                )
            if last in (402, 403):
                return None
            if last not in (404, 408):
                await asyncio.sleep(1)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await asyncio.sleep(1)
    return None


async def _exchange_ticket(
    session: aiohttp.ClientSession, wx_code: str, state: str
) -> Optional[Dict[str, Any]]:
    callback_params = {
        "t": "wx", "c": "0", "a": "0", "code": wx_code, "state": state,
    }
    async with session.get(
        CALLBACK,
        params=callback_params,
        headers={"referer": "https://open.weixin.qq.com/"},
    ) as response:
        await response.read()
        response.raise_for_status()
        callback_url = str(response.url)

    payload = {
        "clienttype": "1000005",
        "mappid": "10001",
        "mcode": "",
        "config_params": {"lang_type": 0},
        "login_info": {"wx_info_type": 1, "appid": APP_ID, "code": wx_code},
    }
    async with session.post(
        LOGIN_BY_WECHAT,
        json=payload,
        headers={
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://www.wegame.com.cn",
            "referer": callback_url,
        },
    ) as response:
        body = await response.json(content_type=None)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if body.get("code") not in (0, "0"):
        return None
    if (
        data.get("error_code") not in (None, 0, "0")
        or data.get("result") not in (None, 0, "0")
    ):
        return None

    fallbacks = {
        "tgp_id": data.get("user_id"),
        "tgp_ticket": data.get("wt"),
        "tgp_env": data.get("env"),
        "tgp_user_type": data.get("third_type"),
        "tgp_third_openid": data.get("third_openid"),
    }
    saved: Dict[str, Any] = {}
    for name in (
        "tgp_id", "tgp_ticket", "tgp_env", "tgp_user_type",
        "tgp_third_openid", "_qimei_uuid42", "_qimei_fingerprint",
        "_qimei_q36", "_qimei_h38",
    ):
        value = _cookie(session, name) or fallbacks.get(name) or ""
        if value != "":
            saved[name] = str(value)
    for name in (
        "ct", "refresh_wt_span", "refresh_ct_span",
    ):
        value = data.get(name)
        if value not in (None, ""):
            saved[name] = value
    saved["auth_refreshed_at"] = int(time.time())
    return saved if saved.get("tgp_ticket") else None
