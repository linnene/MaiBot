"""Regression tests from concrete delivery review reproductions."""

import json, logging, subprocess, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import bootstrap  # noqa: E402,F401
from _virtual_travel_test_pkg.domain.ledger import Ledger
from _virtual_travel_test_pkg.domain.trips import TripService
from _virtual_travel_test_pkg.plugin import VirtualTravelPlugin
from _virtual_travel_test_pkg.config import TravelConfig

NOW = 1900000000.0


def segment(start=NOW + 60, end=NOW + 120, **extra):
    return dict(
        segment_id="flight",
        kind="flight",
        title="Fly",
        start_at=start,
        end_at=end,
        timezone="Asia/Tokyo",
        from_place="Home",
        to_place="Tokyo",
        phase="in_transit",
        requires_funds=True,
        reservation_key="fare",
        **extra,
    )


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "travel.sqlite3"
        self.ledger = Ledger(path)
        self.ledger.credit_month("2030-03", 15000, NOW)
        self.trips = TripService(path, self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, segments, confirm=True):
        t = self.trips.propose("Trip", NOW, "Tokyo", "Asia/Tokyo")
        for s in segments:
            if s.get("requires_funds"):
                self.ledger.reserve(
                    t["trip_id"], s["reservation_key"], 20000, s["end_at"] + 86400, NOW
                )
        t = self.trips.revise(t["trip_id"], t["revision"], {"segments": segments}, NOW)
        return self.trips.confirm(t["trip_id"], t["revision"], NOW) if confirm else t

    def test_unconfirmed_plan_does_not_project_actual_travel(self):
        self.make([segment()], False)
        view = self.trips.projection(NOW + 70, NOW + 500)
        self.assertEqual(view["phase"], "idea")
        self.assertEqual(view["source_kind"], "plan")

    def test_gap_stays_at_destination_until_final_boundary(self):
        first = segment()
        second = segment(NOW + 300, NOW + 400)
        second.update(
            segment_id="return",
            reservation_key="return",
            from_place="Tokyo",
            to_place="Home",
            phase="returning",
        )
        self.make([first, second])
        self.trips.advance(NOW + 150)
        view = self.trips.projection(NOW + 200, NOW + 500)
        self.assertNotEqual(view["phase"], "completed")
        self.assertEqual(view["location"]["place"], "Tokyo")

    def test_settlement_interruption_cannot_commit_free_completion(self):
        t = self.make([segment()])
        # Inject failure at the actual settlement entry used by the current implementation.
        name = "settle_in" if hasattr(self.ledger, "settle_in") else "settle"
        original = getattr(self.ledger, name)

        def crash(*args, **kwargs):
            raise RuntimeError("simulated interruption")

        setattr(self.ledger, name, crash)
        try:
            with self.assertRaises(RuntimeError):
                self.trips.advance(NOW + 500)
        finally:
            setattr(self.ledger, name, original)
        restarted = TripService(self.ledger.db.path, self.ledger)
        restarted.advance(NOW + 600)
        self.assertEqual(restarted.get(t["trip_id"])["status"], "completed")
        self.assertEqual(self.ledger.balance(NOW + 600)["spent_fen"], 20000)


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_postpone_can_use_persisted_segments_without_iso_input(self):
        with tempfile.TemporaryDirectory() as td:
            p = VirtualTravelPlugin()
            p._plugin_config_instance = TravelConfig(enabled=True, poll_seconds=600)
            p._now = lambda: NOW
            p._ctx = SimpleNamespace(
                paths=SimpleNamespace(data_dir=td),
                logger=logging.getLogger("test"),
                config=SimpleNamespace(get=AsyncMock()),
                llm=SimpleNamespace(generate=AsyncMock()),
                api=SimpleNamespace(call=AsyncMock()),
            )
            await p.on_load()
            try:
                t = p.trips.propose("Trip", NOW, "Tokyo", "Asia/Tokyo")
                p.ledger.reserve(t["trip_id"], "fare", 20000, NOW + 90000, NOW)
                t = p.trips.revise(
                    t["trip_id"], t["revision"], {"segments": [segment()]}, NOW
                )
                t = p.trips.confirm(t["trip_id"], t["revision"], NOW)
                result = json.loads(
                    await p.travel_plan(
                        "postpone",
                        trip_id=t["trip_id"],
                        expected_revision=t["revision"],
                        shift_seconds="86400",
                    )
                )
                self.assertTrue(result["success"], result)
                self.assertEqual(result["trip"]["segments"][0]["start_at"], NOW + 86460)
            finally:
                await p.on_unload()


class LoaderTests(unittest.TestCase):
    def test_real_host_style_package_load_ignores_global_config_module(self):
        script = """import importlib.util,sys,types
from pathlib import Path
root=Path(sys.argv[1]);sys.path=[p for p in sys.path if p!=str(root)];sys.modules['config']=types.ModuleType('config')
spec=importlib.util.spec_from_file_location('_host_virtual_travel',root/'plugin.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
assert module.create_plugin().get_components()
"""
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                [sys.executable, "-c", script, str(ROOT)],
                cwd=td,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


class OpportunityRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failure_keeps_monthly_opportunity_for_bounded_retry(self):
        with tempfile.TemporaryDirectory() as td:
            p = VirtualTravelPlugin()
            p._plugin_config_instance = TravelConfig(
                enabled=True, poll_seconds=600, quiet_start="00:00", quiet_end="00:00"
            )
            p._now = lambda: NOW
            p._ctx = SimpleNamespace(
                paths=SimpleNamespace(data_dir=td),
                logger=logging.getLogger("test"),
                config=SimpleNamespace(get=AsyncMock()),
                llm=SimpleNamespace(generate=AsyncMock()),
                api=SimpleNamespace(call=AsyncMock()),
            )
            await p.on_load()
            try:
                month = p._current_month(NOW)
                p.ledger.db.transaction(
                    lambda db: db.execute(
                        "UPDATE opportunities SET at=? WHERE month=?", (NOW - 1, month)
                    )
                )
                p._consider = AsyncMock(
                    return_value={
                        "action": "rejected",
                        "reason": "model_unavailable",
                        "applied": False,
                    }
                )
                await p.tick()
                await p.tick()
                self.assertIsNone(p.opportunities.get(month)["handled_at"])
                self.assertEqual(p._consider.await_count, 1)
                p._now = lambda: NOW + 3601
                p._consider.return_value = {
                    "action": "none",
                    "reason": "declined",
                    "applied": False,
                }
                await p.tick()
                self.assertIsNotNone(p.opportunities.get(month)["handled_at"])
            finally:
                await p.on_unload()
