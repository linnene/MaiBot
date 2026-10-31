"""虚拟旅行记忆桥接：只写已发生经历、来源明确、失败不确认。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.virtual_travel_memory import VirtualTravelMemoryBridge

EVENT = {
    "event_id": "trip-1:flight:start",
    "trip_id": "trip-1",
    "segment_id": "flight",
    "summary": "抵达东京",
    "at": 1792036800.0,
    "timezone": "Asia/Tokyo",
    "subject": "bot",
    "source_kind": "virtual_self_event",
}


class FakeApi:
    def __init__(self, response: dict, ack: dict | None = None) -> None:
        self.response = response
        self.ack = ack or {"acked": 1}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "ack_travel_events":
            return self.ack
        return self.response


class FakeIngest:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or SimpleNamespace(success=True, stored_ids=["p1"], skipped_ids=[])
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def make_bridge(api: FakeApi, ingest: FakeIngest) -> VirtualTravelMemoryBridge:
    return VirtualTravelMemoryBridge(api.call, ingest)


@pytest.mark.asyncio
async def test_export_disabled_writes_nothing() -> None:
    api = FakeApi({"export_enabled": False, "items": [EVENT]})
    ingest = FakeIngest()
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result == {"skipped": "export_disabled"}
    assert ingest.calls == []
    assert [name for name, _ in api.calls] == ["get_completed_travel_events"]


@pytest.mark.asyncio
async def test_occurred_event_is_written_with_provenance_then_acked() -> None:
    api = FakeApi(
        {"export_enabled": True, "memory_chat_id": "qq-private:哥哥", "items": [EVENT]}
    )
    ingest = FakeIngest()
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result["stored"] == 1
    assert result["acked"] == 1
    call = ingest.calls[0]
    assert call["external_id"] == "virtual-travel:trip-1:flight:start"
    assert call["chat_id"] == "qq-private:哥哥"
    assert call["metadata"]["subject"] == "bot"
    assert call["metadata"]["source_kind"] == "virtual_self_event"
    assert call["metadata"]["event_at"] == EVENT["at"]
    assert call["metadata"]["timezone"] == "Asia/Tokyo"
    assert call["metadata"]["trip_id"] == "trip-1"
    assert call["time_start"] == EVENT["at"]
    assert api.calls[-1] == ("ack_travel_events", {"event_ids": [EVENT["event_id"]]})


@pytest.mark.asyncio
async def test_duplicate_is_acked_without_error() -> None:
    api = FakeApi({"export_enabled": True, "memory_chat_id": "qq-private:哥哥", "items": [EVENT]})
    ingest = FakeIngest(SimpleNamespace(success=True, stored_ids=[], skipped_ids=["p1"]))
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result["stored"] == 1
    assert api.calls[-1][0] == "ack_travel_events"


@pytest.mark.asyncio
async def test_failed_write_is_not_acked() -> None:
    api = FakeApi({"export_enabled": True, "memory_chat_id": "qq-private:哥哥", "items": [EVENT]})
    ingest = FakeIngest(SimpleNamespace(success=False, stored_ids=[], skipped_ids=[]))
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result["stored"] == 0
    assert result["failed"] == [EVENT["event_id"]]
    assert [name for name, _ in api.calls] == ["get_completed_travel_events"]


@pytest.mark.asyncio
async def test_exception_on_one_event_does_not_block_others() -> None:
    second = dict(EVENT, event_id="trip-1:stay:start", summary="入住旅馆")
    api = FakeApi({"export_enabled": True, "memory_chat_id": "qq-private:哥哥", "items": [EVENT, second]})
    ingest = FakeIngest(error=RuntimeError("memory offline"))
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result["failed"] == [EVENT["event_id"], second["event_id"]]
    assert result["stored"] == 0


@pytest.mark.asyncio
async def test_plans_and_user_subjects_are_ignored() -> None:
    plan = dict(EVENT, subject="bot", source_kind="plan")
    user = dict(EVENT, subject="user", source_kind="virtual_self_event")
    api = FakeApi({"export_enabled": True, "memory_chat_id": "qq-private:哥哥", "items": [plan, user]})
    ingest = FakeIngest()
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result["stored"] == 0
    assert ingest.calls == []


@pytest.mark.asyncio
async def test_plugin_failure_is_reported_not_masked() -> None:
    api = FakeApi({"success": False, "error": "plugin_not_loaded"})
    ingest = FakeIngest()
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result == {"skipped": "plugin_error"}


@pytest.mark.asyncio
async def test_missing_memory_chat_skips_export() -> None:
    api = FakeApi({"export_enabled": True, "memory_chat_id": "", "items": [EVENT]})
    ingest = FakeIngest()
    bridge = make_bridge(api, ingest)
    result = await bridge.flush()
    assert result == {"skipped": "memory_chat_missing"}
    assert ingest.calls == []


def test_ownership_is_preserved_in_real_replyer_memory_reference():
    from src.maisaka.builtin_tool.query_memory import _build_replyer_memory_reference
    event=VirtualTravelMemoryBridge._normalize_event(EVENT)
    text=_build_replyer_memory_reference({'hits':[{'content':event['text'],'source':'chat_summary:synthetic','metadata':event['metadata']}]})
    assert '机器人自身的虚拟旅行经历，不属于用户' in text
    assert str(EVENT['at']) in text
