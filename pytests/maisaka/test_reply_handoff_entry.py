"""Synthetic real-entry regressions: no provider, account data or sends."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.replyer import maisaka_generator_base as replyer_module
from src.chat.replyer.handoff import HANDOFF_TURN_KEY, validate_handoff
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, MaisakaReplyContext
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.payload_content.context_item import get_item_text
from src.maisaka.context.messages import ToolResultMessage
from src.maisaka.context.planner_messages import build_session_backed_text_message


@pytest.mark.asyncio
@pytest.mark.parametrize("retro", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("refresh", [False, True])
async def test_memory_survives_actual_generation_and_context_rebuild(monkeypatch, retro, explicit, refresh):
    """Dropping raw evidence before request construction loses the current memory."""
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", False)
    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", retro)
    monkeypatch.setattr(global_config.experimental, "replyer_review", False)
    monkeypatch.setattr(global_config.experimental.attention_drift, "enabled", False)
    monkeypatch.setattr(replyer_module, "resolve_enable_visual_planner", lambda: refresh)
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    generator.chat_stream = None
    generator._load_prompt = load_prompt
    monkeypatch.setattr(generator, "_build_system_prompt", lambda **_: "Synthetic identity")
    monkeypatch.setattr(generator, "_build_personality_prompt", lambda: "Synthetic identity")
    monkeypatch.setattr(generator, "_build_group_chat_attention_block", lambda *_: "")
    monkeypatch.setattr(generator, "_build_final_user_message", lambda **_: "Synthetic target")
    monkeypatch.setattr(generator, "_build_keyword_reaction_prompt", lambda **_: "")
    monkeypatch.setattr(generator, "_select_temporary_reply_style", lambda: "")
    monkeypatch.setattr(generator, "_build_reply_context", AsyncMock(return_value=MaisakaReplyContext()))
    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(
        invoke_hook=AsyncMock(return_value=SimpleNamespace(kwargs={}))
    ))
    monkeypatch.setattr(generator, "_persist_reply_preview", lambda *_, **__: None)
    monkeypatch.setattr(generator, "_invoke_before_model_request_hook", AsyncMock(
        side_effect=lambda **kw: kw["request_messages"]
    ))

    def user(text):
        return build_session_backed_text_message(
            speaker_name="Synthetic user", text=text, timestamp=datetime(2030, 1, 1),
            source_kind="user", message_id="synthetic-user",
        )

    def tool(name, call_id, content, *, turn="active-turn", success=True, metadata=None):
        return ToolResultMessage(
            content=content, timestamp=datetime(2030, 1, 1), tool_call_id=call_id,
            logical_turn_id=turn, tool_name=name, success=success, metadata=metadata or {},
        )

    history = [
        user("CURRENT_USER"),
        tool("query_memory", "old", "EXCLUDED_OLD", turn="old-turn"),
        tool("query_memory", "failed", "EXCLUDED_FAILED", success=False),
        tool("web_search", "unrelated", "EXCLUDED_UNRELATED"),
        tool("web_search", "citation", "EXPLICIT_CITATION_ONLY"),
        tool("query_memory", "memory", "RAW_MEMORY_NOT_SPEECH", metadata={
            "replyer_memory_reference": "CURRENT_MEMORY_FACT " + "x" * 3000,
        }),
        tool("query_person_profile", "profile", "CURRENT_PROFILE_FACT"),
    ]
    args = {"reply_reference": "Reply briefly", "evidence_refs": ["memory", "citation"] if explicit else [],
            HANDOFF_TURN_KEY: "active-turn"}
    validate_handoff(args, history)
    captured = []

    async def refresh_history(messages):
        messages[0] = user("REFRESHED_USER")
        return 1

    async def generate(*, context_factory, options):
        # Exercise the initial prompt and repeated actual-provider context rebuilds.
        for _ in range(2):
            items = await context_factory(None, SimpleNamespace(visual=False))
            captured.append("\n".join(get_item_text(item) for item in items))
        return LLMResponseResult.from_portable_output(response="Synthetic reply", model_name="synthetic")

    generator.express_model = SimpleNamespace(task_name="replyer", generate_response_with_context=generate)
    success, result = await generator.generate_reply_with_context(
        chat_history=history, reply_tool_args=args, image_recognition_refresher=refresh_history,
    )
    assert success, result.error_message
    assert len(captured) == 2
    for text in captured:
        assert "CURRENT_MEMORY_FACT" in text
        assert "CURRENT_PROFILE_FACT" in text
        assert text.count('"id": "memory"') == 1
        assert "RAW_MEMORY_NOT_SPEECH" not in text
        assert "EXCLUDED_OLD" not in text and "EXCLUDED_FAILED" not in text
        assert "EXCLUDED_UNRELATED" not in text
        assert ("EXPLICIT_CITATION_ONLY" in text) is explicit
        assert "x" * 1201 not in text
        assert ("REFRESHED_USER" if refresh else "CURRENT_USER") in text
