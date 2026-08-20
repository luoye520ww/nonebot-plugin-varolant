<div align="center">

# nonebot-plugin-varolant

_面向 QQ 群的 NoneBot2 无畏契约助手_

[![PyPI](https://img.shields.io/pypi/v/nonebot-plugin-varolant.svg)](https://pypi.org/project/nonebot-plugin-varolant/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![NoneBot2](https://img.shields.io/badge/NoneBot2-2.2.0%2B-d85f5f.svg)](https://nonebot.dev/)
[![OneBot V11](https://img.shields.io/badge/adapter-OneBot_V11-black.svg)](https://onebot.dev/)
[![Server](https://img.shields.io/badge/无畏契约-仅支持国服-e44c5c.svg)](#功能)
[![License](https://img.shields.io/github/license/luoye520ww/nonebot-plugin-varolant.svg)](LICENSE)

每日商店 · 双方 10 人详情 · 武器数据 · 战绩卡片 · QQ 群开关

</div>

## 功能

`nonebot-plugin-varolant` 是一个适用于 QQ 群和私聊的 NoneBot2 国服无畏契约插件。战绩功能使用 WeGame 微信扫码登录；每日商店使用独立的掌瓦 App 登录，两套登录态互不影响。

- `瓦登录` 默认登录 WeGame，获取更完整的国服战绩数据
- `瓦app登录` 登录掌瓦 App，仅供每日商店与商店监控
- 每日商店支持一个 QQ 绑定多个游戏账号并随时切换
- 查询自己或已绑定群友的每日商店
- 监控指定皮肤，上架后定时私聊提醒
- 查询近期战绩、当前/最近单场双方 10 人、赛季战报、地图、英雄池、开黑和武器击杀
- Pillow 绘制中文卡片，使用腾讯资源接口补齐特工头像、地图预览与武器素材，资源获取失败时自动降级
- 群聊默认关闭，由群主、管理员或超级用户按群启用

> [!IMPORTANT]
> 插件当前仅支持 **国服无畏契约** 与 **OneBot V11**，需要 Python 3.10 及以上版本。群聊默认不响应业务命令，安装后请先由管理员发送 `/s 开启瓦`。

## 效果图

<details>
<summary>查看帮助图</summary>

![帮助图](https://raw.githubusercontent.com/luoye520ww/nonebot-plugin-varolant/main/docs/images/help.jpg)

</details>

<details>
<summary>查看双方 10 人单场详情</summary>

![双方 10 人详情](https://raw.githubusercontent.com/luoye520ww/nonebot-plugin-varolant/main/docs/images/battle-detail.jpg)

</details>

<details>
<summary>查看武器击杀卡片</summary>

![武器击杀](https://raw.githubusercontent.com/luoye520ww/nonebot-plugin-varolant/main/docs/images/weapons.jpg)

</details>

<details>
<summary>查看近期战绩卡片</summary>

![近期战绩](https://raw.githubusercontent.com/luoye520ww/nonebot-plugin-varolant/main/docs/images/stats.jpg)

</details>

## 安装

### 使用 nb-cli

```bash
nb plugin install nonebot-plugin-varolant
```

### 使用 pip

```bash
pip install nonebot-plugin-varolant
```

安装后确认宿主项目加载了 `nonebot_plugin_varolant`。使用 nb-cli 安装时会自动写入插件加载配置；手动安装可在宿主项目的 `pyproject.toml` 中加入：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_varolant"]
```

### 使用源码

```bash
git clone https://github.com/luoye520ww/nonebot-plugin-varolant.git
cd nonebot-plugin-varolant
pip install -e .
```

## 配置

插件无需添加额外 `.env` 配置，安装并加载后即可使用。每日监控时间为北京时间
`08:01`；时区、通知机器人及登录回调均由插件自动处理。

## 指令

所有“瓦”前缀指令均可将“瓦”替换为“无畏契约”，例如 `无畏契约 战报`。指令无需 `/` 前缀。

### 账号

| 指令 | 说明 |
| --- | --- |
| `瓦登录` | 使用微信扫码登录 WeGame，供全部战绩功能使用 |
| `瓦登录 清除` | 仅清除 WeGame 战绩登录态 |
| `瓦app登录` | 按默认方式登录掌瓦 App，仅供每日商店使用 |
| `瓦app登录 qq` / `瓦app登录 wx` | 指定 QQ 或微信登录掌瓦 App |
| `瓦app登录 清除` | 清除全部掌瓦 App 商店账号 |
| `瓦 账号` | 查看掌瓦 App 每日商店账号列表 |
| `瓦 切换账号 <序号或昵称>` | 切换每日商店账号 |
| `瓦 删除账号 <序号或昵称>` | 删除指定每日商店账号 |
| `瓦 帮助` / `瓦登录 帮助` | 查看完整帮助图 |

### 每日商店与监控

| 指令 | 说明 |
| --- | --- |
| `每日商店` | 查看自己的今日商店 |
| `每日商店 @某人` | 查看已绑定群友的今日商店 |
| `商店监控 添加 "皮肤 武器"` | 添加监控项 |
| `商店监控 删除 "皮肤 武器"` | 删除监控项 |
| `商店监控 列表` | 查看全部监控项 |
| `商店监控 查询` | 立即查询一次 |
| `商店监控 开启` / `商店监控 关闭` | 开关每日自动监控 |

### 战绩

| 指令 | 说明 |
| --- | --- |
| `瓦 查战绩` / `瓦 战绩` | 查看自己的近期战绩 |
| `瓦 查战绩 <昵称#ID>` | 从本人近期 10 局双方名单定位；完整历史受限时展示可访问的共同对局 |
| `瓦 队友` / `瓦 队友战绩` | 优先获取当前对局，展示双方 10 人、逐回合结果、换边、对局时间与单场数据；未识别到则展示最近一局 |
| `瓦 战报` | 当前赛季 KDA、胜率、ACS、KAST 和常用特工 |
| `瓦 地图` | 近期 50 场地图胜率、KDA、ACS 和常用特工 |
| `瓦 英雄池` / `瓦 英雄` | 近期 50 场特工聚合 TOP8 |
| `瓦 开黑` | 近期组队队友排行 |
| `瓦 击杀` | 总赛季各武器击杀、场均击杀、爆头率、最远击杀与总伤害 |

### QQ 群开关

以下指令仅限群主、群管理员或 NoneBot 超级用户：

| 指令 | 说明 |
| --- | --- |
| `/s 开启瓦` | 在当前群启用插件 |
| `/s 关闭瓦` | 在当前群停用插件 |
| `/s 瓦` | 查看当前群开关状态 |

`开启/关闭` 也可写作 `启用/停用/on/off`，插件名也可写作 `无畏契约/valorant/val`。私聊始终可用。

插件的交互消息均会引用回复触发该指令的消息，便于在群聊中对应查询结果。

## 数据与隐私

- 账号数据保存在插件包内的 `data/<QQ号>.json`，写入时使用临时文件原子替换。
- 群白名单保存在 `data/switch.json`。
- 二维码、特工头像、地图预览和武器素材缓存在 `cache/`，可在机器人停止后清理并自动重建。
- WeGame 登录态仅用于战绩接口；掌瓦 App 登录态仅用于每日商店接口。
- WeGame 登录会保存腾讯登录响应中的刷新凭证，并在使用时及后台每 20 分钟自动续期。升级后需要重新扫码一次，让旧登录态补齐刷新凭证；机器人连续离线超过刷新凭证有效期时仍需重新登录。
- 插件不会在日志中输出 access token、tgp_ticket 或登录回调参数。
- 战绩登录失效后重新发送 `瓦登录`；每日商店登录失效后重新发送 `瓦app登录`。

> [!NOTE]
> WeGame 战绩接口使用内部 Subject 查询玩家，当前未发现官方公开的“昵称#ID → Subject”接口。因此“查别人战绩”会从本人近期 10 局的双方名单定位；`瓦队友` 的当前对局识别属于实战探测路径，开局数据是否即时同步需要以机器人实际回包为准。

## 常见问题

### 群里发送指令没有反应

群聊默认关闭。请确认群主、管理员或超级用户已经发送 `/s 开启瓦`，并确认 OneBot V11 协议端与机器人连接正常。

### 每日监控没有推送

确认已经添加监控项并发送 `商店监控 开启`。插件会自动选择当前在线机器人发送私聊通知。

### 图片中的官方素材没有显示

插件会从腾讯静态资源地址获取特工头像、武器图标和地图预览图。网络不可用时会自动使用色块兜底，不影响指令主体功能。

## 更新说明

### v1.0.0

首个公开发行版本：

- WeGame 微信扫码登录；掌瓦 App 商店登录与多账号管理
- 每日商店查询和皮肤上架监控
- 近期战绩、双方 10 人单场详情、战报、地图、英雄池、开黑和武器击杀卡片
- QQ 群白名单开关及管理权限检查
- 完整中文帮助图、配置模板和 NoneBot2 插件元数据

## 免责声明

本项目为非官方社区工具，与 Riot Games、腾讯、WeGame 无从属或合作关系。

项目仅用于学习、研究和个人数据展示。请遵守游戏、平台和相关服务条款，不要用于骚扰、作弊、绕过访问控制或侵犯他人隐私。

本项目只展示官方接口返回的公开资料和公开 IP 属地，不提取、不展示玩家真实 IP。

## License

本项目使用 [MIT License](LICENSE) 开源。
