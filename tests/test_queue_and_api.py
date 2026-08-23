from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from fbem.bridge import admin_store, capture_store
from fbem.bridge.server import app


class TestQueueAndApi(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prev_home = admin_store.home_dir
        admin_store.home_dir = lambda: Path(self.temp_dir.name)
        admin_store.init_db()
        self.client = TestClient(app)

    def tearDown(self):
        admin_store.home_dir = self.prev_home
        self.temp_dir.cleanup()

    def test_account_crud_and_idempotent_job(self):
        # Create Account
        res = self.client.post("/api/accounts", json={
            "name": "Fanpage Test",
            "facebookId": "1000123456",
            "extensionId": "ext-test-1",
            "accountType": "page",
        })
        self.assertEqual(res.status_code, 200)
        account = res.json()["account"]
        self.assertEqual(account["name"], "Fanpage Test")

        # List Accounts
        res = self.client.get("/api/accounts")
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        self.assertEqual(len(items), 1)

        # Create Job with Idempotency Key
        idemp_key = "test_video_hash_999"
        res = self.client.post("/api/jobs", json={
            "accountId": account["id"],
            "kind": "post_reel",
            "input": {"videoUrl": "http://127.0.0.1:47102/local-video?name=clip.mp4", "caption": "Hello #viral"},
            "idempotencyKey": idemp_key,
        })
        self.assertEqual(res.status_code, 200)
        job1 = res.json()["job"]
        self.assertEqual(job1["status"], "queued")
        self.assertEqual(job1["idempotency_key"], idemp_key)

        # Re-send same Idempotency Key (should return existing job, not create a duplicate)
        res = self.client.post("/api/jobs", json={
            "accountId": account["id"],
            "kind": "post_reel",
            "input": {"videoUrl": "http://127.0.0.1:47102/local-video?name=clip.mp4", "caption": "Hello #viral"},
            "idempotencyKey": idemp_key,
        })
        self.assertEqual(res.status_code, 200)
        job2 = res.json()["job"]
        self.assertEqual(job1["id"], job2["id"])

        # Check jobs list
        res = self.client.get("/api/jobs")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["items"]), 1)

        # Cancel Job
        res = self.client.post(f"/api/jobs/{job1['id']}/cancel")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

        # Retry Job
        res = self.client.post(f"/api/jobs/{job1['id']}/retry")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

        # Stats
        res = self.client.get("/api/stats")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["accounts"], 1)


if __name__ == "__main__":
    unittest.main()
