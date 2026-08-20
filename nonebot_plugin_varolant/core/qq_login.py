import asyncio
import random
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from nonebot.log import logger

from ..const import (
    DEFAULT_LOGIN_CALLBACK_URL,
    DEFAULT_LOGIN_U1_URL,
    EMULATOR_UA,
    LOGIN_URL_TEMPLATE,
    OPENMOBILE_REDIRECT_URL,
    PTQR_AID,
    PTQR_DAID,
    PTQR_LOGIN_URL,
    PTQR_SHOW_URL,
    PTQR_THIRD_AID,
    QQ_HEADERS_TEMPLATE,
    QQ_LOGIN_BY_QQ_URL,
)

_XLOGIN_UA = (
    "Mozilla/5.0 (Linux; Android 12; 23117RK66C Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/101.0.4951.61 Mobile Safari/537.36"
)


def _url_location(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.netloc else ""


def _get_cookie_value(session: aiohttp.ClientSession, url: str, name: str) -> str:
    try:
        cookies = session.cookie_jar.filter_cookies(url)
        cookie = cookies.get(name)
        if cookie:
            return cookie.value
    except Exception as e:
        logger.warning(f"读取Cookie失败: {name}, {e}")
    return ""


def _calc_ptqrtoken(qrsig: str) -> int:
    token = 0
    for ch in qrsig:
        token += (token << 5) + ord(ch)
    return token & 2147483647


def _parse_ptui_callback(text: str) -> Optional[Dict[str, str]]:
    match = re.search(r"ptuiCB\('([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'", text)
    if not match:
        return None
    redirect_url = match.group(3).replace("\\/", "/").replace("\\x26", "&")
    return {
        "code": match.group(1),
        "redirect_url": redirect_url,
        "message": match.group(5),
    }


def _extract_login_data_from_success_url(success_url: str) -> Dict[str, Any]:
    def normalize_url(url: str) -> str:
        return (url or "").replace("\\/", "/").replace("\\x26", "&").strip()

    def parse_param_str(raw: str) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        if not raw:
            return parsed
        part = raw.replace("#&", "&").lstrip("&")
        for key, value in urllib.parse.parse_qs(part, keep_blank_values=True).items():
            if value:
                parsed[key] = value[0]
        return parsed

    nested_keys = {
        "u1", "url", "jump_url", "redirect_uri", "redirect_url",
        "target_url", "s_url", "f_url", "qtarget", "jump", "ru",
    }

    merged_params: Dict[str, str] = {}
    queue = [normalize_url(success_url)]
    visited = set()

    while queue:
        candidate = queue.pop(0)
        if not candidate or candidate in visited:
            continue
        visited.add(candidate)

        decoded = candidate
        for _ in range(3):
            next_decoded = urllib.parse.unquote(decoded)
            if next_decoded == decoded:
                break
            decoded = next_decoded

        parsed_url = urllib.parse.urlparse(decoded)
        candidate_params: Dict[str, str] = {}
        for raw_part in (parsed_url.query, parsed_url.fragment):
            candidate_params.update(parse_param_str(raw_part))

        if not candidate_params and ("openid=" in decoded or "access_token=" in decoded):
            candidate_params.update(parse_param_str(decoded))

        for key, value in candidate_params.items():
            if key not in merged_params:
                merged_params[key] = value

        for nested_key in nested_keys:
            nested_value = candidate_params.get(nested_key, "")
            if nested_value and nested_value not in visited:
                queue.append(normalize_url(nested_value))

    logger.info(
        f"[HTTP登录] 汇总参数键={sorted(merged_params.keys())}, "
        f"openid={bool(merged_params.get('openid'))}, "
        f"access_token={bool(merged_params.get('access_token'))}"
    )
    return {
        "openid": merged_params.get("openid", ""),
        "appid": merged_params.get("appid", ""),
        "access_token": merged_params.get("access_token", ""),
        "pay_token": merged_params.get("pay_token", ""),
        "key": merged_params.get("key", ""),
        "redirect_uri_key": merged_params.get("redirect_uri_key", ""),
        "expires_in": merged_params.get("expires_in", "7776000"),
        "pf": merged_params.get("pf", "openmobile_android"),
        "status_os": merged_params.get("status_os", "12"),
        "status_machine": merged_params.get("status_machine", ""),
        "full_params": merged_params,
    }


def _build_pt_openlogin_data(login_url: str, session: aiohttp.ClientSession) -> str:
    parsed = urllib.parse.urlparse(login_url)
    query_map = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def q(name: str, default: str = "") -> str:
        values = query_map.get(name, [])
        return values[0] if values else default

    tid = _get_cookie_value(session, "https://xui.ptlogin2.qq.com", "idt") or str(int(time.time()))
    auth_time = str(int(time.time() * 1000))
    items = [
        ("which", ""),
        ("refer_cgi", q("refer_cgi", "m_authorize")),
        ("response_type", q("response_type", "token")),
        ("client_id", q("client_id", PTQR_THIRD_AID)),
        ("state", ""),
        ("display", ""),
        ("openapi", "1011"),
        ("switch", q("switch", "1")),
        ("src", "1"),
        ("sdkv", q("sdkv", "3.5.17.lite")),
        ("sdkp", q("sdkp", "a")),
        ("tid", tid),
        ("pf", q("pf", "openmobile_android")),
        ("need_pay", "0"),
        ("browser", "0"),
        ("browser_error", ""),
        ("serial", ""),
        ("token_key", ""),
        ("redirect_uri", q("redirect_uri", "auth://tauth.qq.com/")),
        ("sign", q("sign", "")),
        ("time", q("time", "")),
        ("status_version", ""),
        ("status_os", q("status_os", "12")),
        ("status_machine", q("status_machine", "")),
        ("page_type", "1"),
        ("has_auth", "1"),
        ("update_auth", "1"),
        ("auth_time", auth_time),
        ("loginfrom", ""),
        ("h5sig", q("h5sig", "")),
        ("loginty", q("loginty", "6")),
    ]
    return urllib.parse.urlencode(items)


def _extract_jsver_from_login_page(login_page: str) -> str:
    text = login_page or ""
    patterns = [
        r"/monorepo/([0-9A-Za-z]+)/ptlogin/js/login_10\.js",
        r"/monorepo/([0-9A-Za-z]+)/ptlogin/js/",
        r"https://qq-web\.cdn-go\.cn/monorepo/([0-9A-Za-z]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return "28d22679"


def _build_aegis_uid(session: aiohttp.ClientSession) -> str:
    aegis_uid = _get_cookie_value(session, "https://xui.ptlogin2.qq.com", "__aegis_uid")
    if aegis_uid:
        return aegis_uid
    server_ip = _get_cookie_value(session, "https://xui.ptlogin2.qq.com", "pt_serverip")
    client_ip = _get_cookie_value(session, "https://xui.ptlogin2.qq.com", "pt_clientip")
    if server_ip and client_ip:
        return f"{server_ip}-{client_ip}-4458"
    return ""


def _extract_auth_url_from_callback_body(text: str) -> str:
    """从 _Callback({...}) 文本里提取 auth:// URL。"""
    if not text:
        return ""
    callback_match = re.search(r"_Callback\s*\(\s*(\{.*?\})\s*\)\s*;?\s*$", text, re.DOTALL)
    if callback_match:
        try:
            import json

            payload = json.loads(callback_match.group(1))
            callback_url = str(payload.get("url", "") or "").strip()
            if callback_url.startswith("auth://"):
                return callback_url
        except Exception as e:
            logger.warning(f"[HTTP登录] 解析_Callback JSON失败: {e}")

    auth_match = re.search(r"(auth://tauth\.qq\.com/[^\s\"'<>]+)", text)
    if auth_match:
        return auth_match.group(1)
    return ""


def _merge_login_data(base_data: Dict[str, Any], extra_data: Dict[str, Any]) -> Dict[str, Any]:
    """合并两份登录参数，优先保留已有值。"""
    base = dict(base_data or {})
    extra = dict(extra_data or {})
    merged_params: Dict[str, str] = dict(base.get("full_params", {}) or {})
    merged_params.update(extra.get("full_params", {}) or {})

    for key in (
        "openid", "appid", "access_token", "pay_token", "key",
        "redirect_uri_key", "expires_in", "pf", "status_os", "status_machine",
    ):
        if not base.get(key) and extra.get(key):
            base[key] = extra[key]
    base["full_params"] = merged_params
    return base


def _collect_redirect_key_candidates(
    session: aiohttp.ClientSession,
    login_data: Dict[str, Any],
    success_url: str,
) -> List[Tuple[str, str]]:
    """收集可用于 m_get_redirect_url 的 keystr 候选。"""
    result: List[Tuple[str, str]] = []
    seen = set()

    def add_key(value: str, source: str):
        keystr = (value or "").strip()
        if not keystr or keystr in seen:
            return
        seen.add(keystr)
        result.append((keystr, source))

    full_params = (login_data or {}).get("full_params", {}) or {}
    for key_name in ("redirect_uri_key", "keystr", "key", "uikey", "superkey", "supertoken"):
        add_key(str(full_params.get(key_name, "")), f"param:{key_name}")

    normalized_url = (success_url or "").replace("\\/", "/").replace("\\x26", "&")
    parsed = urllib.parse.urlparse(normalized_url)
    raw_parts = [parsed.query, parsed.fragment]
    if not parsed.query and not parsed.fragment:
        raw_parts.append(normalized_url)

    for raw in raw_parts:
        if not raw:
            continue
        raw_params = urllib.parse.parse_qs(raw.replace("#&", "&"), keep_blank_values=True)
        for key_name in ("redirect_uri_key", "keystr", "key", "uikey", "superkey", "supertoken"):
            values = raw_params.get(key_name, [])
            if values:
                add_key(values[0], f"url:{key_name}")

    cookie_domains = [
        "https://xui.ptlogin2.qq.com",
        "https://ssl.ptlogin2.qq.com",
        "https://ptlogin4.openmobile.qq.com",
        "https://openmobile.qq.com",
        "https://connect.qq.com",
    ]
    for domain in cookie_domains:
        host = urllib.parse.urlparse(domain).netloc
        for key_name in ("redirect_uri_key", "keystr", "uikey", "superkey", "supertoken", "key"):
            add_key(_get_cookie_value(session, domain, key_name), f"cookie:{host}:{key_name}")

    return result


async def _fetch_auth_url_by_redirect_key(
    session: aiohttp.ClientSession, redirect_uri_key: str
) -> str:
    """调用 m_get_redirect_url，根据 keystr 换取 auth:// 回调。"""
    keystr = (redirect_uri_key or "").strip()
    if not keystr:
        return ""

    headers = {
        "User-Agent": EMULATOR_UA,
        "Accept": "*/*",
        "Referer": "https://imgcache.qq.com/",
    }
    logger.info("[HTTP登录] 调用m_get_redirect_url（密钥已隐藏）")
    try:
        async with session.get(
            OPENMOBILE_REDIRECT_URL,
            params={"keystr": keystr},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20, connect=10, sock_connect=10, sock_read=15),
        ) as response:
            body = await response.text(errors="ignore")
            logger.info(f"[HTTP登录] m_get_redirect_url status={response.status}")
            if response.status != 200:
                return ""
            auth_url = _extract_auth_url_from_callback_body(body)
            if not auth_url:
                logger.warning("[HTTP登录] m_get_redirect_url未提取到auth://tauth.qq.com")
            return auth_url
    except Exception as e:
        logger.warning(f"[HTTP登录] m_get_redirect_url异常: type={type(e).__name__}, repr={repr(e)}")
        return ""


async def _resolve_login_success_url(
    session: aiohttp.ClientSession,
    success_url: str,
    referer_url: str = "",
) -> str:
    """对 check_sig 做一次解析，尝试拿到下一跳 URL。"""
    current_url = (success_url or "").replace("\\/", "/").replace("\\x26", "&").strip()
    if not current_url:
        return ""
    if "check_sig" not in current_url:
        return current_url

    headers = {
        "User-Agent": EMULATOR_UA,
        "Accept": "*/*",
        "Referer": referer_url or "https://openmobile.qq.com/",
    }
    logger.info(f"[HTTP登录] 尝试解析check_sig: {_url_location(current_url)}")
    try:
        async with session.get(
            current_url,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=15, connect=8, sock_connect=8, sock_read=10),
        ) as response:
            body = await response.text(errors="ignore")
            location = (response.headers.get("Location", "") or "").strip()
            if location:
                next_url = urllib.parse.urljoin(str(response.url), location)
                logger.info(f"[HTTP登录] check_sig下一跳: {_url_location(next_url)}")
                return next_url

            body_url = _extract_url_from_body(body)
            if body_url:
                return body_url
            logger.warning("[HTTP登录] check_sig未提取到下一跳")
    except Exception as e:
        logger.warning(f"[HTTP登录] check_sig解析异常: type={type(e).__name__}, repr={repr(e)}")
    return current_url


def _extract_url_from_body(body: str) -> str:
    """从响应正文中提取跳转 URL。"""
    text = (body or "").replace("\\/", "/").replace("\\x26", "&")
    patterns = [
        r"ptuiCB\('[^']*','[^']*','([^']+)'",
        r"ptui_auth_CB\('[^']*','[^']*','([^']+)'",
        r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        r"location\.replace\(\s*['\"]([^'\"]+)['\"]\s*\)",
        r"window\.location\s*=\s*['\"]([^'\"]+)['\"]",
        r"(auth://tauth\.qq\.com/[^\s\"'<>]+)",
        r"(https?://imgcache\.qq\.com/[^\s\"'<>]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def normalize_url(value: str, default: str = "") -> str:
    url = (value or default or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = f"https://{url.lstrip('/')}"
    return url


def build_login_url(callback_url: str) -> str:
    """用配置里的回调地址替换模板中的 s_url。"""
    encoded_callback = urllib.parse.quote(callback_url, safe="")
    if "s_url=" not in LOGIN_URL_TEMPLATE:
        return LOGIN_URL_TEMPLATE
    return re.sub(
        r"([?&])s_url=[^&]*",
        lambda m: f"{m.group(1)}s_url={encoded_callback}",
        LOGIN_URL_TEMPLATE,
        count=1,
    )


async def generate_qr_code(
    callback_url: str = DEFAULT_LOGIN_CALLBACK_URL,
    u1_url: str = DEFAULT_LOGIN_U1_URL,
) -> Optional[Dict[str, Any]]:
    """生成 QQ 登录二维码。

    成功返回包含 ``session`` / ``qr_bytes`` / ``ptqrtoken`` 等轮询所需
    上下文的字典；失败返回 None（session 已关闭）。
    """
    logger.info("[HTTP登录] 开始生成二维码")

    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    callback_url = normalize_url(callback_url, DEFAULT_LOGIN_CALLBACK_URL)
    u1_url = normalize_url(u1_url, DEFAULT_LOGIN_U1_URL)
    login_url = build_login_url(callback_url)
    logger.info(f"[HTTP登录] 使用回调参数: s_url={callback_url}, u1={u1_url}")

    headers = {
        "User-Agent": _XLOGIN_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://openmobile.qq.com/",
        "X-Requested-With": "com.tencent.apps.valorant",
        "Cookie": "accountType=5; clientType=9",
    }

    try:
        async with session.get(login_url, headers=headers) as response:
            response.raise_for_status()
            login_page = await response.text(errors="ignore")
            logger.info(f"[HTTP登录] xlogin status={response.status}, len={len(login_page)}")

        login_sig = ""
        login_sig_match = re.search(r'g_login_sig=encodeURIComponent\("([^"]+)"\)', login_page)
        if login_sig_match:
            login_sig = login_sig_match.group(1)
        if not login_sig:
            login_sig = _get_cookie_value(session, "https://xui.ptlogin2.qq.com", "pt_login_sig")
        if not login_sig:
            login_sig = _get_cookie_value(session, "https://ssl.ptlogin2.qq.com", "pt_login_sig")
        logger.info(f"[HTTP登录] login_sig={'已获取' if login_sig else '未获取'}")
        parsed_login_url = urllib.parse.urlparse(login_url)
        login_query_map = urllib.parse.parse_qs(parsed_login_url.query, keep_blank_values=True)
        login_s_url = login_query_map.get("s_url", [callback_url])[0] or callback_url
        login_u1 = u1_url
        if login_s_url != login_u1:
            logger.info(f"[HTTP登录] 检测到 s_url 与 u1 不一致: s_url={login_s_url}, u1={login_u1}")
        pt_uistyle = login_query_map.get("style", ["35"])[0] or "35"
        ptlang = login_query_map.get("ptlang", ["2052"])[0] or "2052"
        jsver = _extract_jsver_from_login_page(login_page)
        pt_openlogin_data = _build_pt_openlogin_data(login_url, session)
        aegis_uid = _build_aegis_uid(session)

        qr_params = {
            "s": "8",
            "e": "0",
            "appid": PTQR_AID,
            "type": "0",
            "t": str(random.random()),
            "u1": login_u1,
            "daid": PTQR_DAID,
            "pt_3rd_aid": PTQR_THIRD_AID,
        }
        qr_headers = {
            "User-Agent": headers["User-Agent"],
            "Referer": login_url,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "X-Requested-With": "com.tencent.apps.valorant",
        }
        logger.info(f"[HTTP登录] ptqrshow params={qr_params}")
        async with session.get(PTQR_SHOW_URL, params=qr_params, headers=qr_headers) as response:
            response.raise_for_status()
            qr_image_bytes = await response.read()
            logger.info(f"[HTTP登录] ptqrshow status={response.status}, bytes={len(qr_image_bytes)}")

        if not qr_image_bytes:
            raise RuntimeError("二维码内容为空")

        qrsig = _get_cookie_value(session, "https://xui.ptlogin2.qq.com", "qrsig")
        if not qrsig:
            qrsig = _get_cookie_value(session, "https://ssl.ptlogin2.qq.com", "qrsig")
        if not qrsig:
            raise RuntimeError("未获取到qrsig")

        logger.info("[HTTP登录] 二维码生成成功")
        return {
            "session": session,
            "qr_bytes": qr_image_bytes,
            "ptqrtoken": _calc_ptqrtoken(qrsig),
            "login_sig": login_sig,
            "login_url": login_url,
            "u1_url": login_u1,
            "callback_url": callback_url,
            "pt_openlogin_data": pt_openlogin_data,
            "aegis_uid": aegis_uid,
            "jsver": jsver,
            "pt_uistyle": pt_uistyle,
            "ptlang": ptlang,
        }

    except Exception as e:
        logger.warning(f"[HTTP登录] 生成二维码失败: type={type(e).__name__}, repr={repr(e)}")
        await session.close()
        return None


async def wait_for_login_result(
    session: aiohttp.ClientSession,
    ptqrtoken: int,
    login_sig: str,
    login_u1: str,
    referer_url: str,
    pt_openlogin_data: str = "",
    aegis_uid: str = "",
    jsver: str = "28d22679",
    pt_uistyle: str = "35",
    ptlang: str = "2052",
    timeout: int = 30,
) -> Optional[Dict[str, Any]]:
    """轮询二维码扫描结果，成功返回含 openid/access_token 的字典。"""
    logger.info(f"[HTTP登录] 开始轮询: ptqrtoken={ptqrtoken}, u1={login_u1}")
    poll_headers = {
        "User-Agent": EMULATOR_UA,
        "Referer": referer_url,
        "Accept": "*/*",
        "X-Requested-With": "com.tencent.apps.valorant",
    }

    start_time = time.time()
    poll_index = 0
    while time.time() - start_time < timeout:
        poll_index += 1
        try:
            params = {
                "u1": login_u1,
                "from_ui": "1",
                "type": "1",
                "ptlang": str(ptlang or "2052"),
                "ptqrtoken": str(ptqrtoken),
                "daid": PTQR_DAID,
                "aid": PTQR_AID,
                "pt_3rd_aid": PTQR_THIRD_AID,
                "pt_openlogin_data": pt_openlogin_data,
                "device": "2",
                "ptopt": "1",
                "pt_uistyle": str(pt_uistyle or "35"),
                "jsver": str(jsver or "28d22679"),
                "r": str(random.random()),
            }
            if login_sig:
                params["login_sig"] = login_sig
            if aegis_uid:
                params["aegis_uid"] = aegis_uid

            async with session.get(PTQR_LOGIN_URL, params=params, headers=poll_headers) as response:
                response.raise_for_status()
                text = await response.text(errors="ignore")
                logger.info(f"[HTTP登录] 轮询#{poll_index} status={response.status}")

            callback = _parse_ptui_callback(text)
            if not callback:
                logger.warning("[HTTP登录] 无法解析ptui回调")
                await asyncio.sleep(2)
                continue

            code = callback["code"]
            message = callback["message"]
            redirect_url = callback.get("redirect_url", "")
            logger.info(f"[HTTP登录] 轮询#{poll_index} code={code}, message={message}")

            if code == "0":
                success_url = redirect_url
                logger.info(
                    f"[HTTP登录] 登录成功回调: {_url_location(success_url)}"
                )

                login_data = _extract_login_data_from_success_url(success_url)
                if not (login_data.get("openid") and login_data.get("access_token")):
                    resolved_url = await _resolve_login_success_url(
                        session=session,
                        success_url=success_url,
                        referer_url=referer_url,
                    )
                    if resolved_url and resolved_url != success_url:
                        resolved_data = _extract_login_data_from_success_url(resolved_url)
                        login_data = _merge_login_data(login_data, resolved_data)

                    candidate_url = resolved_url if resolved_url else success_url
                    key_candidates = _collect_redirect_key_candidates(
                        session=session,
                        login_data=login_data,
                        success_url=candidate_url,
                    )
                    for keystr, source in key_candidates:
                        auth_url = await _fetch_auth_url_by_redirect_key(session, keystr)
                        if not auth_url:
                            continue
                        auth_data = _extract_login_data_from_success_url(auth_url)
                        login_data = _merge_login_data(login_data, auth_data)
                        if login_data.get("openid") and login_data.get("access_token"):
                            logger.info(f"[HTTP登录] m_get_redirect_url成功补齐token, source={source}")
                            break

                if login_data.get("openid") and login_data.get("access_token"):
                    logger.info("[HTTP登录] HTTP登录成功，已拿到openid/access_token")
                    return login_data

                logger.error(
                    "[HTTP登录] 登录成功但缺少openid/access_token，"
                    f"keys={sorted(login_data.get('full_params', {}).keys())}"
                )
                return None

            if code == "65":
                logger.warning(f"[HTTP登录] 二维码已失效: {message}")
                return None

            if code in ("66", "67"):
                await asyncio.sleep(2)
                continue

            logger.warning(f"[HTTP登录] 登录状态异常: code={code}, message={message}")
            await asyncio.sleep(2)

        except Exception as e:
            logger.warning(f"[HTTP登录] 轮询异常: type={type(e).__name__}, repr={repr(e)}")
            await asyncio.sleep(2)

    logger.warning("[HTTP登录] 轮询超时")
    return None


async def get_final_cookies(login_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """用 openid/access_token 找掌上无畏契约换最终的 userId/tid。"""
    logger.info("正在获取最终Cookie...")

    openid = login_data.get("openid", "")
    access_token = login_data.get("access_token", "")
    if not openid or not access_token:
        logger.error("缺少必要参数 openid 或 access_token")
        return None

    data = {
        "clienttype": 9,
        "config_params": {"client_dev_name": "23117RK66C", "lang_type": 0},
        "login_info": {
            "appid": 102061775,
            "openid": openid,
            "qq_info_type": 5,
            "sig": access_token,
            "uin": 0,
        },
        "mappid": 10200,
        "mcode": "132f0a77d34402abc8463d60100011d19b0e",
        "source_game_zone": "agame",
        "game_zone": "agame",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(QQ_LOGIN_BY_QQ_URL, headers=QQ_HEADERS_TEMPLATE, json=data) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)

                if result.get("result") == 0:
                    login_info = result.get("data", {}).get("login_info", {})
                    user_id = login_info.get("user_id", "")
                    wt = login_info.get("wt", "")
                    logger.info("成功获取最终Cookie")
                    return {
                        "userId": user_id,
                        "tid": wt,
                        "openid": openid,
                        "uin": login_info.get("uin", 0),
                        "access_token": access_token,
                    }
                logger.error(f"获取最终Cookie失败: {result.get('msg', '未知错误')}")
                return None
    except Exception as e:
        logger.error(f"获取最终Cookie时出错: {e}")
        return None
