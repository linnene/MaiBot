from datetime import datetime

import pytest

from src.chat.replyer.handoff import handoff_properties, render_handoff, validate_handoff
from src.common.i18n import use_locale
from src.maisaka.context.messages import ToolResultMessage
from src.maisaka.context.planner_messages import build_session_backed_text_message


def history():
    return [build_session_backed_text_message(
        speaker_name="user", text="我好困", timestamp=datetime(2026, 1, 1, 14),
        source_kind="user", message_id="msg-1",
    )]


def test_default_is_one_point_without_extra_advice():
    args = {"reply_reference": "接住犯困的抱怨"}
    validate_handoff(args, history())
    assert args["reply_boundary"] == "brief"
    with use_locale("zh-CN"):
        prompt = render_handoff(args, history())
    assert "不追加建议" in prompt and "主要回应目的：接住犯困的抱怨" in prompt


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_resolved_source_preserves_actual_content_and_time(locale):
    args = {"reply_reference": "回应", "evidence_refs": ["msg-1"], "reply_boundary": "brief"}
    validate_handoff(args, history())
    with use_locale(locale):
        prompt = render_handoff(args, history())
    assert "我好困" in prompt and "2026-01-01T14:00:00" in prompt
    assert '"kind": "user"' in prompt
    assert "昨晚熬夜" not in prompt


def test_unknown_or_failed_tool_source_is_rejected():
    failed = ToolResultMessage(content="missing", timestamp=datetime.now(), tool_call_id="call-1",
                               logical_turn_id="turn-1", success=False)
    with pytest.raises(ValueError, match="找不到"):
        validate_handoff({"evidence_refs": ["call-1"]}, [failed])


def test_successful_memory_tool_result_can_be_cited():
    tool = ToolResultMessage(content="synthetic memory: user mentioned an internship", timestamp=datetime.now(),
                            tool_call_id="call-1", logical_turn_id="turn-1", tool_name="query_memory")
    args = {"evidence_refs": ["call-1"], "reply_boundary": "explain"}
    validate_handoff(args, [tool])
    assert "query_memory" in render_handoff(args, [tool])


@pytest.mark.parametrize("fields", [
    {"evidence_refs": ["invented"]}, {"evidence_refs": ["msg-1", "msg-1"]},
    {"evidence_refs": "msg-1"}, {"evidence_refs": [False]}, {"reply_boundary": "ramble"},
])
def test_invalid_handoff_fails_before_generation(fields):
    with pytest.raises(ValueError):
        validate_handoff(fields, history())


def test_schema_has_no_nullable_nested_properties():
    schema = handoff_properties()
    assert schema["evidence_refs"]["maxItems"] == 3
    assert schema["reply_boundary"]["default"] == "brief"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_current_turn_memory_is_forwarded_without_model_citations(locale):
    from src.chat.replyer.handoff import HANDOFF_TURN_KEY
    tool = ToolResultMessage(
        content="A synthetic shared trip was booked.", timestamp=datetime(2030, 1, 1, 14),
        tool_call_id="actual-call-id", logical_turn_id="active-turn", tool_name="query_memory",
        metadata={"replyer_memory_reference": "Owner booked the synthetic northbound trip. event_time_start=100"},
    )
    with use_locale(locale):
        prompt = render_handoff({HANDOFF_TURN_KEY: "active-turn", "evidence_refs": []}, [tool])
    assert "northbound trip" in prompt and "actual-call-id" in prompt
    assert "retrieved_at" in prompt and "event_time_start=100" in prompt


@pytest.mark.parametrize("turn,tool_name,success", [
    ("old-turn", "query_memory", True), ("active-turn", "web_search", True),
    ("active-turn", "query_memory", False),
])
def test_automatic_memory_excludes_old_failed_and_unrelated_results(turn, tool_name, success):
    from src.chat.replyer.handoff import HANDOFF_TURN_KEY
    tool = ToolResultMessage(content="DO_NOT_INCLUDE", timestamp=datetime.now(), tool_call_id="call-x",
                             logical_turn_id=turn, tool_name=tool_name, success=success)
    assert "DO_NOT_INCLUDE" not in render_handoff({HANDOFF_TURN_KEY: "active-turn"}, [tool])


def test_automatic_memory_is_bounded_and_deduplicated():
    from src.chat.replyer.handoff import HANDOFF_TURN_KEY, current_turn_memory
    tools = [ToolResultMessage(content="x" * 3000, timestamp=datetime.now(), tool_call_id=f"c-{i}",
                              logical_turn_id="active", tool_name="query_memory") for i in range(4)]
    args = {HANDOFF_TURN_KEY: "active", "evidence_refs": ["c-3"]}
    evidence = current_turn_memory(args, tools)
    assert len(evidence) == 2 and sum(len(item['text']) for item in evidence) == 2400
    assert all(item["excerpt"] == "partial" for item in evidence)
    prompt = render_handoff(args, tools)
    assert prompt.count('"id": "c-3"') == 1


def test_unknown_reference_error_exposes_real_ids_for_correction():
    tool = ToolResultMessage(content="memory", timestamp=datetime.now(), tool_call_id="actual-id",
                             logical_turn_id="active", tool_name="query_memory")
    with pytest.raises(ValueError, match="actual-id"):
        validate_handoff({"evidence_refs": ["call_0"]}, [tool])
