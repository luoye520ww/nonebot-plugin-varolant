import json
import time
import unittest
from unittest.mock import AsyncMock

import nonebot

nonebot.init()

from nonebot_plugin_varolant.core.wegame import WegameClient


class FakeResponse:
    status = 200

    def __init__(self):
        self.cookies = {"tgp_ticket": type("Cookie", (), {"value": "new-wt"})()}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def read(self):
        return json.dumps({
            "code": 0,
            "data": {
                "result": 0,
                "is_timeout": 0,
                "ct_info": {
                    "ct": "new-ct",
                    "refresh_wt_span": 1800,
                    "refresh_ct_span": 604800,
                },
            },
        }).encode()


class FakeSession:
    closed = False

    def __init__(self):
        self.response = FakeResponse()

    def post(self, *_args, **_kwargs):
        return self.response

    async def close(self):
        self.closed = True


class WegameAuthTest(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_rotates_and_persists_tickets(self):
        saved = AsyncMock()
        client = WegameClient({
            "tgp_id": "user",
            "tgp_ticket": "old-wt",
            "ct": "old-ct",
            "auth_refreshed_at": 0,
        }, on_account_update=saved)
        session = FakeSession()
        client._session = session

        self.assertTrue(await client.refresh_ticket())
        self.assertEqual(client.account["tgp_ticket"], "new-wt")
        self.assertEqual(client.account["ct"], "new-ct")
        self.assertGreater(client.account["auth_refreshed_at"], 0)
        saved.assert_awaited_once()

    def test_refresh_due_before_short_web_ticket_expires(self):
        client = WegameClient({
            "tgp_ticket": "wt",
            "ct": "ct",
            "refresh_wt_span": 1800,
            "auth_refreshed_at": int(time.time()) - 1201,
        })

        self.assertTrue(client._refresh_due())


if __name__ == "__main__":
    unittest.main()
