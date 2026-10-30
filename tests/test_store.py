import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from core import ReminderStore, parse_due

NOW = 1900000000.0


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.db"
        self.store = ReminderStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **extra):
        return self.store.create(
            "owner", "想聊摄影", "chat", NOW + 60, "自己的想法", NOW, **extra
        )

    def test_model_owned_crud_is_scoped_and_persistent(self):
        item = self.create()
        restarted = ReminderStore(self.path)
        self.assertEqual(restarted.list("owner")[0]["id"], item["id"])
        with self.assertRaises(ValueError):
            self.store.change("other", item["id"], "delete", {}, NOW)
        updated = self.store.change(
            "owner", item["id"], "update", {"title": "新想法", "due_at": NOW + 120}, NOW
        )
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["basis"], "自己的想法")
        self.store.change("owner", item["id"], "delete", {}, NOW)
        self.assertEqual(self.store.list("owner"), [])

    def test_dates_require_explicit_timezone_and_future(self):
        self.assertGreater(
            parse_due("2030-05-15T12:00:00+08:00", NOW - 10000000), NOW - 10000000
        )
        for value in ["2030-05-15T12:00:00", "明天", None]:
            with self.assertRaises(ValueError):
                parse_due(value, NOW)
        with self.assertRaises(ValueError):
            self.store.create("owner", "x", "chat", NOW - 1, "", NOW)

    def test_concurrent_claim_and_daily_budget(self):
        self.create()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _: self.store.claim(NOW + 61, 8, "2030-03-17"), range(8)
                )
            )
        self.assertEqual(sum(bool(x) for x in results), 1)
        self.create()
        self.assertIsNone(self.store.claim(NOW + 61, 1, "2030-03-17"))

    def test_revise_or_cancel_invalidates_in_flight_wake(self):
        item = self.create()
        claim = self.store.claim(NOW + 61, 8, "day")
        self.store.change("owner", item["id"], "update", {"title": "改了"}, NOW + 62)
        self.assertFalse(self.store.finish(claim, "done", None, "stale", NOW + 63))
        self.assertEqual(self.store.list("owner")[0]["title"], "改了")

    def test_replied_afterthought_can_cancel_but_self_task_survives(self):
        a = self.create(cancel_on_reply=True)
        b = self.store.create("owner", "看书", "self_task", NOW + 60, "", NOW)
        self.store.observe("owner", "new-message", NOW + 1)
        rows = {x["id"]: x for x in self.store.list("owner")}
        self.assertEqual(rows[a["id"]]["state"], "cancelled")
        self.assertEqual(rows[b["id"]]["state"], "scheduled")

    def test_expiry_and_max_active_are_not_silent(self):
        self.create(max_active=1)
        self.assertEqual(
            self.create(max_active=1)["id"], self.store.list("owner")[0]["id"]
        )
        with self.assertRaises(ValueError):
            self.store.create(
                "owner", "另一个念头", "chat", NOW + 60, "", NOW, max_active=1
            )
        self.assertIsNone(self.store.claim(NOW + 8 * 86400, 8, "day"))
        self.assertEqual(self.store.list("owner")[0]["state"], "expired")

    def test_restart_does_not_replay_ambiguous_wake(self):
        self.create()
        self.store.claim(NOW + 61, 8, "day")
        restarted = ReminderStore(self.path)
        self.assertIsNone(restarted.claim(NOW + 700, 8, "day"))
        self.assertEqual(restarted.list("owner")[0]["state"], "uncertain")

    def test_unanswered_wait_is_reconsidered_only_after_new_user_input(self):
        self.create()
        claim = self.store.claim(NOW + 61, 8, "day")
        self.store.finish(claim, "waiting", None, "unanswered_backoff", NOW + 62)
        self.assertIsNone(self.store.claim(NOW + 10000, 8, "day"))
        self.store.observe("owner", "return", NOW + 10001)
        self.assertIsNotNone(self.store.claim(NOW + 12000, 8, "day"))

    def test_any_new_user_input_revokes_inflight_contact_even_when_note_survives(self):
        self.create(cancel_on_reply=False)
        claim = self.store.claim(NOW + 61, 8, "day")
        self.store.begin_contact(claim)
        self.assertTrue(self.store.can_send(claim["token"], "owner", NOW + 62))
        self.store.observe("owner", "new-message", NOW + 63)
        self.assertFalse(self.store.can_send(claim["token"], "owner", NOW + 64))

    def test_event_lookup_cannot_bind_to_another_session(self):
        self.create()
        claim = self.store.claim(NOW + 61, 8, "day")
        self.store.bind_request("other", "anchor", "task", claim["event_id"])
        self.assertFalse(self.store.bound("other", "anchor"))
