import time
from typing import Any, Dict, Optional, Tuple

import aiohttp
from nonebot.log import logger

from ..const import AUTH_INVALID_CODES, STORE_API_URL, STORE_API_UA


def build_store_api_headers(user_config: Dict[str, Any]) -> Dict[str, str]:
    """构造商店接口请求头（userId / tid 来自绑定时的登录态）。"""
    return {
        "Accept": "*/*",
        "Upload-Draft-Interop-Version": "5",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json",
        "User-Agent": STORE_API_UA,
        "Connection": "keep-alive",
        "Upload-Complete": "?1",
        "GH-HEADER": "1-2-105-160-0",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Cookie": (
            "clientType=9; "
            "uin=o0; "
            "appid=102061775; "
            "acctype=qc; "
            "openid=null; "
            "access_token=null; "
            f"userId={user_config['userId']}; "
            "accountType=5; "
            f"tid={user_config['tid']}"
        ),
    }


def get_error_message(response_data: Dict[str, Any]) -> str:
    return response_data.get("errMsg") or response_data.get("msg") or "未知错误"


def is_auth_invalid(result_code: Any, err_msg: str) -> bool:
    """判断是否属于登录凭证失效（此时应提示用户重新绑定）。"""
    err_msg_lower = (err_msg or "").lower()
    return (
        result_code in AUTH_INVALID_CODES
        or "ticket expire" in err_msg_lower
        or "auth web ticket fail" in err_msg_lower
    )


async def request_store_api(
    user_id: str,
    user_config: Dict[str, Any],
    max_retries: int = 3,
    timeout: int = 15,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
    """请求商店接口。

    返回 ``(响应体, 错误信息, 是否凭证失效)``，三者只有一个分支有效。
    """
    logger.info(f"开始请求用户 {user_id} 的商店接口")

    if not all(user_config.get(k) for k in ("userId", "tid")):
        err_msg = "配置不完整，需要包含 userId 和 tid"
        logger.error(err_msg)
        return None, err_msg, False

    headers = build_store_api_headers(user_config)

    for attempt in range(max_retries):
        timestamp = int(time.time())
        payload = {"_t": timestamp}
        try:
            logger.info(
                f"发送API请求到 {STORE_API_URL} (尝试 {attempt + 1}/{max_retries}), 时间戳: {timestamp}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    STORE_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    response.raise_for_status()
                    response_data = await response.json(content_type=None)

                    result_code = response_data.get("result")
                    if result_code != 0:
                        err_msg = get_error_message(response_data)
                        auth_invalid = is_auth_invalid(result_code, err_msg)
                        log = logger.warning if auth_invalid else logger.error
                        log(f"API请求失败，错误码: {result_code}，错误信息: {err_msg}")
                        return None, err_msg, auth_invalid

                    return response_data, None, False

        except aiohttp.ClientError as e:
            logger.error(f"网络请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                continue
            return None, "请求商店接口失败，请稍后重试", False
        except Exception as e:
            logger.error(f"处理失败 (尝试 {attempt + 1}/{max_retries}): {e}", exc_info=True)
            if attempt < max_retries - 1:
                continue
            return None, "处理商店数据时出错，请稍后重试", False

    logger.error(f"API请求失败，已达到最大重试次数 {max_retries}")
    return None, "请求商店接口失败，请稍后重试", False


def extract_goods_list(
    response_data: Dict[str, Any],
) -> Tuple[Optional[list], Optional[str]]:
    """从商店接口响应中拆出商品列表。"""
    if "data" not in response_data:
        logger.error("API返回数据格式不正确，缺少'data'字段")
        return None, "商店接口返回格式异常，请稍后重试"

    if not response_data["data"]:
        logger.info("API返回数据为空")
        return [], None

    data = response_data["data"]
    if isinstance(data, list):
        data = data[0] if data else {}

    if not isinstance(data, dict):
        logger.error("API返回数据格式不正确，data 不是对象")
        return None, "商店接口返回格式异常，请稍后重试"

    goods_list = data.get("list", [])
    if not goods_list:
        logger.info("今日商店没有商品")
        return [], None

    logger.info(f"获取到 {len(goods_list)} 个商品")
    return goods_list, None


async def get_shop_items_raw(
    user_id: str, user_config: Dict[str, Any]
) -> Optional[list]:
    """拿原始商品列表；失败（含凭证失效）一律返回 None。"""
    response_data, err_msg, auth_invalid = await request_store_api(user_id, user_config)
    if not response_data:
        if auth_invalid:
            logger.warning(f"用户 {user_id} 登录凭证已失效: {err_msg}")
        elif err_msg:
            logger.error(f"获取商店原始数据失败: {err_msg}")
        return None

    goods_list, parse_err = extract_goods_list(response_data)
    if parse_err:
        logger.error(f"解析商店原始数据失败: {parse_err}")
        return None
    return goods_list or None


async def test_config_validity(user_id: str, user_config: Dict[str, Any]) -> bool:
    """单次请求探测当前凭证是否仍然有效。"""
    logger.info(f"测试用户配置有效性，user_id: {user_id}")
    try:
        response_data, err_msg, _ = await request_store_api(
            user_id, user_config, max_retries=1, timeout=10
        )
        if response_data:
            logger.info("用户配置有效")
            return True
        logger.warning(f"用户配置无效: {err_msg or '未知错误'}")
        return False
    except Exception as e:
        logger.error(f"测试配置有效性时出错: {e}")
        return False


async def download_image(url: str, user_id: str, filename: str) -> Optional[str]:
    """下载商品相关图片到用户临时目录，成功返回路径。"""
    from ..paths import temp_user_file

    try:
        filepath = temp_user_file(user_id, filename)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response.raise_for_status()
                content = await response.read()
                with open(filepath, "wb") as file:
                    file.write(content)
                return str(filepath)
    except ValueError as e:
        logger.error(f"构建临时文件路径失败: {e}")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"下载图片失败: {e}")
        return None
