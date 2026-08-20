import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from nonebot_plugin_varolant.core import database, mval, store, wegame
from nonebot_plugin_varolant.core.wegame import WegameClient


class FakeResponse:
    def __init__(self, *, ct="new-ct", ticket="new-wt", status=200):
        self.status = status
        self.cookies = (
            {"tgp_ticket": type("Cookie", (), {"value": ticket})()}
            if ticket else {}
        )
        self.ct = ct

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
                    "ct": self.ct,
                    "refresh_wt_span": 1800,
                    "refresh_ct_span": 604800,
                },
            },
        }).encode()


class FakeSession:
    def __init__(self, response=None, counter=None):
        self.closed = False
        self.response = response or FakeResponse()
        self.counter = counter

    def post(self, *_args, **_kwargs):
        if self.counter is not None:
            self.counter[0] += 1
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True
        return False

    async def close(self):
        self.closed = True


class FakeStoreResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    async def json(self, **_kwargs):
        return self.payload


class FakeStoreSession:
    def __init__(self, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        return self.responses.pop(0)


class WegameAuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        wegame._LATEST_ACCOUNTS.clear()
        wegame._REFRESH_LOCKS.clear()

    async def test_refresh_rotates_and_persists_tickets(self):
        saved = AsyncMock(return_value=True)
        client = WegameClient({
            "tgp_id": "user",
            "tgp_ticket": "old-wt",
            "ct": "old-ct",
            "auth_refreshed_at": 0,
        }, on_account_update=saved)
        session = FakeSession()
        client._session = session
        client._validate_candidate = AsyncMock(return_value=True)

        self.assertTrue(await client.refresh_ticket())
        self.assertEqual(client.account["tgp_ticket"], "new-wt")
        self.assertEqual(client.account["ct"], "new-ct")
        self.assertGreater(client.account["auth_refreshed_at"], 0)
        saved.assert_awaited_once()

    async def test_incomplete_refresh_keeps_old_tickets(self):
        saved = AsyncMock(return_value=True)
        client = WegameClient({
            "tgp_id": "user", "tgp_ticket": "old-wt", "ct": "old-ct",
            "auth_refreshed_at": 0,
        }, on_account_update=saved, account_key="missing-ticket")
        client._session = FakeSession(FakeResponse(ticket=""))

        self.assertFalse(await client.refresh_ticket())
        self.assertEqual(client.account["ct"], "old-ct")
        self.assertEqual(client.account["tgp_ticket"], "old-wt")
        self.assertEqual(saved.await_count, 1)
        self.assertEqual(saved.await_args.args[0]["last_auth_error"], "incomplete_refresh")

    async def test_readback_failure_does_not_commit_candidate(self):
        saved = AsyncMock(return_value=True)
        client = WegameClient({
            "tgp_id": "user", "tgp_ticket": "old-wt", "ct": "old-ct",
            "auth_refreshed_at": 0,
        }, on_account_update=saved, account_key="bad-readback")
        client._session = FakeSession()
        client._validate_candidate = AsyncMock(return_value=False)

        self.assertFalse(await client.refresh_ticket())
        self.assertEqual(client.account["ct"], "old-ct")
        self.assertEqual(saved.await_args.args[0]["last_auth_error"], "readback_failed")

    async def test_empty_wegame_readback_is_rejected(self):
        class EmptyResponse(FakeResponse):
            async def read(self):
                return b"{}"

        client = WegameClient({"tgp_ticket": "ticket", "ct": "ct"})
        with patch.object(wegame.aiohttp, "ClientSession", return_value=FakeSession(EmptyResponse())):
            self.assertFalse(await client._validate_candidate(client.account))

    async def test_ten_concurrent_clients_refresh_once(self):
        counter = [0]
        saved = AsyncMock(return_value=True)
        account = {
            "tgp_id": "user", "tgp_ticket": "old-wt", "ct": "old-ct",
            "auth_refreshed_at": 0,
        }
        clients = [
            WegameClient(account, on_account_update=saved, account_key="same-user")
            for _ in range(10)
        ]
        for client in clients:
            client._session = FakeSession(counter=counter)
            client._validate_candidate = AsyncMock(return_value=True)

        results = await __import__("asyncio").gather(
            *(client.refresh_ticket() for client in clients)
        )
        self.assertEqual(results, [True] * 10)
        self.assertEqual(counter[0], 1)
        self.assertEqual(saved.await_count, 1)

    def test_refresh_due_before_short_web_ticket_expires(self):
        client = WegameClient({
            "tgp_ticket": "wt",
            "ct": "ct",
            "refresh_wt_span": 1800,
            "auth_refreshed_at": int(time.time()) - 1300,
        })

        self.assertTrue(client._refresh_due())

    async def test_live_event_lookup_starts_with_match_info(self):
        client = WegameClient({"tgp_ticket": "ticket", "ct": "ct"})
        client.get_match_info = AsyncMock(return_value={"eventId": "event"})
        client.get_user_game_info = AsyncMock(return_value={})
        client.get_my_room = AsyncMock(return_value={})
        self.assertEqual(await client.find_live_event_id(), "event")
        client.get_match_info.assert_awaited_once()
        client.get_user_game_info.assert_not_awaited()
        client.get_my_room.assert_not_awaited()


class DatabaseAndMvalAuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp.name)
        self.data_patch = patch.object(database, "data_dir", return_value=self.data_path)
        self.data_patch.start()
        mval._auth_locks.clear()

    async def asyncTearDown(self):
        self.data_patch.stop()
        self.temp.cleanup()

    async def test_domains_are_isolated_and_stale_wegame_write_is_rejected(self):
        await database.save_user_config(
            "100", "app-user", "app-tid", access_token="app-token",
            auth={"ct": "app-ct"},
        )
        self.assertTrue(await database.save_wegame_config("100", {
            "tgp_ticket": "wg-ticket", "ct": "wg-ct",
        }))
        self.assertFalse(await database.save_wegame_config("100", {
            "tgp_ticket": "stale", "ct": "stale-ct", "_expected_ct": "old-ct",
        }))
        app = await database.get_user_config("100")
        web = await database.get_wegame_config("100")
        self.assertEqual(app["tid"], "app-tid")
        self.assertEqual(app["ct"], "app-ct")
        self.assertEqual(web["tgp_ticket"], "wg-ticket")
        self.assertEqual(web["ct"], "wg-ct")

    async def test_corrupt_user_json_recovers_and_is_rewritten_atomically(self):
        broken = self.data_path / "100.json"
        broken.write_text("{broken", encoding="utf-8")
        self.assertIsNone(await database.get_user_config("100"))
        await database.save_wegame_config("100", {
            "tgp_ticket": "ticket", "ct": "ct",
        })
        data = json.loads(broken.read_text(encoding="utf-8"))
        self.assertEqual(data["wegame"]["tgp_ticket"], "ticket")
        self.assertFalse((self.data_path / "100.tmp").exists())

    async def test_legacy_sqlite_migrates_without_overwriting_json(self):
        db_path = self.data_path / "varolant.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "CREATE TABLE valo_users "
                "(user_id TEXT, userId TEXT, tid TEXT, nickname TEXT, auto_check INTEGER)"
            )
            connection.execute(
                "CREATE TABLE valo_watchlist "
                "(user_id TEXT, item_name TEXT, created_at TEXT)"
            )
            connection.execute(
                "INSERT INTO valo_users VALUES (?, ?, ?, ?, ?)",
                ("100", "app-user", "tid", "玩家", 1),
            )
            connection.execute(
                "INSERT INTO valo_watchlist VALUES (?, ?, ?)",
                ("100", "侦察力量 幻象", "2026-08-29"),
            )
            connection.commit()
        finally:
            connection.close()
        with patch.object(database, "_legacy_db_candidates", return_value=[db_path]):
            database._migrate_legacy_sqlite()
        migrated = json.loads((self.data_path / "100.json").read_text(encoding="utf-8"))
        self.assertEqual(migrated["accounts"][0]["userId"], "app-user")
        self.assertEqual(migrated["watchlist"][0]["item_name"], "侦察力量 幻象")
        self.assertTrue((self.data_path / "varolant.db.migrated").exists())

    async def test_mval_refresh_commits_only_after_store_readback(self):
        account = {
            "userId": "app-user", "tid": "old-tid", "ct": "old-ct",
            "openid": "openid", "access_token": "old-token", "acctype": "qc",
            "next_refresh_at": 0,
        }
        calls = [
            ({"result": 0, "data": {"ct_info": {
                "ct": "new-ct", "wt": "new-tid", "refresh_wt_span": 1800,
            }}}, {}, 200),
            ({"result": 0}, {"ctt": "ctt", "sk": "sk"}, 200),
            ({"result": 0, "data": {"access_token": "new-token"}}, {}, 200),
            ({"result": 0, "data": []}, {}, 200),
        ]
        save = AsyncMock(return_value=True)
        with (
            patch.object(database, "get_app_config", AsyncMock(return_value=account)),
            patch.object(database, "save_app_config", save),
            patch.object(mval, "_auth_post", AsyncMock(side_effect=calls)),
        ):
            self.assertTrue(await mval.refresh_app_account("100", account, force=True))
        candidate = save.await_args.args[2]
        self.assertEqual(candidate["ct"], "new-ct")
        self.assertEqual(candidate["tid"], "new-tid")
        self.assertEqual(candidate["access_token"], "new-token")
        self.assertFalse(candidate["needs_relogin"])

    async def test_mval_incomplete_refresh_keeps_old_credentials(self):
        account = {
            "userId": "app-user", "tid": "old-tid", "ct": "old-ct",
            "openid": "openid", "access_token": "old-token",
        }
        save = AsyncMock(return_value=True)
        with (
            patch.object(database, "get_app_config", AsyncMock(return_value=account)),
            patch.object(database, "save_app_config", save),
            patch.object(
                mval, "_auth_post",
                AsyncMock(return_value=({"result": 0, "data": {"ct_info": {
                    "ct": "new-ct",
                }}}, {}, 200)),
            ),
        ):
            self.assertFalse(await mval.refresh_app_account("100", account, force=True))
        saved_state = save.await_args.args[2]
        self.assertNotIn("tid", saved_state)
        self.assertNotIn("ct", saved_state)
        self.assertEqual(saved_state["last_auth_error"], "掌瓦票据刷新失败")
        self.assertFalse(saved_state["needs_relogin"])

    def test_mval_headers_use_login_account_type_and_empty_auth_is_invalid(self):
        headers = mval.build_mval_headers({
            "userId": "app-user", "tid": "tid", "acctype": "wx",
        })
        self.assertIn("acctype=wx", headers["cookie"])
        self.assertFalse(mval._auth_ok({}))
        self.assertFalse(mval._auth_invalid({"result": 500}, 500))
        self.assertTrue(mval._auth_invalid({"result": 1001}, 200))

    async def test_store_http_auth_error_refreshes_and_retries_once(self):
        responses = [
            FakeStoreResponse(401),
            FakeStoreResponse(200, {"result": 0, "data": {"list": []}}),
        ]
        refresh = AsyncMock(return_value=True)
        updated = {
            "userId": "app-user", "tid": "new-tid", "ct": "new-ct",
            "openid": "openid", "access_token": "token",
        }
        with (
            patch.object(
                store.aiohttp, "ClientSession",
                side_effect=lambda: FakeStoreSession(responses),
            ),
            patch.object(mval, "refresh_app_account", refresh),
            patch.object(database, "get_app_config", AsyncMock(return_value=updated)) as get_app,
        ):
            payload, error, invalid = await store.request_store_api(
                "100", {**updated, "tid": "old-tid", "ct": "old-ct"},
                max_retries=1,
            )
        self.assertEqual(payload["result"], 0)
        self.assertIsNone(error)
        self.assertFalse(invalid)
        refresh.assert_awaited_once()
        get_app.assert_awaited_once_with("100", "app-user")
        self.assertEqual(responses, [])


if __name__ == "__main__":
    unittest.main()
