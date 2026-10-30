import importlib.util
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "self_reminders", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
pkg = importlib.util.module_from_spec(spec)
sys.modules["self_reminders"] = pkg
spec.loader.exec_module(pkg)
from self_reminders.plugin import SelfRemindersPlugin, RemindersConfig  # noqa: E402

NOW = 1900000000.0


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = SelfRemindersPlugin()
        self.p._plugin_config_instance = RemindersConfig()

        async def get(key, default=None):
            if key == "relationship_identity.enabled":
                return True
            return (
                [{"platform": "qq", "user_id": "trusted-user", "exclusive": True}]
                if key == "relationship_identity.bindings"
                else "合成温和人设"
            )

        self.trigger = AsyncMock(
            return_value={
                "success": True,
                "accepted": True,
                "task_id": "proactive:local.self-reminders:test",
            }
        )
        self.llm = AsyncMock(
            return_value={"success": True, "response": '{"action":"done"}'}
        )
        self.p._ctx = SimpleNamespace(
            paths=SimpleNamespace(data_dir=self.tmp.name),
            logger=logging.getLogger("test"),
            config=SimpleNamespace(get=get),
            chat=SimpleNamespace(
                get_private_streams=AsyncMock(
                    return_value=[
                        {
                            "stream_id": "owner",
                            "user_id": "trusted-user",
                            "platform": "qq",
                        }
                    ]
                )
            ),
            llm=SimpleNamespace(generate=self.llm),
            maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=self.trigger)),
        )
        self.p._now = lambda: NOW
        await self.p.on_load()
        self.p.config.enabled = True
        self.p.config.owner_session = "owner"
        self.p.config.quiet_start = "00:00"
        self.p.config.quiet_end = "00:00"

    async def asyncTearDown(self):
        await self.p.on_unload()
        self.tmp.cleanup()

    async def create(self, **kwargs):
        return await self.p.manage(
            action="create",
            title="想聊摄影",
            kind="chat",
            due_at=datetime.fromtimestamp(NOW + 60, timezone.utc).isoformat(),
            session_id="owner",
            **kwargs,
        )

    async def test_tools_are_owner_only_and_no_ui_mutation_api(self):
        self.assertFalse(
            (await self.p.manage(action="list", session_id="stranger"))["success"]
        )
        item = await self.create()
        self.assertTrue(item["success"])
        self.assertEqual(self.trigger.await_count, 0)
        ui = json.loads((ROOT / "webui.json").read_text())
        self.assertTrue(all(not page.get("actions") for page in ui["pages"]))

    async def test_due_internal_review_can_finish_without_qq_task(self):
        await self.create()
        self.p._now = lambda: NOW + 1000
        await self.p.tick()
        self.assertEqual(self.trigger.await_count, 0)
        self.assertEqual(self.p.store.list("owner")[0]["state"], "done")

    async def test_contact_uses_existing_host_policy_and_is_not_marked_sent(self):
        await self.create()
        self.llm.return_value = {
            "success": True,
            "response": '{"action":"contact","reason":"有个摄影念头想聊聊"}',
        }
        self.p._now = lambda: NOW + 1000
        await self.p.tick()
        kwargs = self.trigger.call_args.kwargs
        self.assertEqual(kwargs["intent"], "mai_life_proactive")
        self.assertTrue(kwargs["metadata"]["interaction_delegate"])
        self.assertEqual(
            json.loads(kwargs["reason"])["candidate_kind"], "personal_intent"
        )
        self.assertEqual(
            self.p.store.list("owner")[0]["outcome"], "queued_not_confirmed_sent"
        )

    async def test_unanswered_decline_parks_until_user_returns(self):
        await self.create()
        self.llm.return_value = {"success": True, "response": '{"action":"contact"}'}
        self.trigger.return_value = {
            "success": True,
            "accepted": False,
            "reason": "unanswered_backoff",
        }
        self.p._now = lambda: NOW + 1000
        await self.p.tick()
        self.assertEqual(self.p.store.list("owner")[0]["state"], "waiting")
        await self.p.tick()
        self.assertEqual(self.llm.await_count, 1)

    async def test_ambiguous_queue_holds_without_retry(self):
        await self.create()
        self.llm.return_value = {"success": True, "response": '{"action":"contact"}'}
        self.trigger.side_effect = TimeoutError()
        self.p._now = lambda: NOW + 1000
        await self.p.tick()
        self.assertEqual(self.p.store.list("owner")[0]["state"], "uncertain")
        await self.p.tick()
        self.assertEqual(self.trigger.await_count, 1)

    async def test_new_user_can_cancel_in_flight_note_before_contact(self):
        await self.create(cancel_on_reply=True)
        self.p._now = lambda: NOW + 1000

        async def generate(**kwargs):
            self.p.store.observe("owner", "new", NOW + 1001)
            return {"success": True, "response": '{"action":"contact"}'}

        self.llm.side_effect = generate
        await self.p.tick()
        self.assertEqual(self.trigger.await_count, 0)

    async def test_ordinary_send_is_untouched(self):
        self.assertEqual(
            await self.p.guard_send(
                message={"session_id": "stranger"}, reply_message_id="x"
            ),
            {"action": "continue"},
        )

    async def test_missing_timezone_fails_explicitly(self):
        r = await self.p.manage(
            action="create",
            title="x",
            kind="chat",
            due_at="2030-03-17T20:00:00",
            session_id="owner",
        )
        self.assertFalse(r["success"])
        self.assertEqual(self.p.store.list("owner"), [])

    async def test_tool_update_kind_and_context_are_scoped(self):
        result = await self.create()
        ident = result["item"]["id"]
        changed = await self.p.manage(
            action="update", id=ident, kind="self_task", session_id="owner"
        )
        self.assertEqual(changed["item"]["kind"], "self_task")
        self.assertFalse(
            (await self.p.manage(action="delete", id=ident, session_id="stranger"))[
                "success"
            ]
        )

    async def test_quiet_window_and_daily_zero_make_no_model_calls(self):
        await self.create()
        self.p._now = lambda: NOW + 1000
        self.p.config.quiet_start = "00:00"
        self.p.config.quiet_end = "23:59"
        await self.p.tick()
        self.assertEqual(self.llm.await_count, 0)
        self.p.config.quiet_end = "00:00"
        self.p.config.daily_wakes = 0
        await self.p.tick()
        self.assertEqual(self.llm.await_count, 0)

    async def test_bad_review_is_held_and_not_retried(self):
        await self.create()
        self.p._now = lambda: NOW + 1000
        self.llm.return_value = {"success": True, "response": "not json"}
        await self.p.tick()
        await self.p.tick()
        self.assertEqual(self.llm.await_count, 1)
        self.assertEqual(self.trigger.await_count, 0)
        self.assertEqual(self.p.store.list("owner")[0]["state"], "uncertain")

    async def test_cancel_revokes_queued_send_across_reload(self):
        item = await self.create()
        self.p._now = lambda: NOW + 1000
        claim = self.p.store.claim(NOW + 1000, 8, "day", ["owner"])
        self.p.store.begin_contact(claim)
        self.p.store.bind_request(
            "owner", "anchor", "proactive:local.self-reminders:test", claim["event_id"]
        )
        self.p._bound_keys.add(("owner", "anchor"))
        kw = {
            "message": {
                "session_id": "owner",
                "message_info": {
                    "additional_config": {
                        "_host_proactive_task_id": "proactive:local.self-reminders:test"
                    }
                },
            },
            "reply_message_id": "anchor",
        }
        guarded = await self.p.guard_send(**kw)
        self.assertIn("modified_kwargs", guarded)
        await self.p.manage(action="cancel", id=item["item"]["id"], session_id="owner")
        await self.p.on_unload()
        await self.p.on_load()
        self.assertEqual(await self.p.guard_send(**kw), {"action": "abort"})

    async def test_actual_receipt_and_stale_task_are_distinct(self):
        await self.create()
        claim = self.p.store.claim(NOW + 1000, 8, "day", ["owner"])
        self.p.store.begin_contact(claim)
        self.p.store.bind_request(
            "owner", "anchor", "proactive:local.self-reminders:test", claim["event_id"]
        )
        self.p._bound_keys.add(("owner", "anchor"))
        self.p._now = lambda: NOW + 1000
        guarded = await self.p.guard_send(
            message={
                "session_id": "owner",
                "message_info": {
                    "additional_config": {
                        "_host_proactive_task_id": "proactive:local.self-reminders:test"
                    }
                },
            },
            reply_message_id="anchor",
        )
        msg = guarded["modified_kwargs"]["message"]
        await self.p.after_send(message=msg, sent=True)
        row = self.p.store.run(
            lambda db: dict(db.execute("SELECT * FROM wakes").fetchone())
        )
        self.assertEqual(row["state"], "queued")
        msg["platform_message_id"] = "actual-receipt"
        await self.p.after_send(message=msg, sent=True)
        row = self.p.store.run(
            lambda db: dict(db.execute("SELECT * FROM wakes").fetchone())
        )
        self.assertEqual(row["state"], "sent")
        await self.p.track_request(
            session_id="owner",
            reply_message_id="old",
            reply_tool_args={
                "_host_proactive_task": {
                    "task_id": "proactive:local.self-reminders:old",
                    "text": "bad",
                }
            },
        )
        self.assertEqual(
            await self.p.guard_send(
                message={
                    "session_id": "owner",
                    "message_info": {
                        "additional_config": {
                            "_host_proactive_task_id": "proactive:local.self-reminders:old"
                        }
                    },
                },
                reply_message_id="old",
            ),
            {"action": "abort"},
        )

    async def test_afterthought_default_and_retained_identity_policy(self):
        result = await self.p.manage(
            action="create",
            title="补一句",
            kind="afterthought",
            due_at=datetime.fromtimestamp(NOW + 60, timezone.utc).isoformat(),
            session_id="owner",
        )
        self.assertTrue(result["item"]["cancel_on_reply"])
        self.p.ctx.chat.get_private_streams.return_value = [
            {"stream_id": "owner", "user_id": "wrong", "platform": "qq"}
        ]
        self.assertFalse(
            (await self.p.manage(action="list", session_id="owner"))["success"]
        )

    async def test_host_numeric_timestamp_cancels_queued_afterthought(self):
        await self.create(cancel_on_reply=True)
        claim = self.p.store.claim(NOW + 1000, 8, "day", ["owner"])
        self.p.store.begin_contact(claim)
        self.p.store.bind_request(
            "owner", "anchor", "proactive:local.self-reminders:test", claim["event_id"]
        )
        self.p.store.finish(
            claim, "handled", None, "queued_not_confirmed_sent", NOW + 1000
        )
        self.p._now = lambda: NOW + 1001
        await self.p.observe_user(
            message={
                "session_id": "owner",
                "message_id": "new-user",
                "timestamp": str(NOW + 1001),
                "account_id": "bot",
                "message_info": {"user_info": {"user_id": "trusted-user"}},
            }
        )
        self.assertEqual(self.p.store.list("owner")[0]["state"], "cancelled")
        # A later split segment quoting the previous bot message retains Host provenance.
        verdict = await self.p.guard_send(
            message={
                "session_id": "owner",
                "message_info": {
                    "additional_config": {
                        "_host_proactive_task_id": "proactive:local.self-reminders:test"
                    }
                },
            },
            reply_message_id="different-bot-anchor",
        )
        self.assertEqual(verdict, {"action": "abort"})

    async def test_host_error_payload_never_auto_retries(self):
        await self.create()
        self.p._now = lambda: NOW + 1000
        self.llm.return_value = {"success": True, "response": '{"action":"contact"}'}
        self.trigger.return_value = {"success": False, "error": "post-enqueue failure"}
        await self.p.tick()
        self.p._now = lambda: NOW + 7000
        await self.p.tick()
        self.assertEqual(self.trigger.await_count, 1)
        self.assertEqual(self.p.store.list("owner")[0]["state"], "uncertain")
