"""Protocol regression, entirely synthetic; no model calls or QQ sending."""

import pytest

from src.chat.replyer.visible_reply import unwrap_self_history


@pytest.mark.parametrize(
    "text,expected",
    [
        ('<self_message time="2030-01-02T12:34:56">\n好呀\n</self_message>', "好呀"),
        ('<self_message time="2030-01-02T12:34:56">好呀</self_message>', "好呀"),
        (
            '<self_message time="2030-01-02T12:34:56">\n先看书\n</self_message>\n<self_message time="2030-01-02T12:34:56">\n晚点聊',
            "先看书\n晚点聊",
        ),
        ('<self_message time="2030-01-02T12:34:56">\n好呀', "好呀"),
        ('<self_message time="2030-01-02T12:34:56">\n</self_message>', ""),
    ],
)
def test_only_internal_envelopes_removed(text, expected):
    cleaned = unwrap_self_history(text)
    assert cleaned == expected
    assert unwrap_self_history(cleaned) == cleaned


@pytest.mark.parametrize(
    "text",
    [
        "下午 12:34:56 见，2030-01-02 那天也行",
        '讨论 <self_message time="2030-01-02T12:34:56"> 标签',
        '```xml\n<self_message time="2030-01-02T12:34:56">\n示例\n</self_message>\n```',
        '<message time="2030-01-02T12:34:56">例子</message>',
        '<self_message time="not-a-date">例子</self_message>',
        "  正常对话的空格不应被改动  ",
    ],
)
def test_normal_times_code_and_other_xml_unchanged(text):
    assert unwrap_self_history(text) == text


@pytest.mark.asyncio
async def test_generator_keeps_canonical_projection_equal_to_visible_reply(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
    from src.common.data_models.llm_service_data_models import LLMResponseResult
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_response_text

    raw = '<self_message time="2030-01-02T12:34:56">\n好呀\n</self_message>'
    response = LLMResponseResult.from_portable_output(response=raw, model_name="synthetic")
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    generator.chat_stream = None
    generator.express_model = SimpleNamespace(
        task_name="replyer", generate_response_with_context=AsyncMock(return_value=response)
    )
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext()))
    monkeypatch.setattr(
        generator,
        "_build_request_messages",
        lambda **_: [ContextItemBuilder().set_role(RoleType.User).add_text_content("你好").build()],
    )
    monkeypatch.setattr(
        generator,
        "_get_runtime_manager",
        lambda: SimpleNamespace(invoke_hook=AsyncMock(return_value=SimpleNamespace(kwargs={}))),
    )
    monkeypatch.setattr(generator, "_persist_reply_preview", lambda *args, **kwargs: None)
    success, result = await generator.generate_reply_with_context(chat_history=[])
    assert success
    assert result.completion.response_text == "好呀"
    assert get_response_text(response.output_items) == "好呀"
    assert "self_message" not in str(result.output_items)
