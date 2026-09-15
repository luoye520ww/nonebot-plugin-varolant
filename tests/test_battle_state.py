import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nonebot

nonebot.init(driver="~none")

from nonebot_plugin_varolant.core import gameinfo, names, render
from nonebot_plugin_varolant.core.render import (
    _battle_team_state,
    _load_font,
    _truncate_text,
    build_battle_detail_image,
    build_weapon_image,
)
from nonebot_plugin_varolant.core.mval import PlayerBrief
from PIL import Image, ImageDraw
from nonebot_plugin_varolant.matchers.stats import (
    _battle_to_match,
    _detail_player_to_match,
    _hero_views,
)


def round_row(number: int, won: bool, attack: bool, result: str = "Elimination"):
    return {
        "roundNum": number,
        "teamId": "Blue",
        "isRoundWon": int(won),
        "isAttack": int(attack),
        "roundResultCode": result,
    }


class BattleTeamStateTest(unittest.TestCase):
    def test_agent_text_matches_sage_and_neon_icons(self):
        meta = {
            "569fdd95-4d10-43ab-ca70-79becc718b46": {"name": "贤者"},
            "bb2a4828-46eb-8cd1-e765-15848195d751": {"name": "霓虹"},
            "eb93336a-449b-9c1b-0a54-a891f7921d69": {"name": "不死鸟"},
            "1dbf2edd-4729-0984-3115-daa5eed44993": {"name": "暮蝶"},
            "add6443a-41bd-e414-f6ad-e58d267f4e95": {"name": "捷风"},
            "9f0d8ba9-4140-b941-57d3-a7ad57c6b417": {"name": "炼狱"},
        }

        expected = {
            "569fdd95": "贤者", "bb2a4828": "霓虹", "eb93336a": "不死鸟",
            "1dbf2edd": "暮蝶", "add6443a": "捷风", "9f0d8ba9": "炼狱",
        }
        for agent_id, label in expected.items():
            self.assertEqual(names.agent_name(agent_id), label)
            self.assertEqual(gameinfo.agent_lookup(meta, agent_id)["name"], label)

        unknown = _hero_views(
            [{"characterId": "deadbeef", "name": "接口特工"}],
            {"agents": []},
        )
        self.assertEqual(unknown[0]["name"], "接口特工")

    def test_acs_always_uses_total_score_per_round(self):
        recent = _battle_to_match({
            "roundsPlayed": 7,
            "roundsWon": 2,
            "statsScore": 335,
        })
        detail = _detail_player_to_match(
            "event",
            {"playerGameView": {"roundsPlayed": 7, "roundsWon": 2}},
            {"statsScore": 335, "statsRoundsPlayed": 7},
        )

        self.assertAlmostEqual(recent.acs, 335 / 7)
        self.assertAlmostEqual(detail.acs, 335 / 7)

        exact = _battle_to_match({
            "roundsPlayed": 10, "roundsWon": 4, "statsScore": 1511,
        })
        self.assertEqual(exact.acs, 151.1)

    def test_surrender_uses_played_score_and_final_side(self):
        rows = [
            round_row(index, index in {7, 8, 9, 11}, index >= 12)
            for index in range(13)
        ]
        rows.extend(round_row(index, False, True, "Surrendered") for index in range(13, 17))

        scores, sides, played = _battle_team_state(
            rows, ["Blue", "Red"], {"playerTeamId": "Blue"},
        )

        self.assertEqual(scores, {"Blue": 4, "Red": 9})
        self.assertEqual(sides, {"Blue": "Attackers", "Red": "Defenders"})
        self.assertEqual(len(played), 13)

    def test_swiftplay_follows_returned_side_swap(self):
        rows = [
            round_row(index, index in {4, 5}, index >= 4)
            for index in range(7)
        ]

        scores, sides, _ = _battle_team_state(
            rows, ["Blue", "Red"], {"playerTeamId": "Blue"},
        )

        self.assertEqual(scores, {"Blue": 2, "Red": 5})
        self.assertEqual(sides, {"Blue": "Attackers", "Red": "Defenders"})

    def test_competitive_overtime_follows_each_returned_round(self):
        blue_wins = set(range(7)) | set(range(12, 18)) | {25}
        rows = []
        for index in range(26):
            if index < 12:
                attack = False
            elif index < 24:
                attack = True
            else:
                attack = index % 2 == 1
            rows.append(round_row(index, index in blue_wins, attack))

        scores, sides, _ = _battle_team_state(
            rows, ["Blue", "Red"], {"playerTeamId": "Blue"},
        )

        self.assertEqual(scores, {"Blue": 14, "Red": 12})
        self.assertEqual(sides, {"Blue": "Attackers", "Red": "Defenders"})

    def test_missing_rounds_uses_view_side_and_safe_acs(self):
        battle = {
            "playerGameView": {
                "playerTeamId": "Blue", "roundsPlayed": 10, "roundsWon": 4,
                "isAttack": 1,
            },
            "players": [
                {"teamId": "Blue", "subject": "me", "name": "A",
                 "statsScore": 1511, "statsRoundsPlayed": 0},
                {"teamId": "Red", "subject": "other", "name": "B",
                 "statsScore": 1000, "statsRoundsPlayed": 0},
            ],
        }
        scores, sides, _ = _battle_team_state([], ["Blue", "Red"], battle["playerGameView"])
        self.assertEqual(scores, {"Blue": 4, "Red": 6})
        self.assertEqual(sides, {"Blue": "Attackers", "Red": "Defenders"})
        self.assertTrue(build_battle_detail_image(battle, "me"))

    def test_pixel_truncation_and_missing_weapon_icon_render(self):
        image = Image.new("RGB", (500, 100), "white")
        draw = ImageDraw.Draw(image)
        font = _load_font(24)
        value = _truncate_text(draw, "很长很长的玩家昵称#123456789", font, 120)
        bbox = draw.textbbox((0, 0), value, font=font)
        self.assertLessEqual(bbox[2] - bbox[0], 120)
        rendered = build_weapon_image(
            PlayerBrief(name="很长很长的玩家昵称#123456789"),
            [{"name": "未知的超长武器名称", "kill": 1, "image_path": "MISSING"}],
        )
        self.assertTrue(rendered.startswith(b"\xff\xd8"))

    def test_weapon_meta_can_resolve_chinese_api_name(self):
        meta = gameinfo.weapon_meta({"weapons": [{
            "guid": "weapon-guid",
            "name": {"cn": "狂徒", "en": "Vandal"},
            "type": {"cn": "步枪", "en": "Rifle"},
            "picture_url": "https://example.invalid/vandal.png",
        }]})
        self.assertEqual(meta["狂徒"]["name"], "狂徒")
        self.assertEqual(
            meta["狂徒"]["picture_url"],
            "https://example.invalid/vandal.png",
        )

    def test_all_matcher_messages_quote_the_trigger(self):
        matcher_dir = Path(__file__).parents[1] / "nonebot_plugin_varolant" / "matchers"
        missing = []
        for path in matcher_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"finish", "send"}:
                    continue
                if not any(keyword.arg == "reply_message" for keyword in node.keywords):
                    missing.append(f"{path.name}:{node.lineno}")
        self.assertEqual(missing, [])


class ShopRenderTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_remote_assets_still_generate_a_card(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.object(render, "temp_user_dir", side_effect=lambda uid: root / uid),
                patch.object(
                    render,
                    "temp_user_file",
                    side_effect=lambda uid, name: root / uid / name,
                ),
            ):
                image = await render.build_shop_image(
                    "100",
                    {"userId": "app-user", "tid": "tid", "nickname": "测试玩家"},
                    [{"goods_id": "1", "goods_name": "无素材商品", "rmb_price": 875}],
                )
        self.assertIsNotNone(image)
        self.assertTrue(image.startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
