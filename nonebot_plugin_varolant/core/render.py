import asyncio
import hashlib
import io
import math
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp
from nonebot.log import logger
from PIL import Image as PILImage, ImageDraw, ImageFont

from ..const import FONT_PATH
from ..paths import cache_dir, temp_user_dir, temp_user_file
from . import store as store_api

GOODS_PIC_HEIGHT = 180
FONT_SIZE = 36
MERGE_GAP = 20


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except (IOError, OSError):
        logger.warning("字体加载失败，改用默认字体")
        return ImageFont.load_default()


def _render_single_goods(goods: Dict[str, Any], bg_path: str, goods_path: str) -> PILImage.Image:
    """背景图 + 商品图 + 名称/价格，合成一张商品卡片。"""
    img1 = PILImage.open(bg_path)
    img2 = PILImage.open(goods_path)

    height = GOODS_PIC_HEIGHT
    width = int((img2.width * height) / img2.height)
    img2_resized = img2.resize((width, height))

    x = (img1.width - img2_resized.width) // 2
    y = (img1.height - img2_resized.height) // 2

    new_img = PILImage.new("RGB", img1.size)
    new_img.paste(img1, (0, 0))
    if img2_resized.mode in ("RGBA", "LA"):
        new_img.paste(img2_resized, (x, y), mask=img2_resized)
    else:
        new_img.paste(img2_resized, (x, y))

    draw = ImageDraw.Draw(new_img)
    font = _load_font(FONT_SIZE)
    text_color = (255, 255, 255)

    text = goods["goods_name"]
    text_position = (36, new_img.height - 50)
    draw.text(text_position, text, fill=text_color, font=font)

    price = goods.get("rmb_price", "0")
    price_bbox = draw.textbbox((0, 0), price, font=font)
    price_width = price_bbox[2] - price_bbox[0]
    price_position = (new_img.width - price_width - 36, new_img.height - 50)
    draw.text(price_position, price, fill=text_color, font=font)

    return new_img


def _merge_vertical(images: List[PILImage.Image], gap: int = MERGE_GAP) -> PILImage.Image:
    max_width = max(img.width for img in images)
    total_height = sum(img.height for img in images) + (len(images) - 1) * gap
    merged = PILImage.new("RGB", (max_width, total_height), color="white")

    y_offset = 0
    for img in images:
        merged.paste(img, (0, y_offset))
        y_offset += img.height + gap
    return merged


async def build_shop_image(
    user_id: str,
    user_config: Dict[str, Any],
    goods_list: Optional[list] = None,
) -> Optional[bytes]:
    """拉取（或直接消费）今日商品列表，合成一张竖排 JPG，返回字节流。"""
    from .store import download_image, get_shop_items_raw

    logger.info(
        f"开始获取商店数据，user_id: {user_id}, userId: {user_config.get('userId', '未知')}"
    )
    try:
        user_temp_dir = temp_user_dir(user_id)
        user_temp_dir.mkdir(parents=True, exist_ok=True)
    except ValueError as e:
        logger.error(f"构建用户临时目录失败: {e}")
        return None

    if goods_list is None:
        goods_list = await get_shop_items_raw(user_id, user_config)
    if not goods_list:
        return None

    processed_images: List[str] = []

    for i, goods in enumerate(goods_list):
        logger.info(f"处理商品 {i + 1}/{len(goods_list)}: {goods['goods_name']}")

        bg_img_url = goods.get("bg_image")
        goods_img_url = goods.get("goods_pic")
        if not bg_img_url or not goods_img_url:
            logger.error("商品缺少图片URL")
            continue

        bg_img_path, goods_img_path = await asyncio.gather(
            download_image(bg_img_url, user_id, f"bg_{i}.jpg"),
            download_image(goods_img_url, user_id, f"goods_{i}.jpg"),
        )
        if not bg_img_path or not goods_img_path:
            logger.error("图片下载失败，跳过该商品")
            continue

        try:
            new_img = _render_single_goods(goods, bg_img_path, goods_img_path)

            file_stem = re.sub(
                r"[^0-9A-Za-z_-]", "_", str(goods.get("goods_id", f"goods_{i + 1}"))
            ).strip("_")
            if not file_stem:
                file_stem = f"goods_{i + 1}"
            processed_path = temp_user_file(user_id, f"{file_stem}.jpg")
            new_img.save(processed_path)
            processed_images.append(str(processed_path))
            logger.info(f"商品 {goods['goods_name']} 处理完成")
        except Exception as e:
            logger.error(f"图片处理失败: {e}")
        finally:
            for path in (bg_img_path, goods_img_path):
                if path and os.path.exists(path):
                    os.remove(path)

    if not processed_images:
        logger.error("没有商品图片处理成功")
        return None

    logger.info(f"成功处理 {len(processed_images)} 张图片，开始合并")
    images = [PILImage.open(p) for p in processed_images]
    merged = _merge_vertical(images)

    buffer = io.BytesIO()
    merged.save(buffer, format="JPEG")
    image_bytes = buffer.getvalue()

    for img in images:
        img.close()
    merged.close()

    try:
        if user_temp_dir.exists():
            shutil.rmtree(user_temp_dir)
            logger.info(f"清理临时目录: {user_temp_dir}")
    except Exception as e:
        logger.warning(f"清理临时目录失败: {e}")

    logger.info(f"商店图片生成完成，大小: {len(image_bytes)} 字节")
    return image_bytes

_BG = (15, 25, 35)
_CARD = (255, 255, 255)
_TITLE = (31, 41, 55)
_TEXT = (55, 65, 81)
_MUTED = (107, 114, 128)
_ACCENT = (70, 95, 255)
_ACCENT_SOFT = (232, 236, 255)
_LINE = (229, 231, 235)


def _rounded_card(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int, int, int],
    radius: int = 18,
    fill: Tuple[int, int, int] = _CARD,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def build_help_image(default_login_mode: str, monitor_time: str, timezone: str) -> bytes:
    """生成帮助页图片（所有命令一览）。返回 JPG 字节流。

    左右双栏布局：左栏 A/B/C，右栏 D/E/F，避免单列长图。
    """
    width = 1240
    margin = 40
    col_gap = 28
    col_w = (width - margin * 2 - col_gap) // 2
    card_pad = 24
    inner_w = col_w - card_pad * 2
    col_v_gap = 22

    left_sections: List[Tuple[str, str, List[Tuple[str, str]]]] = [
        (
            "账号绑定与切换",
            "A",
            [
                ("瓦登录", "微信扫码登录 WeGame（全部战绩功能）"),
                ("瓦登录 清除", "清除 WeGame 战绩登录态"),
                ("瓦app登录 qq / wx", "登录掌瓦 App（仅每日商店）"),
                ("瓦 账号", "查看掌瓦 App 商店账号列表"),
                ("瓦 切换账号 序号或昵称", "切换当前每日商店账号"),
                ("瓦 删除账号 序号或昵称", "删除指定每日商店账号"),
            ],
        ),
        (
            "每日商店",
            "B",
            [
                ("每日商店", "查看自己的今日商店"),
                ("每日商店 @某人", "查看对方的今日商店（需对方已绑定）"),
            ],
        ),
        (
            "商店监控",
            "C",
            [
                ('商店监控 添加 "皮肤 武器"', "添加监控项，例：侦察力量 幻象"),
                ('商店监控 删除 "皮肤 武器"', "删除监控项"),
                ("商店监控 列表", "查看我的监控列表"),
                ("商店监控 查询", "立即执行一次监控查询"),
                ("商店监控 开启 / 关闭", f"每日 {monitor_time} 自动监控并通知"),
            ],
        ),
    ]
    right_sections: List[Tuple[str, str, List[Tuple[str, str]]]] = [
        (
            "战绩查询",
            "D",
            [
                ("瓦 查战绩 / 瓦 战绩", "自己的 WeGame 近期战绩"),
                ("瓦 查战绩 昵称#ID", "查近期同局玩家（按 Subject 定位）"),
                ("瓦 队友", "优先查当前对局；展示双方 10 人单场详情"),
                ("瓦 战报", "当前赛季 KDA/胜率/ACS/精准击败 + 常用特工"),
                ("瓦 地图", "近期 50 场每图胜率/KDA/ACS与常用特工"),
                ("瓦 英雄池", "近期 50 场特工聚合 TOP8"),
                ("瓦 开黑", "基于近期对局的开黑队友排行"),
                ("瓦 击杀", "总赛季各武器击杀/场均/爆头率/最远击杀/伤害"),
            ],
        ),
        (
            "群开关",
            "E",
            [
                ("/s 开启瓦", "本群启用插件（群主/管理员/超管）"),
                ("/s 关闭瓦", "本群停用插件"),
                ("/s 瓦", "查看本群开关状态；群聊默认关，私聊常开"),
            ],
        ),
        (
            "其它",
            "F",
            [
                ("瓦登录 帮助 / 瓦 帮助", "查看本帮助"),
                (f"监控时间：每天 {monitor_time}", f"时区 {timezone}，插件已内置无需配置"),
            ],
        ),
    ]

    title_font = _load_font(44)
    section_font = _load_font(32)
    cmd_font = _load_font(27)
    desc_font = _load_font(24)
    badge_font = _load_font(23)

    tmp = PILImage.new("RGB", (width, 10))
    tmp_draw = ImageDraw.Draw(tmp)

    def text_h(font) -> int:
        bbox = tmp_draw.textbbox((0, 0), "瓦Ag｜", font=font)
        return bbox[3] - bbox[1]

    def text_w_of(text: str, font) -> int:
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def fit_font(text: str, base_size: int):
        """逐步缩字号直到文本不超宽，保证任何内容都不溢出卡片。"""
        size = base_size
        font = _load_font(size)
        while size > 19 and text_w_of(text, font) > inner_w:
            size -= 1
            font = _load_font(size)
        return font

    cmd_h = text_h(cmd_font)
    desc_h = text_h(desc_font)
    row_h = cmd_h + 6 + desc_h + 15
    badge_h = 40
    head_gap = 14

    def section_height(rows: List[Tuple[str, str]]) -> int:
        return card_pad * 2 + badge_h + head_gap + row_h * len(rows) + 4

    def column_height(sections) -> int:
        return (
            sum(section_height(rows) for _, _, rows in sections)
            + col_v_gap * (len(sections) - 1)
        )

    banner_top = 34
    banner_h = 82
    header_h = banner_top + banner_h + 26
    footer_h = 66
    content_h = max(column_height(left_sections), column_height(right_sections))
    height = header_h + content_h + footer_h

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (margin, banner_top, width - margin, banner_top + banner_h),
        radius=20,
        fill=_ACCENT,
    )
    draw.text(
        (margin + 28, banner_top + 16),
        "国服无畏契约BOT助手",
        fill=(255, 255, 255),
        font=title_font,
    )

    def draw_column(x: int, sections) -> None:
        y = header_h
        for title, badge, rows in sections:
            sec_h = section_height(rows)
            _rounded_card(draw, (x, y, x + col_w, y + sec_h))
            bx, by = x + card_pad, y + card_pad
            draw.rounded_rectangle(
                (bx, by, bx + badge_h, by + badge_h), radius=10, fill=_ACCENT_SOFT
            )
            b_bbox = draw.textbbox((0, 0), badge, font=badge_font)
            bw, bh = b_bbox[2] - b_bbox[0], b_bbox[3] - b_bbox[1]
            draw.text(
                (bx + (badge_h - bw) // 2, by + (badge_h - bh) // 2 - b_bbox[1]),
                badge,
                fill=_ACCENT,
                font=badge_font,
            )
            draw.text((bx + badge_h + 16, by + 3), title, fill=_TITLE, font=section_font)

            row_y = by + badge_h + head_gap
            for ri, (cmd, desc) in enumerate(rows):
                draw.text((bx + 4, row_y), cmd, fill=_TITLE, font=fit_font(cmd, 27))
                draw.text(
                    (bx + 4, row_y + cmd_h + 6),
                    desc,
                    fill=_MUTED,
                    font=fit_font(desc, 24),
                )
                row_y += row_h
                if ri < len(rows) - 1:
                    draw.line(
                        (bx, row_y - 8, x + col_w - card_pad, row_y - 8),
                        fill=_LINE,
                        width=1,
                    )
            y += sec_h + col_v_gap

    draw_column(margin, left_sections)
    draw_column(margin + col_w + col_gap, right_sections)

    footer_text = "「瓦」均可用「无畏契约」替代 · 瓦业务命令无需 / 前缀 · by LuoYeBot"
    f_font = fit_font(footer_text, 24)
    f_w = text_w_of(footer_text, f_font)
    draw.text(
        ((width - f_w) // 2, height - footer_h + 20),
        footer_text,
        fill=_MUTED,
        font=f_font,
    )

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()

from datetime import datetime  # noqa: E402

from . import names  # noqa: E402
from .mval import MatchRow, PlayerBrief  # noqa: E402

_WIN = (22, 163, 74)
_WIN_SOFT = (220, 252, 231)
_LOSE = (220, 38, 38)
_LOSE_SOFT = (254, 226, 226)
_GOLD = (202, 138, 4)

_CARD_W = 880
_MARGIN = 40
_NO_ACS_MODES = {"deathmatch", "ggteam", "gungame"}


def _fmt_time(ms: int) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _agg(rows: List[MatchRow]) -> Tuple[float, float, float]:
    """聚合 (KDA, 平均ACS, 胜率)。"""
    total_k = sum(r.kills for r in rows)
    total_d = sum(r.deaths for r in rows)
    total_a = sum(r.assists for r in rows)
    kda = (total_k + total_a) / max(total_d, 1)
    acs_rows = [r.acs for r in rows if r.mode_key.lower() not in _NO_ACS_MODES]
    acs = sum(acs_rows) / max(len(acs_rows), 1)
    wins = sum(1 for r in rows if r.won is True)
    valid = sum(1 for r in rows if r.won is not None)
    win_rate = wins / valid * 100 if valid else 0.0
    return kda, acs, win_rate


def _last_rank(rows: List[MatchRow]) -> Optional[int]:
    for r in rows:
        if r.rank_tier_after is not None:
            return r.rank_tier_after
    return None


def _draw_tip(draw: ImageDraw.ImageDraw, width: int, y: int, text: str,
              font: ImageFont.FreeTypeFont) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, y), text, fill=_MUTED, font=font)


def build_stats_image(player: PlayerBrief, rows: List[MatchRow],
                      agent_icons: Optional[Dict[str, str]] = None,
                      season_summary: Optional[Dict[str, Any]] = None) -> bytes:
    """单个玩家的近期战绩卡片。返回 JPG 字节流。

    agent_icons：{特工中文名: 本地头像路径}，缺图时用首字色块兜底。
    """
    width, margin, top = 960, 24, 22
    header_h, row_h, gap, footer_h = 164, 106, 10, 48
    height = top + header_h + gap + len(rows) * (row_h + gap) + footer_h - gap

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(36)
    main_font = _load_font(27)
    small_font = _load_font(21)
    tag_font = _load_font(20)
    tiny_font = _load_font(17)
    _draw_edge_decor(draw, width, top, height - footer_h + 6)

    draw.rounded_rectangle((margin, top, width - margin, top + 62),
                           radius=16, fill=_ACCENT)
    draw.text((margin + 24, top + 12), player.display,
              fill=(255, 255, 255), font=title_font)
    label = "近期战绩"
    label_w = _text_w(draw, label, small_font)
    draw.text((width - margin - 24 - label_w, top + 20), label,
              fill=(224, 229, 255), font=small_font)
    summary_y = top + 72
    _rounded_card(draw, (margin, summary_y, width - margin, top + header_h), radius=14)
    tier = _last_rank(rows)
    kda, acs, win_rate = _agg(rows)
    if season_summary:
        tier = int(season_summary.get("tier") or 0) or tier
        season_win_rate = float(season_summary.get("winrate") or 0)
        if season_win_rate <= 1:
            season_win_rate *= 100
        metrics = [
            ("赛季场次", str(int(season_summary.get("games") or 0))),
            ("赛季胜率", f"{season_win_rate:.0f}%"),
            ("赛季KDA", f"{float(season_summary.get('kda') or 0):.2f}"),
            ("赛季ACS", f"{float(season_summary.get('acs') or 0):.1f}"),
            ("当前段位", names.tier_name(tier) if tier is not None else "未定级"),
        ]
    else:
        metrics = [
            ("近期场次", str(len(rows))),
            ("近期胜率", f"{win_rate:.0f}%"),
            ("近期KDA", f"{kda:.2f}"),
            ("近期ACS", f"{acs:.1f}"),
            ("当前段位", names.tier_name(tier) if tier is not None else "未定级"),
        ]
    metric_gap = 8
    metric_w = (
        width - 2 * margin - 32 - metric_gap * (len(metrics) - 1)
    ) // len(metrics)
    for index, (metric_label, value) in enumerate(metrics):
        mx = margin + 16 + index * (metric_w + metric_gap)
        fill = _ACCENT_SOFT if index in (0, 2, 3) else (240, 247, 244)
        draw.rounded_rectangle((mx, summary_y + 12, mx + metric_w, summary_y + 68),
                               radius=11, fill=fill)
        draw.text((mx + 12, summary_y + 18), metric_label, fill=_MUTED, font=tiny_font)
        value_w = _text_w(draw, value, small_font)
        draw.text((mx + metric_w - 12 - value_w, summary_y + 34), value,
                  fill=_TITLE, font=small_font)
    y = top + header_h + gap

    for r in rows:
        won_soft, won_txt, won_col = (
            (_WIN_SOFT, "胜利", _WIN) if r.won is True
            else (_LOSE_SOFT, "失败", _LOSE) if r.won is False
            else (_ACCENT_SOFT, "平/未知", _ACCENT)
        )
        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=14)
        draw.polygon(((width - margin - 42, y), (width - margin, y),
                      (width - margin, y + 42)), fill=won_soft)
        draw.rounded_rectangle((margin, y, margin + 10, y + row_h), radius=5, fill=won_col)
        draw.rectangle((margin + 5, y, margin + 10, y + row_h), fill=won_col)
        aname_row = r.agent_name or names.agent_name(r.agent_id)
        icon_path = ((agent_icons or {}).get(r.agent_id)
                     or (agent_icons or {}).get(aname_row))
        ax, ay = margin + 24, y + 25
        if icon_path:
            _paste_square_thumb(img, icon_path, ax, ay, 56, radius=12,
                                bg=(240, 242, 246))
        else:
            draw.rounded_rectangle((ax, ay, ax + 56, ay + 56), radius=12,
                                   fill=(226, 232, 240))
            initial = (aname_row or "?")[:1]
            bbi = draw.textbbox((0, 0), initial, font=main_font)
            draw.text((ax + (56 - (bbi[2] - bbi[0])) // 2 - bbi[0],
                       ay + (56 - (bbi[3] - bbi[1])) // 2 - bbi[1]),
                      initial, fill=_TITLE, font=main_font)
        chip_y = y + 18
        chip = (margin + 96, chip_y, margin + 96 + 68, chip_y + 30)
        draw.rounded_rectangle(chip, radius=15, fill=won_soft)
        bb = draw.textbbox((0, 0), won_txt, font=tag_font)
        draw.text((chip[0] + (68 - (bb[2] - bb[0])) // 2,
                   chip[1] + (30 - (bb[3] - bb[1])) // 2 - bb[1]),
                  won_txt, fill=won_col, font=tag_font)
        draw.text((chip[2] + 14, y + 15), f"{r.score1} : {r.score2}",
                  fill=_TITLE, font=main_font)
        meta = "  ·  ".join(x for x in [
            names.mode_name(r.mode_key),
            r.map_name or names.map_name(r.map_id),
        ] if x)
        right = width - margin - 26
        kda_txt = f"{r.kills} / {r.deaths} / {r.assists}"
        bb2 = draw.textbbox((0, 0), kda_txt, font=main_font)
        draw.text((right - (bb2[2] - bb2[0]), y + 15), kda_txt,
                  fill=_TITLE, font=main_font)
        score_label = (
            f"得分 {r.acs:.0f}"
            if r.mode_key.lower() in _NO_ACS_MODES else f"ACS {r.acs:.0f}"
        )
        detail_parts = [r.agent_name or names.agent_name(r.agent_id), score_label]
        if r.is_match_mvp:
            detail_parts.append("全场MVP")
        if r.rank_tier_after is not None:
            tier_txt = names.tier_name(r.rank_tier_after)
            if r.rr_earned is not None:
                tier_txt += f" {r.rr_earned:+d}分"
            detail_parts.append(tier_txt)
        detail = "  ·  ".join(detail_parts)
        meta_left = chip[0]
        if meta and (meta_left + _text_w(draw, meta, small_font) + 12
                     > right - _text_w(draw, detail, small_font)):
            meta = r.map_name or names.map_name(r.map_id)
        meta_f, detail_f, _mw, dw = _fit_row_pair(
            draw, meta, detail, meta_left, right, small_font)
        draw.text((meta_left, y + 64), meta, fill=_MUTED, font=meta_f)
        draw.text((right - dw, y + 64), detail, fill=_MUTED, font=detail_f)
        y += row_h + gap

    _draw_tip(draw, width, height - footer_h + 12,
              "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def build_teammates_image(
        entries: List[Tuple[PlayerBrief, List[MatchRow]]],
        agent_icons: Optional[Dict[str, str]] = None) -> bytes:
    """赛前队友卡片：每人一行，显示近 3 场状态。返回 JPG 字节流。

    agent_icons：{特工中文名: 本地头像路径}，展示每人最近一场使用的特工。
    """
    width = _CARD_W
    margin = _MARGIN
    head_h = 130
    row_h = 96
    gap = 14
    footer_h = 64
    height = head_h + gap + max(len(entries), 1) * (row_h + gap) + footer_h - gap

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _draw_edge_decor(draw, width, 24, height - footer_h + 6)
    title_font = _load_font(40)
    main_font = _load_font(28)
    small_font = _load_font(24)

    draw.rounded_rectangle((margin, 34, width - margin, 34 + 62), radius=16, fill=_ACCENT)
    bb = draw.textbbox((0, 0), "赛前 · 队友近期状态", font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2, 34 + (62 - (bb[3] - bb[1])) // 2 - bb[1]),
              "赛前 · 队友近期状态", fill=(255, 255, 255), font=title_font)
    y = 34 + 62 + gap

    if not entries:
        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=14)
        _draw_tip(draw, width, y + 32, "当前不在房间或对局中，或接口未返回成员列表", main_font)
        y += row_h + gap

    for player, rows in entries:
        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=14)
        last_agent = rows[0].agent_name if rows else ""
        icon_path = (agent_icons or {}).get(last_agent)
        ax, ay = margin + 18, y + 16
        if icon_path:
            _paste_square_thumb(img, icon_path, ax, ay, 64, radius=14,
                                bg=(240, 242, 246))
        else:
            draw.rounded_rectangle((ax, ay, ax + 64, ay + 64), radius=14,
                                   fill=(226, 232, 240))
            initial = (last_agent or player.display or "?")[:1]
            bbi = draw.textbbox((0, 0), initial, font=main_font)
            draw.text((ax + (64 - (bbi[2] - bbi[0])) // 2 - bbi[0],
                       ay + (64 - (bbi[3] - bbi[1])) // 2 - bbi[1]),
                      initial, fill=_TITLE, font=main_font)
        dot_x = width - margin - 30 - 3 * 34
        disp = player.display
        name_limit = dot_x - 14 - (margin + 98)
        while _text_w(draw, disp, main_font) > name_limit and len(disp) > 4:
            disp = disp[:-2]
        if disp != player.display:
            disp = disp[:-1] + "…"
        draw.text((margin + 98, y + 14), disp, fill=_TITLE, font=main_font)
        tier = _last_rank(rows)
        head_line = names.tier_name(tier) if tier is not None else "段位未知"
        draw.text((margin + 98, y + 14 + 40), head_line, fill=_MUTED, font=small_font)
        for i, r in enumerate(rows[:3]):
            col = _WIN if r.won is True else _LOSE if r.won is False else _MUTED
            draw.ellipse((dot_x + i * 34, y + 22, dot_x + i * 34 + 22, y + 22 + 22), fill=col)
        if rows:
            kda, acs, _wr = _agg(rows[:3])
            stat = f"近{len(rows[:3])}场  KDA {kda:.2f}  ·  ACS {acs:.0f}"
        else:
            stat = "近期无对局数据"
        bb2 = draw.textbbox((0, 0), stat, font=small_font)
        draw.text((width - margin - 30 - (bb2[2] - bb2[0]), y + 14 + 40), stat,
                  fill=_GOLD if rows else _MUTED, font=small_font)
        y += row_h + gap

    _draw_tip(draw, width, height - footer_h + 16, "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _hex_rgb(s: str, default: Tuple[int, int, int] = _ACCENT) -> Tuple[int, int, int]:
    v = (s or "").strip().lstrip("#")
    if len(v) == 6:
        try:
            return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
        except ValueError:
            pass
    return default


def _norm_map_key(map_id: str) -> str:
    """'/Game/Maps/Ascent/Ascent' 或 'ascent' → '/game/maps/ascent/ascent'。"""
    m = (map_id or "").strip().lower()
    if not m:
        return ""
    if m.startswith("/game/maps/"):
        parts = m.split("/")
        return f"/game/maps/{parts[3]}/{parts[3]}" if len(parts) > 3 else ""
    if "/" not in m:
        return f"/game/maps/{m}/{m}"
    return ""


def _blend(fg: Tuple[int, int, int], bg: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    """fg 以透明度 t 叠到 bg 上的结果色。"""
    return tuple(int(b + (f - b) * t) for f, b in zip(fg, bg))  # type: ignore


def _winrate_color(rate: float) -> Tuple[int, int, int]:
    if rate >= 0.55:
        return _WIN
    if rate < 0.45:
        return _LOSE
    return _GOLD


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _fit_row_pair(draw: ImageDraw.ImageDraw, left: str, right: str,
                  left_x: int, right_edge: int, base_font,
                  min_size: int = 15, gap: int = 12):
    """同一行内左右两个文本的自适应排布（左锚 / 右锚，防重叠）。

    先缩右侧字号至 min_size，仍挤则再缩左侧。返回
    (left_font, right_font, left_w, right_w)。调用方负责语义化降级
    （如先去掉左侧前缀再调用本函数）。
    """
    lf = rf = base_font
    lw = _text_w(draw, left, lf) if left else 0
    rw = _text_w(draw, right, rf) if right else 0
    while right and lw + gap + rw > right_edge - left_x and rf.size > min_size:
        rf = _load_font(rf.size - 1)
        rw = _text_w(draw, right, rf)
    while left and lw + gap + rw > right_edge - left_x and lf.size > min_size:
        lf = _load_font(lf.size - 1)
        lw = _text_w(draw, left, lf)
    return lf, rf, lw, rw


def _draw_chip(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font,
               fg: Tuple[int, int, int], bg: Tuple[int, int, int],
               pad_x: int = 14, h: int = 34) -> int:
    """画圆角标签，返回 chip 宽度。"""
    w = _text_w(draw, text, font) + pad_x * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((x + (w - (bb[2] - bb[0])) // 2, y + (h - (bb[3] - bb[1])) // 2 - bb[1]),
              text, fill=fg, font=font)
    return w


def _rate_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
              rate: float, color: Optional[Tuple[int, int, int]] = None) -> None:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=(238, 240, 245))
    r = max(min(rate, 1.0), 0.0)
    if r > 0:
        fw = max(int(w * r), h)
        draw.rounded_rectangle((x, y, x + fw, y + h), radius=h // 2,
                               fill=color or _winrate_color(r))


async def fetch_images(urls: List[str]) -> Dict[str, str]:
    """批量下载图片到插件缓存目录（md5 文件名去重），返回 url → 本地路径。

    全部失败/为空时返回 {}，调用方需容忍缺图（用色块占位）。
    """
    out: Dict[str, str] = {}
    uniq = [u for u in {u.strip() for u in urls if u and u.strip()}]
    if not uniq:
        return out
    try:
        dst_dir = cache_dir() / "img"
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"图片缓存目录创建失败: {e}")
        return out

    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(limit=6)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"user-agent": "Mozilla/5.0"},
    ) as session:
        async def _one(u: str) -> None:
            fp = dst_dir / (hashlib.md5(u.encode("utf-8")).hexdigest()[:16] + ".png")
            if fp.exists() and fp.stat().st_size > 0:
                out[u] = str(fp)
                return
            for attempt in range(2):
                try:
                    async with session.get(u, timeout=timeout) as resp:
                        if resp.status != 200:
                            return
                        content = await resp.read()
                    fp.write_bytes(content)
                    out[u] = str(fp)
                    return
                except Exception as e:
                    if attempt:
                        logger.warning(f"素材下载失败 {u[:60]}: {e}")
                    else:
                        await asyncio.sleep(0.2)

        await asyncio.gather(*(_one(u) for u in uniq))
    return out


def _paste_square_thumb(base: PILImage.Image, path: str, x: int, y: int,
                        size: int, radius: int = 14,
                        bg: Tuple[int, int, int] = _ACCENT_SOFT) -> None:
    """贴圆角方形缩略图；失败时贴纯色占位。"""
    try:
        im = PILImage.open(path).convert("RGBA").resize((size, size))
        mask = PILImage.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius, fill=255)
        base.paste(im, (x, y), mask)
    except Exception:
        ImageDraw.Draw(base).rounded_rectangle((x, y, x + size, y + size),
                                               radius=radius, fill=bg)


def _paste_rect_thumb(base: PILImage.Image, path: str, x: int, y: int,
                      w: int, h: int, radius: int = 14,
                      bg: Tuple[int, int, int] = (226, 232, 240)) -> None:
    """贴圆角矩形缩略图（居中裁切填充）。"""
    try:
        im = PILImage.open(path).convert("RGBA")
        scale = max(w / im.width, h / im.height)
        im = im.resize((max(int(im.width * scale), 1), max(int(im.height * scale), 1)))
        ox = (im.width - w) // 2
        oy = (im.height - h) // 2
        im = im.crop((ox, oy, ox + w, oy + h))
        mask = PILImage.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius, fill=255)
        base.paste(im, (x, y), mask)
    except Exception:
        ImageDraw.Draw(base).rounded_rectangle((x, y, x + w, y + h),
                                               radius=radius, fill=bg)


def _paste_contain_thumb(base: PILImage.Image, path: str, x: int, y: int,
                         w: int, h: int) -> None:
    """按比例完整贴入透明素材，适合横向枪械图。"""
    try:
        im = PILImage.open(path).convert("RGBA")
        scale = min(w / im.width, h / im.height)
        im = im.resize((max(int(im.width * scale), 1), max(int(im.height * scale), 1)))
        px = x + (w - im.width) // 2
        py = y + (h - im.height) // 2
        base.paste(im, (px, py), im)
    except Exception:
        return


def _draw_radar(img: PILImage.Image, cx: int, cy: int, radius: int,
                dims: List[Tuple[str, str, float]],
                x_bounds: Optional[Tuple[int, int]] = None) -> None:
    """四维雷达图。dims = [(维度名, 数值文本, 百分位 0~1)]。

    x_bounds 为 (左, 右) 横向边界：轴标签中心会被钳制在边界内，
    防止侧向标签与相邻区域文字重叠、或溢出卡片左右边缘。
    """
    draw = ImageDraw.Draw(img)
    label_font = _load_font(24)
    value_font = _load_font(22)
    n = len(dims)
    if n < 3:
        draw.text((cx - 60, cy - 14), "数据不足", fill=_MUTED, font=label_font)
        return
    angles = [math.radians(-90 + 360.0 * i / n) for i in range(n)]

    for ring in (0.34, 0.67, 1.0):
        pts = [(cx + radius * ring * math.cos(a), cy + radius * ring * math.sin(a))
               for a in angles]
        draw.polygon(pts, outline=_LINE)
    for a in angles:
        draw.line((cx, cy, cx + radius * math.cos(a), cy + radius * math.sin(a)),
                  fill=_LINE, width=1)

    data_pts = []
    for (_, _, ratio), a in zip(dims, angles):
        r = radius * max(min(ratio, 1.0), 0.06)
        data_pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    draw.polygon(data_pts, fill=_blend(_ACCENT, (255, 255, 255), 0.24))
    draw.line(data_pts + [data_pts[0]], fill=_ACCENT, width=3)
    for px, py in data_pts:
        draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill=_ACCENT)

    for (label, value, _ratio), a in zip(dims, angles):
        lx = cx + (radius + 56) * math.cos(a)
        ly = cy + (radius + 44) * math.sin(a)
        for txt, fnt, ty, fill in ((label, label_font, ly - 22, _TITLE),
                                   (value, value_font, ly + 12, _MUTED)):
            tx_c = lx
            if x_bounds:
                bb = draw.textbbox((0, 0), txt, font=fnt)
                half = (bb[2] - bb[0]) / 2
                tx_c = min(max(lx, x_bounds[0] + half), x_bounds[1] - half)
            draw.text((tx_c, ty), txt, fill=fill, font=fnt, anchor="mm")


def build_report_image(player: PlayerBrief,
                       season: Dict[str, Any],
                       top_heroes: List[Dict[str, Any]],
                       radar: Dict[str, Any],
                       meta_agents: Dict[str, Dict[str, str]],
                       agent_imgs: Dict[str, str]) -> bytes:
    """赛季战报：总览 + 四维雷达 + 常用特工 TOP5。返回 JPG 字节流。

    season 为 mval val_card 解析后的扁平字段
    （games/wins/winrate/kda/acs/head_shot_rate/kast/damage_avg/kills_avg/
    time_hours/first_kills/five_kills/flawless/tier/tier_max/name）；

    top_heroes 为近期 record/list 聚合的特工 TOP5
    （每项 {name, games, win_rate, kda, acs, avatar_url, color}）；

    radar 为四维（kda/acs/head_shot_rate/damage_avg）数值。
    """
    width = _CARD_W
    margin = _MARGIN
    gap = 16
    header_h = 150
    stat_row_h = 112
    mid_h = 340
    agent_row_h = 68
    show_chars = top_heroes[:5]
    agent_block_h = (74 + len(show_chars) * agent_row_h + 8) if show_chars else 0
    footer_h = 64
    height = (34 + 64 + gap + header_h + gap + stat_row_h + gap + mid_h
              + (gap + agent_block_h if agent_block_h else 0) + footer_h)

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _draw_edge_decor(draw, width, 24, height - footer_h + 6)

    title_font = _load_font(40)
    name_font = _load_font(38)
    main_font = _load_font(28)
    small_font = _load_font(24)
    tiny_font = _load_font(21)
    num_font = _load_font(34)

    season_name = str(season.get("name") or "当前赛季")

    draw.rounded_rectangle((margin, 34, width - margin, 34 + 64), radius=16, fill=_ACCENT)
    banner = f"赛季战报 · {season_name}"
    bb = draw.textbbox((0, 0), banner, font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2, 34 + (64 - (bb[3] - bb[1])) // 2 - bb[1]),
              banner, fill=(255, 255, 255), font=title_font)
    y = 34 + 64 + gap

    _rounded_card(draw, (margin, y, width - margin, y + header_h))
    draw.text((margin + 30, y + 30), player.display, fill=_TITLE, font=name_font)
    games = int(season.get("games") or 0)
    winnum = int(season.get("wins") or 0)
    winrate = float(season.get("winrate") or 0.0)
    tier_now = int(season.get("tier") or 0)
    tier_max = int(season.get("tier_max") or 0)
    sub = "  ·  ".join(x for x in [
        f"场次 {games}",
        f"胜 {winnum} / 负 {max(games - winnum, 0)}",
        f"段位 {names.tier_name(tier_now)}" if tier_now else "",
        f"赛季最高 {names.tier_name(tier_max)}" if tier_max and tier_max != tier_now else "",
    ] if x)
    draw.text((margin + 30, y + 30 + 58), sub, fill=_MUTED, font=small_font)
    wr_txt = f"{winrate * 100:.1f}%"
    bbx = draw.textbbox((0, 0), wr_txt, font=num_font)
    wr_cx = width - margin - 90
    draw.text((wr_cx - (bbx[2] - bbx[0]) // 2, y + 34), wr_txt,
              fill=_winrate_color(winrate), font=num_font)
    wr_label = "赛季胜率"
    bbl = draw.textbbox((0, 0), wr_label, font=tiny_font)
    draw.text((wr_cx - (bbl[2] - bbl[0]) // 2, y + 34 + 48), wr_label,
              fill=_MUTED, font=tiny_font)
    y += header_h + gap

    _rounded_card(draw, (margin, y, width - margin, y + stat_row_h))
    stat_items = [
        ("KDA", f"{float(season.get('kda') or 0.0):.2f}"),
        ("ACS 均分", f"{float(season.get('acs') or 0.0):.0f}"),
        ("场均伤害", f"{float(season.get('damage_avg') or 0.0):.0f}"),
        ("精准击败", f"{float(season.get('head_shot_rate') or 0.0) * 100:.1f}%"),
        ("KAST", f"{float(season.get('kast') or 0.0) * 100:.1f}%"),
        ("回合均击败", f"{float(season.get('kills_avg') or 0.0):.2f}"),
    ]
    cell_w = (width - margin * 2) // len(stat_items)
    for i, (label, value) in enumerate(stat_items):
        cx = margin + cell_w * i + cell_w // 2
        bbv = draw.textbbox((0, 0), value, font=num_font)
        draw.text((cx - (bbv[2] - bbv[0]) // 2, y + 22), value, fill=_TITLE, font=num_font)
        bbl2 = draw.textbbox((0, 0), label, font=tiny_font)
        draw.text((cx - (bbl2[2] - bbl2[0]) // 2, y + 22 + 50), label,
                  fill=_MUTED, font=tiny_font)
        if i:
            draw.line((margin + cell_w * i, y + 22, margin + cell_w * i,
                       y + stat_row_h - 22), fill=_LINE, width=1)
    y += stat_row_h + gap

    _rounded_card(draw, (margin, y, width - margin, y + mid_h))
    hl_x = margin + 445
    radar_cx = margin + 210
    radar_cy = y + mid_h // 2 + 6
    radar_map = [
        ("KDA", "kda"),
        ("精准击败", "head_shot_rate"),
        ("ACS", "acs"),
        ("场均伤害", "damage_avg"),
    ]
    _RADAR_SCALES = {"kda": 2.0, "head_shot_rate": 0.4, "acs": 300.0, "damage_avg": 200.0}
    radar_dims: List[Tuple[str, str, float]] = []
    for label, key in radar_map:
        v = float(radar.get(key) or 0.0)
        scale = _RADAR_SCALES.get(key, 1.0) or 1.0
        pct_val = v * 100 if key == "head_shot_rate" else v
        txt = f"{pct_val:.1f}%" if key == "head_shot_rate" else f"{pct_val:.2f}" if key == "kda" else f"{pct_val:.0f}"
        radar_dims.append((label, txt, min(max(v / scale, 0.06), 1.0)))
    if any(d[2] > 0 for d in radar_dims):
        _draw_radar(img, radar_cx, radar_cy, 90, radar_dims,
                    x_bounds=(margin + 10, hl_x - 14))
    else:
        _draw_tip(draw, margin + 210, radar_cy - 14, "本赛季雷达数据不足", small_font)

    draw.text((hl_x, y + 26), "赛季亮点", fill=_TITLE, font=main_font)
    hours = float(season.get("time_hours") or 0)
    highlights = [
        ("游戏时长", f"{hours:.1f} 小时"),
        ("首杀次数", f"{int(season.get('first_kills') or 0)}"),
        ("五杀次数", f"{int(season.get('five_kills') or 0)}"),
        ("零封回合", f"{int(season.get('flawless') or 0)}"),
    ]
    hy = y + 26 + 48
    for label, value in highlights:
        draw.text((hl_x, hy), label, fill=_MUTED, font=small_font)
        bbh = draw.textbbox((0, 0), value, font=main_font)
        draw.text((width - margin - 40 - (bbh[2] - bbh[0]), hy - 4), value,
                  fill=_TITLE, font=main_font)
        hy += 50
    y += mid_h + gap

    if agent_block_h:
        _rounded_card(draw, (margin, y, width - margin, y + agent_block_h))
        draw.text((margin + 30, y + 22), "常用特工 TOP5", fill=_TITLE, font=main_font)
        ay = y + 74
        for c in show_chars:
            color = _hex_rgb(str(c.get("color", "")))
            avatar = agent_imgs.get(str(c.get("avatar_url") or "")) if c.get("avatar_url") else None
            if avatar:
                _paste_square_thumb(img, avatar, margin + 30, ay + 6, 52, radius=12,
                                    bg=_blend(color, (255, 255, 255), 0.3))
            else:
                draw.rounded_rectangle((margin + 30, ay + 6, margin + 82, ay + 58),
                                       radius=12,
                                       fill=_blend(color, (255, 255, 255), 0.55))
            aname = str(c.get("name") or "未知特工")
            games_c = int(c.get("games") or 0)
            wr_c = float(c.get("win_rate") or 0.0)
            draw.text((margin + 96, ay + 4), aname, fill=_TITLE, font=main_font)
            sub_c = (f"{games_c} 场 · 胜率 {wr_c * 100:.0f}% · "
                     f"KDA {float(c.get('kda') or 0.0):.2f}")
            draw.text((margin + 96, ay + 4 + 36), sub_c, fill=_MUTED, font=small_font)
            bar_w = 170
            bar_x = width - margin - 30 - bar_w
            _rate_bar(draw, bar_x, ay + 12, bar_w, 12, wr_c)
            acs_txt = f"ACS {float(c.get('acs') or 0.0):.0f}"
            bba = draw.textbbox((0, 0), acs_txt, font=small_font)
            draw.text((width - margin - 30 - (bba[2] - bba[0]), ay + 34), acs_txt,
                      fill=_GOLD, font=small_font)
            ay += agent_row_h

    _draw_tip(draw, width, height - footer_h + 16, "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def build_map_image(player: PlayerBrief, season_name: str,
                    rows: List[Dict[str, Any]],
                    meta_maps: Dict[str, Dict[str, str]],
                    meta_agents: Dict[str, Dict[str, str]],
                    imgs: Dict[str, str]) -> bytes:
    """各地图数据卡片。rows 为 GetMap 返回的 maps 列表。"""
    width = _CARD_W
    margin = _MARGIN
    gap = 16
    header_h = 120
    row_h = 88
    footer_h = 64

    def _games(m: Dict[str, Any]) -> int:
        return int(m.get("win") or 0) + int(m.get("lose") or 0)

    rows = sorted(rows, key=_games, reverse=True)
    best = None
    candidates = [m for m in rows if _games(m) >= 5]
    if candidates:
        best = max(candidates, key=lambda m: float(m.get("win_rate") or 0.0))

    height = 34 + 64 + gap + header_h + gap + len(rows) * (row_h + 10) + footer_h

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _draw_edge_decor(draw, width, 24, height - footer_h + 6)
    title_font = _load_font(38)
    main_font = _load_font(28)
    small_font = _load_font(24)
    tiny_font = _load_font(21)

    draw.rounded_rectangle((margin, 34, width - margin, 34 + 64), radius=16, fill=_ACCENT)
    banner = f"地图数据 · {season_name}"
    bb = draw.textbbox((0, 0), banner, font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2, 34 + (64 - (bb[3] - bb[1])) // 2 - bb[1]),
              banner, fill=(255, 255, 255), font=title_font)
    y = 34 + 64 + gap

    _rounded_card(draw, (margin, y, width - margin, y + header_h))
    draw.text((margin + 30, y + 22), player.display, fill=_TITLE, font=main_font)
    total_g = sum(_games(m) for m in rows)
    total_w = sum(int(m.get("win") or 0) for m in rows)
    draw.text((margin + 30, y + 22 + 42),
              f"共 {total_g} 场 · 胜 {total_w} · 覆盖 {len(rows)} 张地图",
              fill=_MUTED, font=small_font)
    if best is not None:
        binfo = meta_maps.get(_norm_map_key(str(best.get("id") or "")), {})
        bn = (
            best.get("name")
            or binfo.get("name")
            or names.map_name(str(best.get("id") or ""))
        )
        chip_text = f"最佳 {bn}"
        chip_w = _text_w(draw, chip_text, small_font) + 28
        _draw_chip(draw, width - margin - 30 - chip_w, y + 30, chip_text,
                   small_font, _GOLD, (254, 243, 199))
    y += header_h + gap

    for m in rows:
        mid = str(m.get("id") or "")
        info = meta_maps.get(_norm_map_key(mid), {})
        preview = imgs.get(str(m.get("preview_url") or "")
                           or info.get("preview_url", "") or "")
        mname = (
            m.get("name")
            or m.get("display_name")
            or info.get("name")
            or names.map_name(mid)
        )
        w_g = int(m.get("win") or 0)
        l_g = int(m.get("lose") or 0)
        rate = float(m.get("win_rate") or 0.0)
        is_best = best is not None and mid == str(best.get("id") or "")

        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=14)
        if is_best:
            draw.rounded_rectangle((margin, y, width - margin, y + row_h),
                                   radius=14, outline=_GOLD, width=3)
        if preview:
            _paste_rect_thumb(img, preview, margin + 12, y + 10, 116, row_h - 20)
        else:
            draw.rounded_rectangle((margin + 12, y + 10, margin + 128, y + row_h - 10),
                                   radius=14, fill=(226, 232, 240))
        tx = margin + 144
        draw.text((tx, y + 12), mname, fill=_TITLE, font=main_font)
        draw.text((tx, y + 12 + 38), f"{w_g} 胜 / {l_g} 负", fill=_MUTED, font=tiny_font)

        bar_x = tx + 178
        bar_w = 140
        _rate_bar(draw, bar_x, y + 18, bar_w, 14, rate)
        rate_txt = f"{rate * 100:.0f}%"
        bbr = draw.textbbox((0, 0), rate_txt, font=small_font)
        draw.text((bar_x + bar_w // 2 - (bbr[2] - bbr[0]) // 2, y + 40), rate_txt,
                  fill=_winrate_color(rate), font=small_font)

        champ = m.get("champion") or {}
        cid = str(champ.get("id") or "").lower()
        aname = (
            champ.get("name")
            or (meta_agents.get(cid, {}).get("name") if cid else "")
            or names.agent_name(cid)
        )
        has_champ = bool(aname and aname != "未知特工")
        line1 = line2 = ""
        bb1 = bb3 = (0, 0, 0, 0)
        cav = None
        rx = width - margin - 24
        if has_champ:
            cw = int(champ.get("win") or 0)
            cl = int(champ.get("lose") or 0)
            line1 = aname  # 仅特工名：880 宽卡「常用 」前缀会挤占 KDA/ACS 空间
            line2 = f"{cw}胜 {cl}负"
            bb1 = draw.textbbox((0, 0), line1, font=small_font)
            bb3 = draw.textbbox((0, 0), line2, font=tiny_font)
            cav_url = str(champ.get("avatar_url") or "")
            cav = imgs.get(cav_url) if cav_url else None
            if cav:
                rx -= 44 + 10
        champ_left = rx - max(bb1[2] - bb1[0], bb3[2] - bb3[0]) if has_champ else width

        stat_x = bar_x + bar_w + 28
        dmg = float(m.get('damage_avg') or 0.0)
        stat1 = f"KDA {float(m.get('kda') or 0.0):.2f}"
        stat2 = f"ACS {float(m.get('score_avg') or 0.0):.0f}"
        if dmg:
            stat2 += f" · 伤 {dmg:.0f}"
        avail = champ_left - 12 - stat_x
        f1, f2 = small_font, tiny_font
        if avail > 40:
            while _text_w(draw, stat1, f1) > avail and f1.size > 15:
                f1 = _load_font(f1.size - 1)
            while _text_w(draw, stat2, f2) > avail and f2.size > 15:
                f2 = _load_font(f2.size - 1)
        draw.text((stat_x, y + 12), stat1, fill=_TITLE, font=f1)
        draw.text((stat_x, y + 12 + 32), stat2, fill=_MUTED, font=f2)

        if has_champ:
            if cav:
                _paste_square_thumb(img, cav, rx + 10, y + 22, 44, radius=10,
                                    bg=(240, 242, 246))
            ameta = meta_agents.get(cid) or {}
            a_col = str(champ.get("color") or "") or ameta.get("color", "")
            draw.text((rx - (bb1[2] - bb1[0]), y + 14), line1,
                      fill=_hex_rgb(a_col, _TITLE), font=small_font)
            draw.text((rx - (bb3[2] - bb3[0]), y + 14 + 32), line2,
                      fill=_MUTED, font=tiny_font)
        y += row_h + 10

    _draw_tip(draw, width, height - footer_h + 16, "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def build_champions_image(player: PlayerBrief, heroes: List[Dict[str, Any]],
                          meta_agents: Dict[str, Dict[str, str]],
                          imgs: Dict[str, str]) -> bytes:
    """英雄池卡片：近期 50 场特工聚合 TOP8。

    heroes 为 record/list 按 hero_name 聚合后的列表
    （每项 {name, games, wins, win_rate, kda, acs, color, avatar_url}）。
    """
    width = _CARD_W
    margin = _MARGIN
    gap = 16
    header_h = 116
    row_h = 86
    footer_h = 64

    rows = sorted(heroes, key=lambda h: int(h.get("games") or 0), reverse=True)[:8]
    max_games = max((int(h.get("games") or 0) for h in rows), default=1) or 1
    total_games = sum(int(h.get("games") or 0) for h in heroes)

    height = (34 + 64 + gap + header_h + gap + len(rows) * (row_h + 10) + footer_h)

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _draw_edge_decor(draw, width, 24, height - footer_h + 6)
    title_font = _load_font(38)
    main_font = _load_font(28)
    small_font = _load_font(24)
    tiny_font = _load_font(21)

    draw.rounded_rectangle((margin, 34, width - margin, 34 + 64), radius=16, fill=_ACCENT)
    banner = "英雄池 · 近期 50 场"
    bb = draw.textbbox((0, 0), banner, font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2, 34 + (64 - (bb[3] - bb[1])) // 2 - bb[1]),
              banner, fill=(255, 255, 255), font=title_font)
    y = 34 + 64 + gap

    _rounded_card(draw, (margin, y, width - margin, y + header_h))
    draw.text((margin + 30, y + 22), player.display, fill=_TITLE, font=main_font)
    draw.text((margin + 30, y + 22 + 40),
              f"使用过 {len(heroes)} 名特工 · 累计 {total_games} 场（近期 50 场聚合）",
              fill=_MUTED, font=small_font)
    y += header_h + gap

    for idx, h in enumerate(rows):
        name = str(h.get("name") or "未知特工")
        color = _hex_rgb(str(h.get("color", "")))
        games = int(h.get("games") or 0)
        wr = float(h.get("win_rate") or 0.0)
        kda = float(h.get("kda") or 0.0)
        acs = float(h.get("acs") or 0.0)
        avatar = imgs.get(str(h.get("avatar_url") or "")) if h.get("avatar_url") else None

        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=14)
        if avatar:
            _paste_square_thumb(img, avatar, margin + 12, y + 12, 52, radius=12,
                                bg=_blend(color, (255, 255, 255), 0.3))
        else:
            draw.rounded_rectangle((margin + 12, y + 12, margin + 64, y + 64),
                                   radius=12, fill=_blend(color, (255, 255, 255), 0.55))
        tx = margin + 80
        draw.text((tx, y + 8), f"{idx + 1}. {name}", fill=_TITLE, font=main_font)
        kda_txt = f"KDA {kda:.2f}  ·  ACS {acs:.0f}"
        right_end = width - margin - 30
        bb_kda = draw.textbbox((0, 0), kda_txt, font=small_font)
        draw.text((right_end - (bb_kda[2] - bb_kda[0]), y + 12),
                  kda_txt, fill=_MUTED, font=small_font)
        draw.text((tx, y + 8 + 36),
                  f"{games} 场 · 胜率 {wr * 100:.0f}%",
                  fill=_MUTED, font=tiny_font)
        bar_x = tx
        bar_w = right_end - bar_x
        _rate_bar(draw, bar_x, y + 74, bar_w, 6, games / max_games,
                  color=_blend(color, (255, 255, 255), 0.55))
        y += row_h + 10

    _draw_tip(draw, width, height - footer_h + 16, "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def build_friends_image(player: PlayerBrief, friends: List[Dict[str, Any]]) -> bytes:
    """开黑队友卡片：按开黑场次排序，显示胜率/ACS/同游时长/净胜贡献。

    friends 每项 {nickname, battle_count, win_count, win_rate, avg_acs,
    total_secs, score, tier}，由 matchers 聚合自 mval scoreboard。
    """
    width = _CARD_W
    margin = _MARGIN
    gap = 16
    header_h = 116
    row_h = 84
    footer_h = 64

    def _cnt(f: Dict[str, Any]) -> int:
        try:
            return int(f.get("battle_count") or 0)
        except (TypeError, ValueError):
            return 0

    rows = [f for f in friends if _cnt(f) > 0]
    rows = sorted(rows, key=_cnt, reverse=True)[:10]

    height = 34 + 64 + gap + header_h + gap + len(rows) * (row_h + 10) + footer_h

    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _draw_edge_decor(draw, width, 24, height - footer_h + 6)
    title_font = _load_font(38)
    main_font = _load_font(28)
    small_font = _load_font(24)
    tiny_font = _load_font(21)

    draw.rounded_rectangle((margin, 34, width - margin, 34 + 64), radius=16, fill=_ACCENT)
    banner = "开黑队友 · 近期对局聚合"
    bb = draw.textbbox((0, 0), banner, font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2, 34 + (64 - (bb[3] - bb[1])) // 2 - bb[1]),
              banner, fill=(255, 255, 255), font=title_font)
    y = 34 + 64 + gap

    _rounded_card(draw, (margin, y, width - margin, y + header_h))
    draw.text((margin + 30, y + 22), player.display, fill=_TITLE, font=main_font)
    total_battles = sum(_cnt(f) for f in friends)
    total_secs = 0
    for f in friends:
        try:
            total_secs += int(f.get("total_secs") or 0)
        except (TypeError, ValueError):
            pass
    draw.text((margin + 30, y + 22 + 40),
              f"开黑队友 {len(rows)} 人 · 组队 {total_battles} 场 · "
              f"同游 {total_secs / 3600:.1f} 小时",
              fill=_MUTED, font=small_font)
    y += header_h + gap

    for idx, f in enumerate(rows):
        nick = str(f.get("nickname") or "未知玩家")
        cnt = _cnt(f)
        try:
            wins = int(f.get("win_count") or 0)
        except (TypeError, ValueError):
            wins = 0
        rate = wins / cnt if cnt else 0.0
        avg_acs = float(f.get("avg_acs") or 0.0)
        try:
            secs = int(f.get("total_secs") or 0)
        except (TypeError, ValueError):
            secs = 0
        try:
            score = int(f.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        tier = int(f.get("tier") or 0)

        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=14)
        top3 = [(218, 165, 32), (148, 163, 184), (180, 120, 80)]
        dot_col = top3[idx] if idx < 3 else _MUTED
        draw.ellipse((margin + 14, y + 24, margin + 50, y + 60), fill=dot_col)
        num = str(idx + 1)
        bbn = draw.textbbox((0, 0), num, font=small_font)
        draw.text((margin + 32 - (bbn[2] - bbn[0]) // 2,
                   y + 42 - (bbn[3] - bbn[1]) // 2 - bbn[1]),
                  num, fill=(255, 255, 255), font=small_font)

        tx = margin + 66
        disp = nick
        while _text_w(draw, disp, main_font) > 240 and len(disp) > 4:
            disp = disp[:-2]
        if disp != nick:
            disp = disp[:-1] + "…"
        draw.text((tx, y + 8), disp, fill=_TITLE, font=main_font)
        tier_txt = names.tier_name(tier) if tier else "段位未知"
        draw.text((tx, y + 8 + 38), tier_txt, fill=_MUTED, font=tiny_font)

        bar_x = tx + 262
        draw.text((bar_x, y + 8), f"{cnt} 场", fill=_TITLE, font=small_font)
        _rate_bar(draw, bar_x, y + 40, 120, 12, rate)
        draw.text((bar_x + 128, y + 30), f"{rate * 100:.0f}%",
                  fill=_winrate_color(rate), font=tiny_font)

        stat_x = bar_x + 190
        acs_txt = f"ACS {avg_acs:.0f}" if avg_acs else "ACS —"
        draw.text((stat_x, y + 8), acs_txt, fill=_TITLE, font=small_font)
        draw.text((stat_x, y + 8 + 32), f"同游 {secs / 3600:.1f}h",
                  fill=_MUTED, font=tiny_font)

        if score > 0:
            chip_txt, chip_fg, chip_bg = f"净胜 +{score}", _WIN, (220, 245, 231)
        elif score < 0:
            chip_txt, chip_fg, chip_bg = f"净胜 {score}", _LOSE, (252, 228, 226)
        else:
            chip_txt, chip_fg, chip_bg = "净胜 0", _MUTED, (238, 240, 245)
        cw = _text_w(draw, chip_txt, tiny_font) + 24
        _draw_chip(draw, width - margin - 24 - cw, y + 26, chip_txt,
                   tiny_font, chip_fg, chip_bg, h=32)
        y += row_h + 10

    _draw_tip(draw, width, height - footer_h + 16, "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _draw_edge_decor(
    draw: ImageDraw.ImageDraw, width: int, top: int, bottom: int,
) -> None:
    """在计分板侧边留白中绘制低对比度战术导轨。"""
    rail = (213, 220, 246)
    shard = (188, 199, 250)
    accent = (111, 130, 244)
    left, right = 9, width - 10
    draw.line((left, top + 18, left, bottom - 18), fill=rail, width=2)
    draw.line((right, top + 18, right, bottom - 18), fill=rail, width=2)
    for cy in (top + 92, (top + bottom) // 2, bottom - 92):
        draw.polygon(((3, cy), (10, cy - 9), (18, cy), (10, cy + 9)), fill=shard)
        draw.polygon(((width - 4, cy), (right, cy - 9),
                      (width - 19, cy), (right, cy + 9)), fill=shard)
        draw.ellipse((left - 3, cy - 3, left + 3, cy + 3), fill=accent)
        draw.ellipse((right - 3, cy - 3, right + 3, cy + 3), fill=accent)
    draw.line((left, top + 18, 18, top + 7), fill=accent, width=3)
    draw.line((right, top + 18, width - 19, top + 7), fill=accent, width=3)
    draw.line((left, bottom - 18, 18, bottom - 7), fill=accent, width=3)
    draw.line((right, bottom - 18, width - 19, bottom - 7), fill=accent, width=3)


def _battle_time_text(view: Dict[str, Any]) -> Tuple[str, str]:
    length_ms = _int_render(view.get("gameLengthMillis"))
    minutes, seconds = divmod(max(length_ms, 0) // 1000, 60)
    duration = f"{minutes}m {seconds}s" if length_ms else "用时未知"
    start_ms = _int_render(view.get("gameStartMillis"))
    if not start_ms:
        return duration, "时间未知"
    started = datetime.fromtimestamp(start_ms / 1000, ZoneInfo("Asia/Shanghai"))
    return duration, started.strftime("%Y.%m.%d %H:%M")


def _draw_round_result_icon(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, code: str,
    color: Tuple[int, int, int], highlight: bool = False,
) -> None:
    if highlight:
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            radius = 8 if i % 2 == 0 else 3.5
            points.append((cx + math.cos(angle) * radius,
                           cy + math.sin(angle) * radius))
        draw.polygon(points, fill=(230, 216, 132))
        return
    normalized = (code or "").lower()
    if "defuse" in normalized:
        draw.polygon(((cx, cy - 8), (cx - 6, cy + 6), (cx + 6, cy + 6)),
                     outline=color)
        draw.line((cx - 8, cy + 7, cx + 8, cy + 7), fill=color, width=2)
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=color)
    elif "deton" in normalized or "bomb" in normalized:
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=color, width=2)
        draw.line((cx + 4, cy - 5, cx + 8, cy - 9), fill=color, width=2)
        draw.line((cx + 8, cy - 9, cx + 10, cy - 6), fill=color, width=2)
    elif "time" in normalized:
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), outline=color, width=2)
        draw.line((cx, cy, cx, cy - 5), fill=color, width=2)
        draw.line((cx, cy, cx + 4, cy + 2), fill=color, width=2)
    else:
        draw.line((cx - 7, cy - 7, cx + 7, cy + 7), fill=color, width=3)
        draw.line((cx + 7, cy - 7, cx - 7, cy + 7), fill=color, width=3)
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=(37, 48, 63))


def _draw_round_swap(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    color = (111, 122, 140)
    draw.arc((cx - 8, cy - 7, cx + 8, cy + 7), 200, 350, fill=color, width=2)
    draw.arc((cx - 8, cy - 7, cx + 8, cy + 7), 20, 170, fill=color, width=2)
    draw.polygon(((cx + 8, cy - 2), (cx + 3, cy - 5), (cx + 4, cy + 1)), fill=color)
    draw.polygon(((cx - 8, cy + 2), (cx - 3, cy + 5), (cx - 4, cy - 1)), fill=color)


def _played_rounds(rounds_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        row for row in rounds_data
        if "surrender" not in str(
            row.get("roundResultCode") or row.get("roundResult") or ""
        ).lower()
    ]


def _battle_team_state(
    rounds_data: List[Dict[str, Any]], team_order: List[str],
    view: Dict[str, Any],
) -> Tuple[Dict[str, int], Dict[str, str], List[Dict[str, Any]]]:
    rows = sorted(
        _played_rounds(rounds_data),
        key=lambda row: _int_render(row.get("roundNum")),
    )
    scores = {team: 0 for team in team_order}
    sides: Dict[str, str] = {}
    if rows and len(team_order) == 2:
        tracked = str(rows[-1].get("teamId") or view.get("playerTeamId") or "")
        if tracked in scores:
            other = next(team for team in team_order if team != tracked)
            for row in rows:
                row_team = str(row.get("teamId") or tracked)
                if row_team not in scores:
                    continue
                opponent = next(team for team in team_order if team != row_team)
                scores[row_team if _int_render(row.get("isRoundWon")) else opponent] += 1
            tracked_side = (
                "Attackers" if _int_render(rows[-1].get("isAttack"))
                else "Defenders"
            )
            sides[tracked] = tracked_side
            sides[other] = "Defenders" if tracked_side == "Attackers" else "Attackers"
            return scores, sides, rows

    my_team = str(view.get("playerTeamId") or "")
    played = _int_render(view.get("roundsPlayed"))
    won = _int_render(view.get("roundsWon"))
    if my_team in scores:
        scores[my_team] = won
        if len(team_order) == 2:
            other = next(team for team in team_order if team != my_team)
            scores[other] = max(played - won, 0)
    return scores, sides, rows


def _side_colors(side: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    if side == "Attackers":
        return _LOSE, _LOSE_SOFT
    if side == "Defenders":
        return _WIN, _WIN_SOFT
    return _ACCENT, _ACCENT_SOFT


def _draw_round_timeline(
    draw: ImageDraw.ImageDraw, rounds_data: List[Dict[str, Any]],
    x: int, y: int, width: int, tiny_font: ImageFont.FreeTypeFont,
) -> None:
    rows = sorted(_played_rounds(rounds_data),
                  key=lambda row: _int_render(row.get("roundNum")))
    if not rows:
        return
    swaps = {
        index for index in range(1, len(rows))
        if _int_render(rows[index - 1].get("isAttack"))
        != _int_render(rows[index].get("isAttack"))
    }
    gap = 2 if len(rows) > 24 else 3
    swap_gap = 18
    extra_width = len(swaps) * swap_gap
    cell_w = max(
        8,
        min(24, (width - gap * (len(rows) - 1) - extra_width) // len(rows)),
    )
    used_w = len(rows) * cell_w + gap * (len(rows) - 1) + extra_width
    cursor = x + max((width - used_w) // 2, 0)
    number_font = _load_font(12 if len(rows) > 20 else 14)
    max_score = max((_int_render(row.get("roundScore")) for row in rows), default=0)
    for index, row in enumerate(rows):
        if index in swaps:
            _draw_round_swap(draw, cursor + swap_gap // 2, y + 26)
            cursor += swap_gap
        draw.rectangle((cursor, y, cursor + cell_w, y + 48), fill=(37, 48, 63))
        number = str(_int_render(row.get("roundNum")) + 1)
        number_w = _text_w(draw, number, number_font)
        draw.text((cursor + (cell_w - number_w) // 2, y + 3), number,
                  fill=(242, 245, 250), font=number_font)
        won = bool(_int_render(row.get("isRoundWon")))
        tracked_attack = bool(_int_render(row.get("isAttack")))
        attacker_won = tracked_attack == won
        color = _LOSE if attacker_won else _WIN
        score = _int_render(row.get("roundScore"))
        _draw_round_result_icon(
            draw, cursor + cell_w // 2, y + 34,
            str(row.get("roundResultCode") or row.get("roundResult") or ""), color,
            highlight=bool(max_score and score == max_score),
        )
        draw.rectangle((cursor, y + 46, cursor + cell_w, y + 48), fill=color)
        cursor += cell_w + gap


def build_battle_detail_image(
    battle: Dict[str, Any], my_subject: str = "", live: bool = False,
    agent_icons: Optional[Dict[str, str]] = None,
) -> bytes:
    """绘制双方 10 人单场详情，字段直接来自 WeGame GetBattleDetail。"""
    players = list(battle.get("players") or [])[:10]
    view = battle.get("playerGameView") or {}
    team_order = []
    for p in players:
        team = str(p.get("teamId") or "未知")
        if team not in team_order:
            team_order.append(team)
    team_order = team_order[:2]
    players.sort(key=lambda p: (
        team_order.index(str(p.get("teamId") or "未知"))
        if str(p.get("teamId") or "未知") in team_order else 9,
        -int(float(p.get("statsScore") or 0)),
    ))

    width, margin, top = 1120, 24, 22
    banner_h, summary_h, team_head_h, row_h = 62, 72, 42, 68
    gap, team_gap, footer_h = 10, 8, 46
    height = top + banner_h + gap + summary_h + gap \
             + len(team_order) * team_head_h + len(players) * row_h \
             + max(len(team_order) - 1, 0) * team_gap + footer_h
    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    title_font = _load_font(34)
    main_font = _load_font(23)
    small_font = _load_font(19)
    tiny_font = _load_font(16)
    _draw_edge_decor(draw, width, top, height - footer_h + 6)

    draw.rounded_rectangle((margin, top, width - margin, top + banner_h),
                           radius=16, fill=_ACCENT)
    title = ("对局中" if live else "最近一局") + " · 双方 10 人单场详情"
    bb = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2,
               top + (banner_h - (bb[3] - bb[1])) // 2 - bb[1]),
              title, fill=(255, 255, 255), font=title_font)
    y = top + banner_h + gap

    _rounded_card(draw, (margin, y, width - margin, y + summary_h), radius=14)
    map_text = names.map_name(str(view.get("mapId") or ""))
    mode_text = names.mode_name(str(view.get("queueId") or ""))
    round_rows = list(battle.get("rounds") or [])
    team_scores, team_sides, played_round_rows = _battle_team_state(
        round_rows, team_order, view,
    )
    left_rounds = team_scores.get(team_order[0], 0) if team_order else 0
    right_rounds = team_scores.get(team_order[1], 0) if len(team_order) > 1 else 0
    draw.text((margin + 22, y + 12), f"{mode_text}  ·  {map_text}",
              fill=_TITLE, font=main_font)
    draw.text((margin + 22, y + 42),
              "数据已同步" if not live else "进行中数据，以接口当前返回为准",
              fill=_MUTED, font=small_font)
    duration, started_at = _battle_time_text(view)
    draw.line((310, y + 12, 310, y + summary_h - 12), fill=_LINE, width=2)
    draw.text((326, y + 10), duration, fill=_TITLE, font=small_font)
    draw.text((326, y + 40), started_at, fill=_MUTED, font=tiny_font)
    total_rounds = left_rounds + right_rounds
    if played_round_rows:
        _draw_round_timeline(draw, played_round_rows, 475, y + 12, 485, tiny_font)
    elif total_rounds:
        track_x, track_y, track_w = 480, y + 36, 420
        segment_gap = 3
        segment_w = max(
            6,
            min(16, (track_w - segment_gap * (total_rounds - 1)) // total_rounds),
        )
        used_w = total_rounds * segment_w + (total_rounds - 1) * segment_gap
        start_x = track_x + (track_w - used_w) // 2
        left_color = _side_colors(team_sides.get(team_order[0], ""))[0]
        right_color = _side_colors(team_sides.get(team_order[1], ""))[0]
        label = f"回合比分 · {total_rounds} 回合"
        label_w = _text_w(draw, label, tiny_font)
        draw.text((track_x + (track_w - label_w) // 2, y + 11), label,
                  fill=_MUTED, font=tiny_font)
        for index in range(total_rounds):
            x1 = start_x + index * (segment_w + segment_gap)
            color = left_color if index < left_rounds else right_color
            draw.rounded_rectangle((x1, track_y, x1 + segment_w, track_y + 9),
                                   radius=2, fill=color)
    score_parts = (str(left_rounds), " : ", str(right_rounds))
    score_widths = [_text_w(draw, part, title_font) for part in score_parts]
    score_x = width - margin - 22 - sum(score_widths)
    score_colors = (
        _side_colors(team_sides.get(team_order[0], ""))[0] if team_order else _ACCENT,
        _MUTED,
        _side_colors(team_sides.get(team_order[1], ""))[0]
        if len(team_order) > 1 else _ACCENT,
    )
    for part, part_width, part_color in zip(score_parts, score_widths, score_colors):
        draw.text((score_x, y + 15), part, fill=part_color, font=title_font)
        score_x += part_width
    y += summary_h + gap

    headers = [(570, "K / D / A"), (700, "ACS"), (788, "爆头"),
               (880, "回合伤害"), (1010, "首杀")]
    for team_index, team in enumerate(team_order):
        members = [p for p in players if str(p.get("teamId") or "未知") == team]
        won = any(bool(int(float(p.get("wonMatch") or 0))) for p in members)
        side = team_sides.get(team, str(team))
        color, soft = _side_colors(side)
        draw.rounded_rectangle((margin, y, width - margin, y + team_head_h),
                               radius=12, fill=soft)
        if live:
            score_value = team_scores.get(team, 0)
            other_score = max(
                (value for key, value in team_scores.items() if key != team),
                default=score_value,
            )
            if score_value == other_score:
                team_label = "当前"
            else:
                team_label = "领先方" if score_value > other_score else "落后方"
        else:
            team_label = "胜方" if won else "败方"
        draw.text((margin + 18, y + 8), f"{team_label} · {side}", fill=color, font=main_font)
        for x, text_value in headers:
            draw.text((x, y + 12), text_value, fill=color, font=tiny_font)
        y += team_head_h

        body_top = y
        body_bottom = y + len(members) * row_h
        _rounded_card(draw, (margin, body_top, width - margin, body_bottom), radius=12)
        draw.rectangle((margin, body_top, margin + 6, body_bottom), fill=color)
        for member_index, p in enumerate(members):
            is_me = str(p.get("subject") or "") == my_subject
            if is_me:
                draw.rounded_rectangle((margin + 6, y + 3, width - margin, y + row_h - 3),
                                       radius=8, fill=(239, 243, 255))
            if member_index:
                draw.line((margin + 76, y, width - margin - 18, y),
                          fill=(232, 236, 243), width=1)
            agent_id = str(p.get("characterId") or "")
            agent = names.agent_name(agent_id)
            icon_path = (agent_icons or {}).get(agent_id) or (agent_icons or {}).get(agent)
            if icon_path:
                _paste_square_thumb(img, icon_path, margin + 18, y + 11, 46, radius=10)
            else:
                draw.rounded_rectangle((margin + 18, y + 11, margin + 64, y + 57),
                                       radius=10, fill=(226, 232, 240))
                initial = (agent or "?")[:1]
                ib = draw.textbbox((0, 0), initial, font=main_font)
                draw.text((margin + 41 - (ib[2] - ib[0]) // 2 - ib[0],
                           y + 34 - (ib[3] - ib[1]) // 2 - ib[1]), initial,
                          fill=_TITLE, font=main_font)
            nickname = str(p.get("name") or "未知玩家")
            while _text_w(draw, nickname, main_font) > 320 and len(nickname) > 5:
                nickname = nickname[:-2]
            if nickname != str(p.get("name") or "未知玩家"):
                nickname = nickname[:-1] + "…"
            draw.text((margin + 76, y + 7), nickname + ("  [我]" if is_me else ""),
                      fill=_ACCENT if is_me else _TITLE, font=main_font)
            tier = int(float(p.get("competitiveTierAfter") or p.get("competitiveTier") or 0))
            utility = f"{agent} · {names.tier_name(tier)} · 下包 {_int_render(p.get('plantCount'))}/拆包 {_int_render(p.get('defuseCount'))}"
            draw.text((margin + 76, y + 39), utility, fill=_MUTED, font=tiny_font)

            kills = _int_render(p.get("statsKills")); deaths = _int_render(p.get("statsDeaths"))
            assists = _int_render(p.get("statsAssists")); rounds_p = max(_int_render(p.get("statsRoundsPlayed")), 1)
            score_v = float(p.get("statsScore") or 0)
            acs = score_v / rounds_p
            head = _int_render(p.get("totalHeadshots")); body = _int_render(p.get("totalBodyshots"))
            leg = _int_render(p.get("totalLegshots")); shots = head + body + leg
            hs = head / shots * 100 if shots else 0
            adr = float(p.get("totalDamage") or 0) / rounds_p
            values = [(570, f"{kills}/{deaths}/{assists}"), (700, f"{acs:.0f}"),
                      (788, f"{hs:.1f}%"), (880, f"{adr:.0f}"),
                      (1010, str(_int_render(p.get("firstKillCount"))))]
            for x, text_value in values:
                draw.text((x, y + 23), text_value, fill=_TITLE, font=small_font)
            y += row_h

        if team_index < len(team_order) - 1:
            y += team_gap

    _draw_tip(draw, width, height - footer_h + 12,
              "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=91)
    return buffer.getvalue()


def _int_render(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def build_weapon_image(player: PlayerBrief, weapons: List[Dict[str, Any]]) -> bytes:
    """绘制武器击杀、场均击杀、爆头率、最远击杀和总伤害。"""
    rows = sorted(weapons, key=lambda w: _int_render(w.get("kill")), reverse=True)
    rows = [w for w in rows if _int_render(w.get("kill")) > 0][:14]
    width, margin, row_h, gap, footer_h = 1060, 40, 74, 10, 64
    header_h = 150
    height = header_h + gap + max(len(rows), 1) * (row_h + gap) + footer_h - gap
    img = PILImage.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _draw_edge_decor(draw, width, 20, height - footer_h + 6)
    title_font = _load_font(38); main_font = _load_font(25)
    small_font = _load_font(21); tiny_font = _load_font(18)

    draw.rounded_rectangle((margin, 34, width - margin, 98), radius=16, fill=_ACCENT)
    title = "武器击杀 · 总赛季"
    bb = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (bb[2] - bb[0])) // 2, 48), title,
              fill=(255, 255, 255), font=title_font)
    draw.text((margin + 22, 112), player.display,
              fill=(230, 235, 241), font=main_font)
    headers = [(430, "击杀"), (540, "场均"), (650, "爆头率"),
               (780, "最远击杀"), (920, "伤害")]
    for x, label in headers:
        draw.text((x, 118), label, fill=(151, 161, 174), font=tiny_font)
    y = header_h + gap
    if not rows:
        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=13)
        _draw_tip(draw, width, y + 22, "暂无武器击杀数据", main_font)
        y += row_h + gap
    for index, weapon in enumerate(rows, 1):
        _rounded_card(draw, (margin, y, width - margin, y + row_h), radius=13)
        draw.rounded_rectangle((margin + 16, y + 17, margin + 56, y + 57),
                               radius=10, fill=_ACCENT_SOFT)
        num = str(index); nb = draw.textbbox((0, 0), num, font=small_font)
        draw.text((margin + 36 - (nb[2] - nb[0]) // 2,
                   y + 37 - (nb[3] - nb[1]) // 2 - nb[1]),
                  num, fill=_ACCENT, font=small_font)
        name = str(weapon.get("name") or weapon.get("type") or "未知武器")
        while _text_w(draw, name, main_font) > 285 and len(name) > 5:
            name = name[:-2]
        if _text_w(draw, name, main_font) > 285:
            name = name[:5] + "…"
        draw.text((margin + 72, y + 19), name, fill=_TITLE, font=main_font)
        image_path = str(weapon.get("image_path") or "")
        if image_path:
            _paste_contain_thumb(img, image_path, 205, y + 10, 195, 54)
        distance = float(weapon.get("kill_furthest_distance") or 0)
        if distance > 500:
            distance /= 100
        values = [
            (430, str(_int_render(weapon.get("kill")))),
            (540, f"{float(weapon.get('kill_avg') or 0):.2f}"),
            (650, f"{float(weapon.get('head_shot_rate') or 0) * 100:.1f}%"),
            (780, f"{distance:.1f}m"),
            (920, f"{float(weapon.get('damage') or 0):.0f}"),
        ]
        for x, value in values:
            draw.text((x, y + 23), value, fill=_TITLE, font=small_font)
        y += row_h + gap
    _draw_tip(draw, width, height - footer_h + 16,
              "数据来源&展示图片 by LuoYeBot", small_font)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=91)
    return buffer.getvalue()
