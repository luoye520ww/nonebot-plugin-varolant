import asyncio
from typing import Any, Dict, List, Optional, Tuple

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.params import RegexGroup

from ..core import database, gameinfo, names, switch, wegame
from ..core.mval import MatchRow, PlayerBrief
from ..core.render import (
    build_battle_detail_image,
    build_champions_image,
    build_friends_image,
    build_map_image,
    build_report_image,
    build_stats_image,
    build_weapon_image,
    fetch_images,
)
from ..core.wegame import WegameClient, WegameError, WegameRole

_RULE = switch.plugin_group_rule
_P = r"(?:瓦|无畏契约)"
stats_cmd = on_regex(rf"^(?:/)?{_P}\s*查战绩(?:\s+([\s\S]+?))?\s*$", rule=_RULE, priority=10, block=True)
stats_alias_cmd = on_regex(rf"^(?:/)?{_P}\s*战绩(?:\s+([\s\S]+?))?\s*$", rule=_RULE, priority=10, block=True)
teammate_cmd = on_regex(rf"^(?:/)?{_P}\s*队友(?:战绩)?\s*$", rule=_RULE, priority=10, block=True)
report_cmd = on_regex(rf"^(?:/)?{_P}\s*战报(?:\s+([\s\S]+?))?\s*$", rule=_RULE, priority=10, block=True)
map_cmd = on_regex(rf"^(?:/)?{_P}\s*地图(?:\s+([\s\S]+?))?\s*$", rule=_RULE, priority=10, block=True)
champions_cmd = on_regex(rf"^(?:/)?{_P}\s*英雄(?:池)?\s*$", rule=_RULE, priority=10, block=True)
friends_cmd = on_regex(rf"^(?:/)?{_P}\s*开黑\s*$", rule=_RULE, priority=10, block=True)
weapon_cmd = on_regex(rf"^(?:/)?{_P}\s*击杀\s*$", rule=_RULE, priority=10, block=True)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _event_id(row: Dict[str, Any]) -> str:
    battle = row.get("battle") if isinstance(row.get("battle"), dict) else row
    return str(row.get("ap_event_id") or row.get("apEventId")
               or battle.get("apEventId") or "")


def _battle_to_match(row: Dict[str, Any]) -> MatchRow:
    b = row.get("battle") if isinstance(row.get("battle"), dict) else row
    rounds = _int(b.get("roundsPlayed") or b.get("rounds"))
    won_rounds = _int(b.get("roundsWon") or b.get("score1"))
    score = _num(b.get("statsScore") or b.get("score"))
    acs = score / rounds if rounds else 0
    tier = (_int(b.get("competitiveTierAfter")) or _int(b.get("CompetitiveTierAfter"))
            or _int(b.get("competitiveTier")) or _int(b.get("rtierAft")))
    rr = b.get("competitiveTierRankedRatingEarned")
    if rr in (None, ""):
        rr = b.get("CompetitiveTierRankedRatingEarned")
    return MatchRow(
        event_id=_event_id(row), won=bool(_int(b.get("wonMatch") or b.get("result"))),
        score1=won_rounds, score2=max(rounds - won_rounds, 0),
        kills=_int(b.get("statsKills") or b.get("kills")),
        deaths=_int(b.get("statsDeaths") or b.get("deaths")),
        assists=_int(b.get("statsAssists") or b.get("assists")), acs=acs,
        agent_id=str(b.get("characterId") or ""),
        agent_name=names.agent_name(str(b.get("characterId") or "")),
        map_id=str(b.get("mapId") or ""), mode_key=str(b.get("queueId") or ""),
        rank_tier_after=tier or None, rr_earned=_int(rr) if rr not in (None, "") else None,
        is_match_mvp=bool(_int(b.get("isMatchMvp") or b.get("mvpsvp"))),
        first_kills=_int(b.get("firstKillCount")),
        start_time_ms=_int(b.get("gameStartMillis")), role_id=str(b.get("subject") or ""),
    )


async def _client_or_hint(matcher, user_id: str) -> Optional[WegameClient]:
    cfg = await database.get_wegame_config(user_id)
    if not cfg:
        await matcher.finish(
            "请先发送「瓦登录」使用微信扫码登录 WeGame。\n"
            "每日商店的「瓦app登录」不包含战绩权限。",
            reply_message=True,
        )
        return None
    async def save(updated: Dict[str, Any]) -> None:
        await database.save_wegame_config(user_id, updated)

    return wegame.get_client(cfg, save)


async def _fail(matcher, error: WegameError, prefix: str) -> None:
    if error.auth_invalid:
        await matcher.finish("WeGame 登录态已失效，请重新发送「瓦登录」", reply_message=True)
    await matcher.finish(f"{prefix}：{error}", reply_message=True)


async def _role(matcher, client: WegameClient) -> Optional[WegameRole]:
    try:
        return await client.resolve_own_subject()
    except WegameError as e:
        await _fail(matcher, e, "角色获取失败")
        return None


async def _agent_images(ids_or_names: List[str]) -> Dict[str, str]:
    data = await gameinfo.load_gameinfo()
    meta = gameinfo.agent_meta(data)
    labels_by_url: Dict[str, set[str]] = {}
    for value in ids_or_names:
        item = gameinfo.agent_lookup(meta, value) or gameinfo.lookup_by_name(meta, value)
        url = item.get("avatar_url") or ""
        if not url:
            continue
        labels_by_url.setdefault(url, set()).update(filter(None, (
            value, names.agent_name(value), item.get("name"), item.get("alias_cn"),
        )))
    fetched = await fetch_images(list(labels_by_url)) if labels_by_url else {}
    return {
        label: fetched[url]
        for url, labels in labels_by_url.items() if url in fetched
        for label in labels
    }


def _pick_season(data: Dict[str, Any], keyword: str) -> Optional[Tuple[str, str]]:
    options = gameinfo.seasons(data)
    if not keyword:
        return gameinfo.current_season(data)
    if keyword.isdigit():
        index = int(keyword) - 1
        return options[index][:2] if 0 <= index < len(options) else None
    for sid, name, _current in options:
        if keyword.lower() in name.lower():
            return sid, name
    return None


def _detail_player_to_match(
    event_id: str, battle: Dict[str, Any], player: Dict[str, Any]
) -> MatchRow:
    """把双方详情中的一名玩家转成近期战绩行。"""
    view = battle.get("playerGameView") or {}
    rounds = _int(view.get("roundsPlayed"))
    own_rounds = _int(view.get("roundsWon"))
    other_rounds = max(rounds - own_rounds, 0)
    same_team = str(player.get("teamId") or "") == str(view.get("playerTeamId") or "")
    won_rounds, lost_rounds = (
        (own_rounds, other_rounds) if same_team else (other_rounds, own_rounds)
    )
    score = _num(player.get("statsScore"))
    rounds_played = _int(player.get("statsRoundsPlayed")) or rounds
    aid = str(player.get("characterId") or "")
    tier = _int(player.get("competitiveTierAfter") or player.get("competitiveTier"))
    rr = player.get("competitiveTierRankedRatingEarned")
    return MatchRow(
        event_id=event_id,
        won=bool(_int(player.get("wonMatch"))),
        score1=won_rounds,
        score2=lost_rounds,
        kills=_int(player.get("statsKills")),
        deaths=_int(player.get("statsDeaths")),
        assists=_int(player.get("statsAssists")),
        acs=score / rounds_played if rounds_played else 0,
        agent_id=aid,
        agent_name=names.agent_name(aid),
        map_id=str(view.get("mapId") or ""),
        mode_key=str(view.get("queueId") or ""),
        rank_tier_after=tier or None,
        rr_earned=_int(rr) if rr not in (None, "") else None,
        is_match_mvp=bool(_int(player.get("isMatchMvp"))),
        first_kills=_int(player.get("firstKillCount")),
        start_time_ms=_int(view.get("gameStartMillis")),
        role_id=str(player.get("subject") or ""),
    )


async def _find_subject(
    client: WegameClient, me: WegameRole, keyword: str
) -> Tuple[Optional[WegameRole], List[MatchRow]]:
    """从本人近期 10 局解析昵称到 Subject，并保留可访问的共同对局。"""
    query = keyword.strip().lower()
    if query == me.name.lower():
        return me, []
    found: Optional[WegameRole] = None
    shared: List[MatchRow] = []
    rows = await client.get_battle_list(me.subject, size=10)
    for row in rows:
        event_id = _event_id(row)
        if not event_id:
            continue
        try:
            detail = await client.get_battle_detail(event_id, me.subject)
        except WegameError:
            continue
        battle = detail.get("battle_detail") or {}
        players = battle.get("players") or []
        for player in players:
            name = str(player.get("name") or "")
            if query == name.lower() or query in name.lower():
                subject = str(player.get("subject") or "")
                if subject and (found is None or found.subject == subject):
                    if found is None:
                        found = WegameRole(subject=subject, name=name)
                        try:
                            resolved = await client.get_role_info(subject)
                            if not resolved.name:
                                resolved.name = name
                            found = resolved
                        except WegameError:
                            pass
                    shared.append(_detail_player_to_match(event_id, battle, player))
                if found:
                    break
        await asyncio.sleep(0.03)
    return found, shared


async def _do_stats(matcher, user_id: str, keyword: str) -> None:
    client = await _client_or_hint(matcher, user_id)
    if client is None:
        return
    try:
        me = await _role(matcher, client)
        if me is None:
            return
        target = me
        shared_rows: List[MatchRow] = []
        shared_only = False
        if keyword:
            target, shared_rows = await _find_subject(client, me, keyword)
            if target is None:
                await matcher.finish(
                    f"近期 10 局双方名单中找不到「{keyword}」。\n"
                    "WeGame 网页接口以 Subject 查询，昵称#ID 目前没有公开的官方换 Subject 接口。",
                    reply_message=True,
                )
                return
        try:
            battles = await client.get_battle_list(target.subject, size=6)
            rows = [_battle_to_match(row) for row in battles[:6]]
        except WegameError as e:
            if keyword and "not allow access" in str(e).lower() and shared_rows:
                rows = shared_rows[:6]
                shared_only = True
            else:
                raise
        if not rows:
            await matcher.finish("近期没有可查对局", reply_message=True)
            return
        season_summary = None
        if target.subject == me.subject:
            try:
                data = await gameinfo.load_gameinfo()
                picked = _pick_season(data, "")
                if picked:
                    raw = await client.get_battle_report(picked[0], me.subject)
                    candidate = _season_view(raw, picked[1])
                    if candidate["games"]:
                        season_summary = candidate
            except WegameError:
                logger.warning("当前赛季摘要获取失败，使用近期对局聚合数据")
        icons = await _agent_images([row.agent_id for row in rows])
        image = build_stats_image(
            PlayerBrief(
                role_id=target.subject,
                name=target.name + ("（共同对局）" if shared_only else ""),
            ),
            rows,
            icons,
            season_summary,
        )
        await matcher.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(matcher, e, "战绩获取失败")
    except Exception as e:
        logger.error(f"战绩卡片生成失败: {e}")
        await matcher.finish("战绩卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()


@stats_cmd.handle()
async def _handle_stats(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    await _do_stats(stats_cmd, event.get_user_id(),
                    str(args[0]).strip() if args and args[0] else "")


@stats_alias_cmd.handle()
async def _handle_stats_alias(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    await _do_stats(stats_alias_cmd, event.get_user_id(),
                    str(args[0]).strip() if args and args[0] else "")


@teammate_cmd.handle()
async def _handle_teammates(event: MessageEvent):
    client = await _client_or_hint(teammate_cmd, event.get_user_id())
    if client is None:
        return
    try:
        me = await _role(teammate_cmd, client)
        if me is None:
            return
        live_event = await client.find_live_event_id()
        event_id = live_event
        source = "live"
        if not event_id:
            rows = await client.get_battle_list(me.subject, size=1)
            event_id = _event_id(rows[0]) if rows else ""
            source = "recent"
        if not event_id:
            await teammate_cmd.finish("没有找到进行中或最近一局对局", reply_message=True)
            return
        try:
            detail = await client.get_battle_detail(event_id, me.subject)
        except WegameError as e:
            if e.auth_invalid or not live_event:
                raise
            rows = await client.get_battle_list(me.subject, size=1)
            fallback = _event_id(rows[0]) if rows else ""
            if not fallback:
                raise
            event_id = fallback
            detail = await client.get_battle_detail(event_id, me.subject)
            battle_row = (
                rows[0].get("battle")
                if isinstance(rows[0].get("battle"), dict) else rows[0]
            )
            source = "recent" if _int(battle_row.get("isCompleted"), 1) else "live"
        battle = detail.get("battle_detail") or {}
        players = battle.get("players") or []
        if not players and live_event:
            rows = await client.get_battle_list(me.subject, size=1)
            fallback = _event_id(rows[0]) if rows else ""
            if fallback:
                event_id = fallback
                detail = await client.get_battle_detail(fallback, me.subject)
                battle = detail.get("battle_detail") or {}
                players = battle.get("players") or []
                source = "recent"
        if not players:
            await teammate_cmd.finish("当前对局详情尚未同步，请开局后稍等片刻再发送「瓦队友」", reply_message=True)
            return
        try:
            battle["rounds"] = await client.get_round_list(event_id)
        except WegameError as e:
            logger.warning(f"逐回合数据获取失败，使用比分汇总兜底: {e}")
        icons = await _agent_images([
            str(player.get("characterId") or "") for player in players
        ])
        image = build_battle_detail_image(
            battle, me.subject, live=(source == "live"), agent_icons=icons,
        )
        await teammate_cmd.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(teammate_cmd, e, "双方详情获取失败")
    except Exception as e:
        logger.error(f"双方详情卡片生成失败: {e}")
        await teammate_cmd.finish("双方详情卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()


def _season_view(raw: Dict[str, Any], display_name: str) -> Dict[str, Any]:
    season = raw.get("season") or {}
    stats = season.get("stats") or {}
    overview = season.get("overview") or {}
    return {
        "name": display_name or season.get("name") or "当前赛季",
        "games": _int(stats.get("gamenum")), "wins": _int(stats.get("winnum")),
        "winrate": _num(stats.get("winrate") or overview.get("win_rate")),
        "kda": _num(overview.get("kda") or stats.get("statsKda")),
        "acs": _num(stats.get("scoreAvg")), "damage_avg": _num(stats.get("damageAvg")),
        "head_shot_rate": _num(overview.get("head_shot_rate")),
        "kast": _num(season.get("kast")), "kills_avg": _num(stats.get("killsAvg")),
        "time_hours": _num(overview.get("gametime")) / 3600,
        "first_kills": _int(stats.get("firstBlood")),
        "five_kills": _int(stats.get("fiveKills")),
        "flawless": _int(stats.get("flawlessCount")),
        "tier": _int(overview.get("itier_rank")),
        "tier_max": _int(overview.get("max_itier_rank")),
    }


def _hero_views(items: List[Dict[str, Any]], data: Dict[str, Any]) -> List[Dict[str, Any]]:
    meta = gameinfo.agent_meta(data)
    out = []
    for item in items:
        aid = str(item.get("characterId") or "")
        info = gameinfo.agent_lookup(meta, aid)
        out.append({
            "name": str(item.get("name") or info.get("name") or names.agent_name(aid)),
            "games": _int(item.get("gamenum")), "win_rate": _num(item.get("win_rate")),
            "kda": _num(item.get("kda")), "acs": _num(item.get("scoreAvg")),
            "avatar_url": info.get("avatar_url", ""), "color": info.get("color", ""),
        })
    return out


@report_cmd.handle()
async def _handle_report(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    client = await _client_or_hint(report_cmd, event.get_user_id())
    if client is None:
        return
    keyword = str(args[0]).strip() if args and args[0] else ""
    try:
        me = await _role(report_cmd, client)
        data = await gameinfo.load_gameinfo()
        picked = _pick_season(data, keyword)
        if not picked:
            await report_cmd.finish(f"没找到赛季「{keyword}」", reply_message=True)
            return
        sid, season_name = picked
        raw = await client.get_battle_report(sid, me.subject)
        season = _season_view(raw, season_name)
        if not season["games"]:
            await report_cmd.finish(f"「{season_name}」暂无你的赛季数据", reply_message=True)
            return
        heroes = _hero_views((raw.get("season") or {}).get("characterStats") or [], data)
        radar = await client.get_radar(sid, me.subject)
        radar_headshot = _num(radar.get("accurate_defeat_rate"))
        if radar_headshot > 1:
            radar_headshot /= 100
        radar_view = {
            "kda": _num(radar.get("kda")),
            "head_shot_rate": radar_headshot,
            "acs": _num(radar.get("acs")),
            "damage_avg": _num(radar.get("round_average_hurt")),
        }
        urls = [h["avatar_url"] for h in heroes[:5] if h.get("avatar_url")]
        image = build_report_image(
            PlayerBrief(role_id=me.subject, name=me.name), season, heroes,
            radar_view, gameinfo.agent_meta(data), await fetch_images(urls),
        )
        await report_cmd.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(report_cmd, e, "战报获取失败")
    except Exception as e:
        logger.error(f"战报卡片生成失败: {e}")
        await report_cmd.finish("战报卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()


@map_cmd.handle()
async def _handle_map(event: MessageEvent, args: Tuple[Any, ...] = RegexGroup()):
    client = await _client_or_hint(map_cmd, event.get_user_id())
    if client is None:
        return
    keyword = str(args[0]).strip() if args and args[0] else ""
    try:
        me = await _role(map_cmd, client)
        data = await gameinfo.load_gameinfo()
        picked = _pick_season(data, keyword)
        if not picked:
            await map_cmd.finish(f"没找到赛季「{keyword}」", reply_message=True)
            return
        sid, season_name = picked
        rows = await client.get_maps(sid, me.subject)
        meta_maps, meta_agents = gameinfo.map_meta(data), gameinfo.agent_meta(data)
        for row in rows:
            mid = str(row.get("id") or "")
            info = meta_maps.get(gameinfo._norm_map_key(mid), {})
            row["name"], row["preview_url"] = info.get("name", ""), info.get("preview_url", "")
            champion = row.get("champion") or {}
            ai = gameinfo.agent_lookup(meta_agents, str(champion.get("id") or ""))
            champion.update({"name": ai.get("name", ""), "avatar_url": ai.get("avatar_url", ""),
                             "color": ai.get("color", "")})
            row["champion"] = champion
        urls = [r.get("preview_url") for r in rows if r.get("preview_url")]
        urls += [(r.get("champion") or {}).get("avatar_url") for r in rows
                 if (r.get("champion") or {}).get("avatar_url")]
        image = build_map_image(PlayerBrief(role_id=me.subject, name=me.name),
                                season_name, rows, meta_maps, meta_agents,
                                await fetch_images(urls))
        await map_cmd.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(map_cmd, e, "地图数据获取失败")
    except Exception as e:
        logger.error(f"地图卡片生成失败: {e}")
        await map_cmd.finish("地图卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()


@champions_cmd.handle()
async def _handle_champions(event: MessageEvent):
    client = await _client_or_hint(champions_cmd, event.get_user_id())
    if client is None:
        return
    try:
        me = await _role(champions_cmd, client)
        data = await gameinfo.load_gameinfo()
        heroes = _hero_views(await client.get_champions(me.subject), data)
        if not heroes:
            await champions_cmd.finish("当前赛季没有英雄数据", reply_message=True)
            return
        urls = [h["avatar_url"] for h in heroes if h.get("avatar_url")]
        image = build_champions_image(
            PlayerBrief(role_id=me.subject, name=me.name), heroes,
            gameinfo.agent_meta(data), await fetch_images(urls),
        )
        await champions_cmd.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(champions_cmd, e, "英雄池数据获取失败")
    except Exception as e:
        logger.error(f"英雄池卡片生成失败: {e}")
        await champions_cmd.finish("英雄池卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()


@friends_cmd.handle()
async def _handle_friends(event: MessageEvent):
    client = await _client_or_hint(friends_cmd, event.get_user_id())
    if client is None:
        return
    try:
        me = await _role(friends_cmd, client)
        sid, _name = await gameinfo.resolve_season(client)
        raw = await client.get_friends(sid, me.subject)
        friends = []
        for row in raw:
            games = _int(row.get("battle_count")); wins = _int(row.get("win_count"))
            friends.append({
                "nickname": row.get("nickname") or "未知玩家", "battle_count": games,
                "win_count": wins, "win_rate": wins / games if games else 0,
                "avg_acs": _num(row.get("acs")), "total_secs": _int(row.get("time_up_time")),
                "score": _int(row.get("battle_score")), "tier": _int(row.get("tier")),
            })
        if not friends:
            await friends_cmd.finish("当前赛季没有识别到开黑队友", reply_message=True)
            return
        image = build_friends_image(PlayerBrief(role_id=me.subject, name=me.name), friends)
        await friends_cmd.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(friends_cmd, e, "开黑数据获取失败")
    except Exception as e:
        logger.error(f"开黑卡片生成失败: {e}")
        await friends_cmd.finish("开黑卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()


@weapon_cmd.handle()
async def _handle_weapon(event: MessageEvent):
    client = await _client_or_hint(weapon_cmd, event.get_user_id())
    if client is None:
        return
    try:
        me = await _role(weapon_cmd, client)
        weapons = await client.get_weapons()
        if not weapons:
            await weapon_cmd.finish("没有武器击杀数据", reply_message=True)
            return
        meta = gameinfo.weapon_meta(await gameinfo.load_gameinfo())
        urls: List[str] = []
        for weapon in weapons:
            raw_name = str(weapon.get("name") or "")
            info = meta.get(raw_name.lower()) or {}
            weapon["name"] = info.get("name") or (
                raw_name if len(raw_name) < 24 else f"武器-{raw_name[:8]}"
            )
            weapon["weapon_type"] = info.get("type") or str(weapon.get("type") or "")
            weapon["picture_url"] = info.get("picture_url") or ""
            if weapon["picture_url"]:
                urls.append(weapon["picture_url"])
        pictures = await fetch_images(urls)
        for weapon in weapons:
            weapon["image_path"] = pictures.get(str(weapon.get("picture_url") or ""), "")
        image = build_weapon_image(
            PlayerBrief(role_id=me.subject, name=me.name), weapons,
        )
        await weapon_cmd.finish(MessageSegment.image(image), reply_message=True)
    except MatcherException:
        raise
    except WegameError as e:
        await _fail(weapon_cmd, e, "武器数据获取失败")
    except Exception as e:
        logger.error(f"武器卡片生成失败: {e}")
        await weapon_cmd.finish("武器卡片生成失败，请稍后重试", reply_message=True)
    finally:
        await client.close()
