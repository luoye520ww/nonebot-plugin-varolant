import unittest

import nonebot

nonebot.init()

from nonebot_plugin_varolant.core import gameinfo, names
from nonebot_plugin_varolant.core.render import _battle_team_state
from nonebot_plugin_varolant.matchers.stats import _battle_to_match, _detail_player_to_match


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
        }

        self.assertEqual(names.agent_name("569fdd95"), "贤者")
        self.assertEqual(names.agent_name("bb2a4828"), "霓虹")
        self.assertEqual(gameinfo.agent_lookup(meta, "569fdd95")["name"], "贤者")
        self.assertEqual(gameinfo.agent_lookup(meta, "bb2a4828")["name"], "霓虹")

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


if __name__ == "__main__":
    unittest.main()
