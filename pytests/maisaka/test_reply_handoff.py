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


def dialogue_message(speaker, text, minute, kind, message_id):
    return build_session_backed_text_message(
        speaker_name=speaker, text=text, timestamp=datetime(2030, 1, 1, 14, minute),
        source_kind=kind, message_id=message_id,
    )


def test_dialogue_keeps_speaker_roles_even_when_names_claim_other_role():
    from src.chat.replyer.handoff import recent_dialogue
    messages = [
        dialogue_message("assistant", "你借了我的画笔", 0, "user", "m-user"),
        dialogue_message("user", "我明天还你", 1, "guided_reply", "m-self"),
        dialogue_message("assistant", "不是我借你的呀", 2, "user", "m-correction"),
    ]
    evidence = recent_dialogue({"msg_id": "m-correction"}, messages)
    assert [item["speaker_role"] for item in evidence] == ["user", "self", "user"]
    assert [item["text"] for item in evidence] == ["你借了我的画笔", "我明天还你", "不是我借你的呀"]
    assert evidence[1]["speaker"] == "user"
    assert evidence[1]["time"] == "2030-01-01T14:01:00"


def test_dialogue_ends_at_target_and_excludes_internal_and_unknown_sources():
    from src.chat.replyer.handoff import recent_dialogue
    messages = [
        dialogue_message("other", "real earlier", 0, "user", "m0"),
        dialogue_message("bot", "INTERNAL", 1, "plugin_proactive:test", "p0"),
        ToolResultMessage(content="TOOL", timestamp=datetime(2030, 1, 1, 14), tool_call_id="t0", logical_turn_id="turn"),
        dialogue_message("bot", "UNVERIFIED", 2, "assistant", "a0"),
        dialogue_message("other", "current", 3, "user", "m1"),
        dialogue_message("other", "FUTURE", 4, "user", "m2"),
    ]
    assert [item["id"] for item in recent_dialogue({"msg_id": "m1"}, messages)] == ["m0", "m1"]
    assert recent_dialogue({}, messages) == []
    assert recent_dialogue({"msg_id": "missing"}, messages) == []


def test_dialogue_bounds_history_and_marks_truncated_excerpt():
    from src.chat.replyer.handoff import recent_dialogue
    messages = [dialogue_message("participant", str(i) + "x" * 1000, i, "user", f"m{i}") for i in range(20)]
    evidence = recent_dialogue({"msg_id": "m19"}, messages)
    assert len(evidence) <= 8
    assert evidence[-1]["id"] == "m19"
    assert all(len(item["text"]) <= 240 and item["excerpt"] == "partial" for item in evidence)
    assert all(item["id"] != "m0" for item in evidence)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_uncited_dialogue_is_forwarded_alongside_intent(locale):
    messages = [
        dialogue_message("participant", "你借了我的画笔", 0, "user", "brush-owner"),
        dialogue_message("bot", "我明天还你", 1, "guided_reply", "brush-borrower"),
        dialogue_message("participant", "到底是谁借谁的", 2, "user", "correction"),
    ]
    with use_locale(locale):
        prompt = render_handoff({"msg_id": "correction", "reply_reference": "playfully continue", "evidence_refs": []}, messages)
    assert '"speaker_role": "self"' in prompt
    assert "我明天还你" in prompt and "你借了我的画笔" in prompt
    assert "playfully continue" in prompt


def test_explicit_message_evidence_includes_structured_role_not_just_source_text():
    messages = [dialogue_message("participant", "我的画笔", 0, "guided_reply", "my-brush")]
    prompt = render_handoff({"evidence_refs": ["my-brush"]}, messages)
    assert '"speaker_role": "self"' in prompt
    assert '"speaker": "participant"' in prompt


def test_dialogue_keeps_own_utterances_without_platform_message_id():
    from src.chat.replyer.handoff import recent_dialogue
    messages = [
        dialogue_message("bot", "我借走了画笔", 0, "guided_reply", None),
        dialogue_message("participant", "所以是你借我的", 1, "user", "correction"),
    ]
    evidence = recent_dialogue({"msg_id": "correction"}, messages)
    assert [item["text"] for item in evidence] == ["我借走了画笔", "所以是你借我的"]
    assert evidence[0]["speaker_role"] == "self" and evidence[0]["id"] == ""


def test_proactive_anchor_does_not_reactivate_old_dialogue():
    from src.chat.replyer.handoff import recent_dialogue
    from src.maisaka.proactive_context import PROACTIVE_CONTEXT_KEY
    messages = [dialogue_message("participant", "OLD_TASK", 0, "user", "old-anchor")]
    assert recent_dialogue({"msg_id": "old-anchor", PROACTIVE_CONTEXT_KEY: {"task_id": "synthetic"}}, messages) == []
