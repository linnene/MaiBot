from types import SimpleNamespace

import pytest

from src.chat.replyer.review import (
    REVIEW_TOOL, ReplyReviewBudget, parse_review_calls, review_history_content, review_tool,
)
from src.llm_models.payload_content.tool_option import ToolCall


def call(**changes):
    args = {"reason": "event_owner_conflict", "evidence_message_ids": ["m1"], "note": "The event belongs to the bot."}
    args.update(changes)
    return ToolCall(call_id="review1", func_name=REVIEW_TOOL, args=args)


def test_normal_speech_does_not_trigger_review():
    assert parse_review_calls([]) is None


def test_native_review_is_structured_and_not_user_history():
    parsed = parse_review_calls([call()])
    result = review_history_content(parsed, retry_allowed=True)
    assert result["status"] == "needs_review" and result["sent"] is False
    assert result["review"]["evidence_message_ids"] == ["m1"]
    assert "source_kind" not in result


@pytest.mark.parametrize("calls", [
    [call(reason="anything")], [call(note="")], [call(evidence_message_ids="m1")],
    [call(evidence_message_ids=["m"] * 5)], [call(extra="bad")], [call(), call()],
    [SimpleNamespace(func_name="unknown", args={})],
])
def test_malformed_controls_never_fail_open(calls):
    assert parse_review_calls(calls)["reason"] == "invalid_review_call"


def test_budget_is_bound_to_input_not_target_or_elapsed_time():
    budget = ReplyReviewBudget()
    assert not budget.exhausted("user:1")
    assert budget.record("user:1")
    assert not budget.record("user:1")
    assert budget.exhausted("user:1")
    assert not budget.exhausted("user:2")
    assert budget.record("user:2")
    assert not budget.exhausted("proactive:task2")


def test_schema_uses_native_function_and_bounded_evidence():
    tool = review_tool()
    assert tool.name == REVIEW_TOOL
    assert tool.parameters_schema_override["additionalProperties"] is False
    assert tool.parameters_schema_override["properties"]["evidence_message_ids"]["maxItems"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("review_call", [call(), call(note="")])
async def test_generator_suppresses_co_emitted_prose_before_hooks(monkeypatch, review_call):
    from unittest.mock import AsyncMock
    from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
    from src.common.data_models.llm_service_data_models import LLMResponseResult
    from src.config.config import global_config
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_response_text

    monkeypatch.setattr(global_config.experimental, "replyer_review", True)
    response=LLMResponseResult.from_portable_output(response="This must never be sent.",tool_calls=[review_call],model_name="synthetic")
    generator=object.__new__(BaseMaisakaReplyGenerator)
    generator.request_type="maisaka.replyer"
    generator.chat_stream=None
    generate=AsyncMock(return_value=response)
    generator.express_model=SimpleNamespace(task_name="replyer",generate_response_with_context=generate)
    monkeypatch.setattr(generator,"_build_reply_context",AsyncMock(return_value=MaisakaReplyContext()))
    monkeypatch.setattr(generator,"_build_request_messages",lambda **_: [ContextItemBuilder().set_role(RoleType.User).add_text_content("synthetic").build()])
    hook=AsyncMock(return_value=SimpleNamespace(kwargs={}))
    monkeypatch.setattr(generator,"_get_runtime_manager",lambda:SimpleNamespace(invoke_hook=hook))
    monkeypatch.setattr(generator,"_persist_reply_preview",lambda *a,**k:None)
    success,result=await generator.generate_reply_with_context(chat_history=[])
    assert not success and result.review_request is not None
    assert not result.completion.response_text and not get_response_text(response.output_items)
    assert generate.call_args.kwargs["options"].tool_options[0].name == REVIEW_TOOL
    assert not any(c.args[0] == "maisaka.replyer.after_response" for c in hook.call_args_list)
