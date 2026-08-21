from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fbem.bridge import admin_store, capture_store
from fbem.bridge.connection_registry import ConnectionRegistry


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))


class MultiExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_callbacks_stay_with_their_extension(self) -> None:
        registry = ConnectionRegistry()
        first = registry.register("extension-a", FakeWebSocket())
        second = registry.register("extension-b", FakeWebSocket())

        async def roundtrip(session):
            task = asyncio.create_task(session.send("get_identity", {}, timeout=1))
            await asyncio.sleep(0)
            request_id = session.ws.sent[-1]["id"]
            session.resolve({"id": request_id, "status": 200, "data": session.extension_id})
            return await task

        self.assertEqual(await asyncio.gather(roundtrip(first), roundtrip(second)), [
            {"id": first.ws.sent[-1]["id"], "status": 200, "data": "extension-a"},
            {"id": second.ws.sent[-1]["id"], "status": 200, "data": "extension-b"},
        ])

    def test_templates_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            previous = capture_store._CAPTURES_DIR
            capture_store._CAPTURES_DIR = Path(temp)
            try:
                reel = {
                    "kind": "graphql",
                    "friendlyName": "ComposerStoryCreateMutation",
                    "body": {"value": "variables=%7B%22input%22%3A%7B%22attachments%22%3A%5B%7B%22video%22%3A%7B%7D%7D%5D%7D%7D"},
                }
                capture_store.save_capture(reel, "extension-a")
                capture_store.save_capture({"kind": "other"}, "extension-b")
                self.assertIn("graphql", capture_store.load_template("extension-a") or {})
                self.assertNotIn("graphql", capture_store.load_template("extension-b") or {})
            finally:
                capture_store._CAPTURES_DIR = previous

    def test_queue_never_claims_two_jobs_for_one_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            previous = admin_store.home_dir
            admin_store.home_dir = lambda: Path(temp)
            try:
                admin_store.init_db()
                first = admin_store.save_account({"name": "A", "facebookId": "1", "extensionId": "ext-a"})
                second = admin_store.save_account({"name": "B", "facebookId": "2", "extensionId": "ext-b"})
                admin_store.create_job(first, "get_identity", {})
                admin_store.create_job(first, "get_identity", {})
                admin_store.create_job(second, "get_identity", {})
                claimed = [admin_store.claim_next_job(), admin_store.claim_next_job()]
                self.assertNotEqual(claimed[0]["account_id"], claimed[1]["account_id"])
            finally:
                admin_store.home_dir = previous

    def test_retry_and_safe_delete_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            previous = admin_store.home_dir
            admin_store.home_dir = lambda: Path(temp)
            try:
                admin_store.init_db()
                account = admin_store.save_account({"name": "A", "facebookId": "1", "extensionId": "ext-a"})
                job = admin_store.create_job(account, "get_identity", {})
                deleted, error = admin_store.delete_account(account["id"])
                self.assertFalse(deleted)
                self.assertEqual(error, "account_has_active_jobs")
                admin_store.finish_job(job["id"], "failed", error="test")
                self.assertTrue(admin_store.retry_job(job["id"]))
                self.assertEqual(admin_store.get_row("jobs", job["id"])["status"], "queued")
                self.assertTrue(admin_store.cancel_job(job["id"]))
                self.assertEqual(admin_store.delete_account(account["id"]), (True, None))
            finally:
                admin_store.home_dir = previous


if __name__ == "__main__":
    unittest.main()
