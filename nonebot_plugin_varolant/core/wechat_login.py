import asyncio
import hashlib
import json
import random
import re
import string
import time
from typing import Any, Dict, List, Optional

import aiohttp
from nonebot.log import logger

from ..const import (
    WECHAT_APP_ID,
    WECHAT_LOGIN_URL,
    WECHAT_LONG_POLL_URL,
    WECHAT_POLL_UA,
    WECHAT_QRCONNECT_URL,
    WECHAT_TICKET_URL,
    WECHAT_TICKET_UA,
)

# 进行中的轮询任务，用于「瓦登录 清除」时取消
_wechat_login_tasks: Dict[str, List[asyncio.Task]] = {}


def cancel_wechat_tasks(user_id: str) -> None:
    """取消指定用户尚未完成的微信登录轮询。"""
    for task in _wechat_login_tasks.pop(user_id, []):
        if not task.done():
            task.cancel()


def _forget_wechat_task(user_id: str, task: asyncio.Task) -> None:
    """任务结束后从登记表移除，避免不同用户登录后长期积累已完成任务。"""
    tasks = _wechat_login_tasks.get(user_id)
    if not tasks:
        return
    try:
        tasks.remove(task)
    except ValueError:
        return
    if not tasks:
        _wechat_login_tasks.pop(user_id, None)


async def create_wechat_qr(user_id: str) -> Optional[Dict[str, Any]]:
    """申请微信登录二维码，返回 ``{"uuid": ..., "qr_bytes": ...}``。"""
    timestamp = str(int(time.time()))
    noncestr = "".join(random.choices(string.ascii_letters + string.digits, k=6))

    async with aiohttp.ClientSession() as session:
        ticket_payload = {
            "clienttype": 9,
            "config_params": {"client_dev_name": "22041216C", "lang_type": 0},
            "mappid": 10200,
            "mcode": "69028af6dca2c107f4f58290100011b1a303",
            "sdk_appid": WECHAT_APP_ID,
            "source_game_zone": "agame",
            "game_zone": "agame",
        }
        ticket_headers = {
            "user-agent": WECHAT_TICKET_UA,
            "content-type": "application/json",
        }

        async with session.post(
            WECHAT_TICKET_URL, headers=ticket_headers, json=ticket_payload
        ) as resp:
            ticket_resp = await resp.json(content_type=None)

        sdk_ticket = ticket_resp.get("data", {}).get("ticket", "")
        if not sdk_ticket:
            logger.error("获取微信登录 sdk_ticket 失败")
            return None

        # 签名规则：sha1("appid=xxx&noncestr=xxx&sdk_ticket=xxx&timestamp=xxx")
        raw_string = (
            f"appid={WECHAT_APP_ID}&noncestr={noncestr}"
            f"&sdk_ticket={sdk_ticket}&timestamp={timestamp}"
        )
        signature = hashlib.sha1(raw_string.encode("utf-8")).hexdigest()

        params = {
            "appid": WECHAT_APP_ID,
            "noncestr": noncestr,
            "timestamp": timestamp,
            "scope": "snsapi_userinfo",
            "signature": signature,
        }
        headers = {
            "User-Agent": WECHAT_POLL_UA,
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

        async with session.get(
            WECHAT_QRCONNECT_URL + "?f=json", params=params, headers=headers
        ) as resp:
            resp_text = await resp.text()
            try:
                result = await resp.json(content_type=None)
            except Exception:
                result = json.loads(resp_text)

            if result.get("errcode") != 0:
                logger.error(f"获取微信二维码失败: {result.get('errmsg', '未知错误')}")
                return None

            uuid = result.get("uuid")
            qrcode_base64 = result.get("qrcode", {}).get("qrcodebase64", "")
            if not uuid or not qrcode_base64:
                logger.error("微信二维码数据不完整")
                return None

            import base64

            if "," in qrcode_base64:
                qrcode_base64 = qrcode_base64.split(",", 1)[1]
            qr_bytes = base64.b64decode(qrcode_base64)
            return {"uuid": uuid, "qr_bytes": qr_bytes}


async def wait_wechat_login(user_id: str, uuid: str) -> Optional[Dict[str, Any]]:
    """长轮询等待微信扫码结果，成功返回含 userId/tid 的字典。"""
    logger.info(f"开始微信扫码登录任务，user_id: {user_id}")
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": WECHAT_POLL_UA,
                "Content-Type": "application/json",
                "Accept": "*/*",
            }

            wx_code = None
            last_code = None
            for _ in range(30):
                await asyncio.sleep(2)

                poll_url = f"{WECHAT_LONG_POLL_URL}?f=json&uuid={uuid}"
                if last_code is not None:
                    poll_url += f"&last={last_code}"

                async with session.get(poll_url, headers=headers) as resp:
                    try:
                        if resp.status != 200:
                            continue
                        resp_text = await resp.text()
                        try:
                            result = await resp.json(content_type=None)
                        except Exception:
                            # 微信长轮询偶尔会返回 "window.wx_errcode=408;" 这样的脚本片段
                            if "window.wx_errcode" in resp_text:
                                errcode_match = re.search(r"wx_errcode=(\d+)", resp_text)
                                code_match = re.search(r"wx_code='([^']+)'", resp_text)
                                result = {
                                    "wx_errcode": int(errcode_match.group(1)) if errcode_match else 408,
                                    "wx_code": code_match.group(1) if code_match else "",
                                }
                            else:
                                result = json.loads(resp_text)

                        wx_errcode = result.get("wx_errcode")
                        last_code = wx_errcode

                        if wx_errcode in (0, 405) and result.get("wx_code"):
                            logger.info("扫码成功，获取到 wx_code")
                            wx_code = result.get("wx_code")
                            break
                        elif wx_errcode == 404:
                            logger.info("扫码中，等待点击确认...")
                            continue
                        elif wx_errcode == 408:
                            continue
                        else:
                            logger.info(f"扫码异常状态: {wx_errcode}")
                            return None
                    except Exception as e:
                        logger.error(f"解析微信扫码状态失败: {e}")
                        return None

            if not wx_code:
                return None

            payload = {
                "clienttype": 9,
                "config_params": {"client_dev_name": "22041216C", "lang_type": 0},
                "login_info": {
                    "appid": WECHAT_APP_ID,
                    "check_third_type": 1,
                    "code": wx_code,
                    "wx_info_type": 1,
                },
                "mappid": 10200,
                "mcode": "69028af6dca2c107f4f58290100011b1a303",
                "source_game_zone": "agame",
                "game_zone": "agame",
            }
            login_headers = {
                "user-agent": WECHAT_TICKET_UA,
                "content-type": "application/json",
                "cookie": "clientType=9; openid=null; access_token=null;",
            }
            async with session.post(WECHAT_LOGIN_URL, headers=login_headers, json=payload) as resp:
                login_result = await resp.json(content_type=None)
                logger.info(
                    "login_by_wechat 完成: "
                    f"result={login_result.get('result')}, "
                    f"has_login_info={bool(login_result.get('data', {}).get('login_info'))}"
                )

                login_info = login_result.get("data", {}).get("login_info", {})
                if login_info and login_info.get("result") == 0:
                    return {
                        "userId": login_info.get("user_id"),
                        "tid": login_info.get("wt"),
                        "openid": login_info.get("openid"),
                        "uin": 0,
                        "access_token": login_info.get("access_token"),
                        "login_type": "wechat",
                    }
                logger.error(
                    "微信登录验证失败: "
                    f"result={login_result.get('result')}, "
                    f"msg={login_result.get('msg') or login_result.get('errMsg') or '未知错误'}"
                )
                return None

    except asyncio.TimeoutError:
        logger.warning("微信登录轮询超时")
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"微信登录轮询异常: {e}")
        return None


async def start_wechat_login(user_id: str) -> Optional[Dict[str, Any]]:
    """整体流程：先申请二维码，再登记轮询任务。

    返回 ``{"qr_bytes": ..., "task": ...}``；二维码申请失败返回 None。
    """
    cancel_wechat_tasks(user_id)

    qr_ctx = await create_wechat_qr(user_id)
    if not qr_ctx:
        return None

    task = asyncio.create_task(wait_wechat_login(user_id, qr_ctx["uuid"]))
    _wechat_login_tasks.setdefault(user_id, []).append(task)
    task.add_done_callback(lambda done: _forget_wechat_task(user_id, done))
    return {"qr_bytes": qr_ctx["qr_bytes"], "task": task}
