"""插件用到的固定常量。

这里的 URL、appid、UA 等均来自掌上无畏契约 App 与 QQ / 微信
开放平台的公开登录链路，属于协议字段，不建议随意改动。
"""

from pathlib import Path

# 插件自带字体（用于绘制每日商店 / 帮助图片）
FONT_PATH = Path(__file__).parent / "fontFamily.ttf"

# ---------------------------------------------------------------------------
# QQ 扫码登录（ptlogin2 协议）
# ---------------------------------------------------------------------------

# xlogin 页面地址，末尾的 time / h5sig 等参数仅影响页面内部行为，
# 扫码链路主要依赖其中的 s_url / client_id / sign 等字段。
LOGIN_URL_TEMPLATE = (
    "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?pt_enable_pwd=1&appid=716027609"
    "&pt_3rd_aid=102061775&daid=381&pt_skey_valid=0&style=35&force_qr=1"
    "&autorefresh=1&s_url=http%3A%2F%2Fconnect.qq.com&refer_cgi=m_authorize"
    "&ucheck=1&fall_to_wv=1&status_os=12&redirect_uri=auth%3A%2F%2Ftauth.qq.com%2F"
    "&client_id=102061775&pf=openmobile_android&response_type=token&scope=all"
    "&sdkp=a&sdkv=3.5.17.lite&sign=a6479455d3e49b597350f13f776a6288"
    "&status_machine=MjMxMTdSSzY2Qw%3D%3D&switch=1&time=1763280194"
    "&show_download_ui=true&h5sig=trobryxo8IPM0GaSQH12mowKG-CY65brFzkK7_-9EW4&loginty=6"
)

PTQR_SHOW_URL = "https://xui.ptlogin2.qq.com/ssl/ptqrshow"
PTQR_LOGIN_URL = "https://xui.ptlogin2.qq.com/ssl/ptqrlogin"
OPENMOBILE_REDIRECT_URL = "https://openmobile.qq.com/oauth2.0/m_get_redirect_url"

PTQR_AID = "716027609"
PTQR_DAID = "381"
PTQR_THIRD_AID = "102061775"

DEFAULT_LOGIN_CALLBACK_URL = "http://connect.qq.com"
DEFAULT_LOGIN_U1_URL = "http://connect.qq.com"

# 掌上无畏契约登录接口
QQ_LOGIN_BY_QQ_URL = (
    "https://app.mval.qq.com/go/auth/login_by_qq"
    "?source_game_zone=agame&game_zone=agame"
)

QQ_HEADERS_TEMPLATE = {
    "Cookie": "clientType=9; openid=null; access_token=null;",
    "User-Agent": (
        "mval/2.4.0.10053 Channel/10068 Manufacturer/Redmi  "
        "Mozilla/5.0 (Linux; Android 12; 23117RK66C Build/V417IR; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/101.0.4951.61 Mobile Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Host": "app.mval.qq.com",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
}

# 二维码扫描阶段使用的模拟器 UA
EMULATOR_UA = (
    "Mozilla/5.0 (Linux; Android 12; 23117RK66C Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/101.0.4951.61 Mobile Safari/537.36 tencent_game_emulator"
)

# ---------------------------------------------------------------------------
# 微信扫码登录
# ---------------------------------------------------------------------------

WECHAT_QRCONNECT_URL = "https://open.weixin.qq.com/connect/sdk/qrconnect"
WECHAT_LONG_POLL_URL = "https://long.open.weixin.qq.com/connect/l/qrconnect"
WECHAT_APP_ID = "wxcbb49f1f39656c2a"  # 掌上无畏契约 appid

WECHAT_TICKET_URL = "https://app.mval.qq.com/go/auth/get_sdk_ticket"
WECHAT_LOGIN_URL = "https://app.mval.qq.com/go/auth/login_by_wechat"

WECHAT_TICKET_UA = (
    "mval/2.10062 Channel/3 Manufacturer/Redmi  "
    "Mozilla/5.0 (Linux; Android 12; 22041216C Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/110.0.5481.154 Mobile Safari/537.36"
)

WECHAT_POLL_UA = (
    "mval/2.10053 Channel/10068 Manufacturer/Redmi "
    "Mozilla/5.0 (Linux; Android 12; 23117RK66C Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/101.0.4951.61 Mobile Safari/537.36"
)

# ---------------------------------------------------------------------------
# 每日商店接口
# ---------------------------------------------------------------------------

STORE_API_URL = "https://app.mval.qq.com/go/mlol_store/agame/user_store"

STORE_API_UA = (
    "mval/2.3.0.10050 Channel/5 Manufacturer/Xiaomi  "
    "Mozilla/5.0 (Linux; Android 14; 23078RKD5C Build/UP1A.230905.011; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/140.0.7339.207 Mobile Safari/537.36"
)

# 登录态失效的错误码
AUTH_INVALID_CODES = {1001, 1003, 999999}
