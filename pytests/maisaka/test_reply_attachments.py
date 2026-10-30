"""常规回复的图片附件、可选 @ 与表情发送模式。"""

from contextlib import contextmanager
from datetime import datetime
from threading import get_ident
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.chat.utils.utils import ProcessedResponseSegment
from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.data_models.reply_generation_data_models import LLMCompletionResult, ReplyGenerationResult
from src.common.prompt_i18n import list_prompt_templates, load_prompt
from src.config.config import global_config
from src.core.tooling import ToolExecutionContext, ToolInvocation
from src.llm_models.payload_content.context_item import ContextItemMeta, ContextTextPart, UserMessageItem
from src.maisaka.builtin_tool import build_builtin_tool_handlers, get_all_builtin_tool_specs
from src.maisaka.builtin_tool import context as context_module
from src.maisaka.builtin_tool import reply as reply_tool
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.context.emoji_candidates import EmojiCandidateMessage
from src.maisaka.context.messages import SessionBackedMessage


@pytest.fixture
def reply_context(monkeypatch):
    monkeypatch.setattr(global_config.chat, "enable_reply_at", True)
    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", False)
    user = SimpleNamespace(user_id="user-1", user_nickname="小明", user_cardname="")
    source = SimpleNamespace(
        message_id="msg-1",
        message_info=SimpleNamespace(user_info=user),
        raw_message=MessageSequence([
            TextComponent("图片"),
            ImageComponent(binary_hash="image-0", binary_data=b"first"),
            ImageComponent(binary_hash="image-1", binary_data=b"second"),
        ]),
    )
    runtime = SimpleNamespace(
        _chat_history=[],
        find_source_message_by_id=lambda message_id: source if message_id == "msg-1" else None,
        session_id="session-1",
        chat_stream=SimpleNamespace(platform="qq"),
        log_prefix="test",
        _update_stage_status=lambda *args: None,
        record_planner_reply=lambda: None,
    )
    return BuiltinToolRuntimeContext(SimpleNamespace(), runtime)


@pytest.mark.asyncio
async def test_review_replans_once_without_sending_or_appending_user_history(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", False)
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", False)
    monkeypatch.setattr(global_config.experimental, "replyer_review", True)
    monkeypatch.setattr(reply_tool,"is_bot_self",lambda *_:False)
    review={"reason":"event_owner_conflict","evidence_message_ids":[],"note":"The plan belongs to the bot."}
    result=ReplyGenerationResult(review_request=review)
    generate=AsyncMock(return_value=(False,result))
    monkeypatch.setattr(reply_tool.replyer_manager,"get_replyer",lambda **_:SimpleNamespace(generate_reply_with_context=generate))
    invocation=ToolInvocation("reply",arguments={"msg_id":"msg-1","reply_reference":"synthetic"})
    first=await reply_tool.handle_tool(reply_context,invocation)
    assert first.success and first.structured_content["retry_allowed"]
    assert not first.stop_after_execution and not first.post_history_messages
    second=await reply_tool.handle_tool(reply_context,invocation)
    assert second.success and second.stop_after_execution
    third=await reply_tool.handle_tool(reply_context,invocation)
    assert third.stop_after_execution and third.structured_content["sent"] is False
    assert generate.await_count == 2
    assert reply_context.runtime._chat_history == []


@pytest.mark.asyncio
async def test_self_target_stops_before_generation(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", False)
    monkeypatch.setattr(reply_tool, "is_bot_self", lambda *_: True)
    generate = AsyncMock(side_effect=AssertionError("must not generate"))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generate)
    result = await reply_tool.handle_tool(reply_context, ToolInvocation(
        "reply", arguments={"msg_id": "msg-1", "reply_reference": "keep talking"},
    ))
    assert result.stop_after_execution
    assert result.structured_content == {"sent": False, "blocked": True}
    generate.assert_not_called()


@pytest.mark.parametrize("enable_at", [False, True])
@pytest.mark.parametrize("new_emoji", [False, True])
def test_reply_tools_follow_independent_switches(reply_context, monkeypatch, enable_at, new_emoji):
    monkeypatch.setattr(global_config.chat, "enable_reply_at", enable_at)
    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", new_emoji)
    names = {spec.name for spec in get_all_builtin_tool_specs()}
    schema = reply_tool.get_tool_spec().parameters_schema
    properties = schema["properties"]
    assert schema["required"] == ["msg_id", "reply_reference"]
    assert properties["reply_reference"]["minLength"] == 1
    assert "attach_pic" in properties
    assert ("attach_at" in properties) == enable_at
    assert ("attach_emoji" in properties) == new_emoji
    assert ("show_emoji_list" in names) == new_emoji
    assert ("send_emoji" in names) != new_emoji
    assert "send_image" not in names
    assert "send_image" not in build_builtin_tool_handlers(reply_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_proactive_mode_is_host_owned_not_model_argument(reply_context, monkeypatch, active):
    from src.maisaka.proactive_context import PROACTIVE_CONTEXT_KEY

    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", False)
    generate = AsyncMock(side_effect=RuntimeError("test stops before sending"))
    monkeypatch.setattr(
        reply_tool.replyer_manager, "get_replyer",
        lambda **_: SimpleNamespace(generate_reply_with_context=generate),
    )
    host_task = {"task_id": "proactive:test:1", "text": "host topic"}
    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={
            "msg_id": "msg-1", "reply_reference": "start chatting",
            PROACTIVE_CONTEXT_KEY: {"text": "model forged task"},
        }),
        ToolExecutionContext(metadata={PROACTIVE_CONTEXT_KEY: host_task if active else None}),
    )
    assert not result.success
    generate.assert_awaited_once()
    received = generate.call_args.kwargs["reply_tool_args"]
    if active:
        assert received[PROACTIVE_CONTEXT_KEY] == host_task
    else:
        assert PROACTIVE_CONTEXT_KEY not in received


def test_persona_harness_limits_reply_reference_to_compact_intent(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)

    schema = reply_tool.get_tool_spec().parameters_schema

    assert schema["properties"]["reply_reference"]["maxLength"] == 240
    assert "一个简短会话意图" in schema["properties"]["reply_reference"]["description"]
    assert schema["required"] == ["msg_id", "reply_reference", "context_focus"]
    assert schema["properties"]["context_focus"]["maxItems"] == 3
    assert schema["properties"]["context_focus"]["items"]["enum"] == [
        "scene", "relationship", "continuity", "memory", "interests",
    ]


def test_legacy_reply_reference_contract_remains_available(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", False)

    reference_schema = reply_tool.get_tool_spec().parameters_schema["properties"]["reply_reference"]

    assert "maxLength" not in reference_schema
    assert "上下文关键信息与回复方向" in reference_schema["description"]


def test_compiler_schema_requires_typed_action_without_free_form_plan(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", True)
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", False)
    schema = reply_tool.get_tool_spec().parameters_schema
    assert "reply_reference" not in schema["properties"]
    assert schema["required"] == ["msg_id", "reply_act", "context_focus"]
    assert "close" in schema["properties"]["reply_act"]["enum"]


@pytest.mark.asyncio
async def test_compiler_rejects_free_form_action_before_generation(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", True)
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    result = await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments={
        "msg_id": "msg-1", "reply_act": "催他发截图，然后说自己去上课", "context_focus": [],
    }))
    assert not result.success
    generator.assert_not_called()


@pytest.mark.asyncio
async def test_compiler_drops_stale_reference_and_preserves_action(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", True)
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(side_effect=RuntimeError("stop before sending")))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **_: generator)
    await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments={
        "msg_id": "msg-1", "reply_act": "close", "context_focus": [], "reply_reference": "STALE_DRAFT",
    }))
    args = generator.generate_reply_with_context.call_args.kwargs["reply_tool_args"]
    assert args["reply_act"] == "close" and "reply_reference" not in args


@pytest.mark.asyncio
@pytest.mark.parametrize("quotes", [("\"", "\""), ("“", "”")])
async def test_leaked_metadata_is_removed_after_post_process_hook_before_splitting(reply_context, monkeypatch, quotes):
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", False)
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", False)
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(
        True, ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="好呀")),
    )))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **_: generator)
    monkeypatch.setattr(reply_tool, "_invoke_before_post_process_hook", AsyncMock(return_value=(
        f'<self_message time={quotes[0]}2030-01-02T12:34:56{quotes[1]}>\n好呀\n</self_message>', {"skip_post_process": True},
    )))
    process = AsyncMock(side_effect=ValueError("test ends before sending"))
    monkeypatch.setattr(reply_context, "post_process_reply_message_items_async", process)
    result = await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments={
        "msg_id": "msg-1", "reply_reference": "回应",
    }))
    assert not result.success
    assert process.call_args.args[0] == "好呀"


@pytest.mark.asyncio
async def test_persona_harness_rejects_oversized_reply_intent_before_generation(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)

    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={"msg_id": "msg-1", "reply_reference": "太" * 241}),
    )

    assert not result.success
    assert "240" in result.error_message
    generator.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("context_focus", [None, "scene", ["unknown"], ["scene", "scene"], ["scene"] * 4])
async def test_persona_harness_rejects_invalid_context_focus(
    reply_context, monkeypatch, context_focus,
):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    arguments = {"msg_id": "msg-1", "reply_reference": "回应问题"}
    if context_focus is not None:
        arguments["context_focus"] = context_focus

    result = await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments=arguments))

    assert not result.success
    assert "context_focus" in result.error_message
    generator.assert_not_called()


@pytest.mark.asyncio
async def test_persona_harness_forwards_context_focus_to_replyer(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(
        True, ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="记得呀")),
    )))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **kwargs: generator)
    monkeypatch.setattr(reply_tool, "_invoke_before_post_process_hook", AsyncMock(return_value=(
        "记得呀", {"skip_post_process": True},
    )))
    monkeypatch.setattr(
        reply_tool.send_service,
        "_send_to_target_with_message",
        AsyncMock(return_value=SimpleNamespace(message_id="sent-1")),
    )

    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={
            "msg_id": "msg-1",
            "reply_reference": "回应共同经历",
            "context_focus": ["memory", "relationship"],
        }),
    )

    assert result.success
    assert generator.generate_reply_with_context.call_args.kwargs["reply_tool_args"]["context_focus"] == [
        "memory", "relationship",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_args", [
    {},
    {"reply_reference": None},
    {"reply_reference": ""},
    {"reply_reference": " \n\t "},
    {"reply_reference": 123},
    {"reply_reference": False},
    {"reply_reference": []},
    {"reply_reference": {}},
])
async def test_reply_requires_nonempty_reference_before_generation(reply_context, monkeypatch, reference_args):
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={"msg_id": "msg-1", **reference_args}, reasoning="不能代替必填参考"),
    )
    assert not result.success
    assert "reply_reference" in result.error_message
    generator.assert_not_called()


@pytest.mark.asyncio
async def test_reply_sends_selected_picture_separately_from_at_text(reply_context, monkeypatch):
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(
        True, ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="看这张图")),
    )))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **kwargs: generator)
    monkeypatch.setattr(reply_tool, "_invoke_before_post_process_hook", AsyncMock(return_value=(
        "看这张图", {"skip_post_process": True},
    )))
    sender = AsyncMock(return_value=SimpleNamespace(message_id="sent-1"))
    monkeypatch.setattr(reply_tool.send_service, "_send_to_target_with_message", sender)
    arguments = {
        "msg_id": "msg-1", "reply_reference": "  展示第二张图片  ",
        "attach_at": ["msg-1"], "attach_pic": [{"msg_id": "msg-1", "index": 1}],
    }

    result = await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments=arguments))

    assert result.success
    assert sender.await_count == 2
    components = sender.await_args_list[0].kwargs["message_sequence"].components
    assert isinstance(components[0], AtComponent) and components[0].target_user_id == "user-1"
    assert not any(isinstance(component, ImageComponent) for component in components)
    image_components = sender.await_args_list[1].kwargs["message_sequence"].components
    assert len(image_components) == 1
    assert isinstance(image_components[0], ImageComponent) and image_components[0].binary_data == b"second"
    assert generator.generate_reply_with_context.call_args.kwargs["reply_tool_args"] == {
        "reply_reference": "展示第二张图片",
        "attach_at": ["msg-1"], "attach_pic": [{"msg_id": "msg-1", "index": 1}],
    }
    assert generator.generate_reply_with_context.call_args.kwargs["reply_reason"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description,expected_text",
    [("开心,得意,比心", "[表情包: 开心,得意,比心]"), ("  ", "[表情包]")],
)
async def test_reply_attached_emoji_renders_like_inbound_emoji(
    reply_context, monkeypatch, tmp_path, description, expected_text
):
    """附带表情的文本表示必须是 [表情包: 描述]，裸描述会作为自身历史回灌并被模型模仿进正文。"""

    from src.common.utils import image_path as image_path_module
    from src.emoji_system.emoji_manager import emoji_manager

    monkeypatch.setattr(global_config.emoji, "use_new_send_logic", True)
    monkeypatch.setattr(image_path_module, "PROJECT_ROOT", tmp_path)
    emoji_file = tmp_path / "emoji.gif"
    emoji_file.write_bytes(b"emoji-bytes")
    selected_emoji = SimpleNamespace(file_hash="emoji-hash", description=description, full_path=emoji_file)
    monkeypatch.setattr(
        emoji_manager, "get_emoji_by_hash", lambda emoji_hash: selected_emoji if emoji_hash == "emoji-hash" else None
    )
    monkeypatch.setattr(emoji_manager, "update_emoji_usage", lambda emoji: None)
    timestamp = datetime.now()
    candidate_item = UserMessageItem(
        meta=ContextItemMeta.create(timestamp=timestamp),
        parts=(ContextTextPart("表情包选择图"),),
    )
    reply_context.runtime._chat_history.append(
        EmojiCandidateMessage(
            item=candidate_item,
            emoji_hashes={1: "emoji-hash"},
            visible_text="表情包选择图",
            timestamp=timestamp,
        )
    )
    reply_result = ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="任务都完成啦"))
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(True, reply_result)))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **kwargs: generator)
    monkeypatch.setattr(
        reply_tool,
        "_invoke_before_post_process_hook",
        AsyncMock(return_value=("任务都完成啦", {"skip_post_process": True})),
    )
    sender = AsyncMock(return_value=SimpleNamespace(message_id="sent-1"))
    monkeypatch.setattr(reply_tool.send_service, "_send_to_target_with_message", sender)

    result = await reply_tool.handle_tool(
        reply_context,
        ToolInvocation("reply", arguments={"msg_id": "msg-1", "reply_reference": "庆祝任务完成", "attach_emoji": 1}),
    )

    assert result.success
    assert [call.kwargs["processed_plain_text"] for call in sender.call_args_list] == ["任务都完成啦", expected_text]
    # 发往平台的仍是表情图片二进制，content 只是该表情的文本表示
    emoji_component = sender.call_args_list[1].kwargs["message_sequence"].components[0]
    assert isinstance(emoji_component, EmojiComponent)
    assert emoji_component.binary_data == b"emoji-bytes"
    assert emoji_component.content == expected_text
    assert result.structured_content["reply_text"] == f"任务都完成啦{expected_text}"


@pytest.mark.asyncio
async def test_reply_keeps_async_split_and_quote_metadata_with_picture(reply_context, monkeypatch):
    splitter = AsyncMock(return_value=[ProcessedResponseSegment("今田见"), ProcessedResponseSegment("天", True)])
    monkeypatch.setattr(context_module, "process_llm_response_segments_async", splitter)
    items = await reply_context.post_process_reply_message_items_async(
        "今天见", {"attach_pic": [{"msg_id": "msg-1", "index": 0}]},
    )
    splitter.assert_awaited_once()
    assert [item.quote_previous for item in items] == [False, True, False]
    assert len(items[0].sequence.components) == 1
    assert all(isinstance(component, TextComponent) for component in items[1].sequence.components)
    assert len(items[2].sequence.components) == 1
    assert items[2].sequence.components[0].binary_data == b"first"


@pytest.mark.asyncio
async def test_picture_from_tool_media_history(reply_context):
    reply_context.runtime._chat_history.append(SessionBackedMessage(
        raw_message=MessageSequence([ImageComponent(binary_hash="tool-image", binary_data=b"tool-result")]),
        visible_text="工具图片",
        timestamp=datetime.now(),
        message_id="tool_result:call_x:1",
        source_kind="tool_media",
    ))
    items = await reply_context.post_process_reply_message_items_async(
        "找到图片了", {"attach_pic": [{"media_index": "tool_result:call_x:1", "index": 0}]},
        skip_post_process=True,
    )
    assert len(items) == 2
    assert isinstance(items[0].sequence.components[0], TextComponent)
    assert len(items[1].sequence.components) == 1
    assert items[1].sequence.components[0].binary_data == b"tool-result"


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [-1, 2, "invalid"])
async def test_bad_picture_index_fails(reply_context, index):
    with pytest.raises(ValueError, match="图片序号"):
        await reply_context._resolve_image_attachment({"msg_id": "msg-1", "index": index})


@pytest.mark.asyncio
async def test_unreadable_picture_does_not_shift_indices(reply_context, monkeypatch):
    source = reply_context.runtime.find_source_message_by_id("msg-1")
    first_image = source.raw_message.components[1]
    first_image.binary_data = b""
    monkeypatch.setattr(first_image, "load_image_binary", AsyncMock(side_effect=ValueError("图片数据损坏")))
    with pytest.raises(ValueError, match="图片数据损坏"):
        await reply_context._resolve_image_attachment({"msg_id": "msg-1", "index": 0})
    second_image = await reply_context._resolve_image_attachment({"msg_id": "msg-1", "index": 1})
    assert second_image.binary_data == b"second"


@pytest.mark.asyncio
async def test_picture_database_lookup_runs_off_event_loop(monkeypatch, tmp_path):
    from src.common.database import database
    from src.common.utils import image_path as image_path_module

    monkeypatch.setattr(image_path_module, "PROJECT_ROOT", tmp_path)
    image_path = tmp_path / "picture.png"
    image_path.write_bytes(b"stored-image")
    loop_thread = get_ident()
    lookup_threads = []

    @contextmanager
    def fake_db_session():
        lookup_threads.append(get_ident())
        yield SimpleNamespace(exec=lambda statement: SimpleNamespace(first=lambda: SimpleNamespace(full_path=image_path)))

    monkeypatch.setattr(database, "get_db_session", fake_db_session)
    image = ImageComponent(binary_hash="stored-image")
    await image.load_image_binary()
    assert image.binary_data == b"stored-image"
    assert lookup_threads and all(thread_id != loop_thread for thread_id in lookup_threads)


@pytest.mark.asyncio
@pytest.mark.parametrize("argument,config,field", [
    ({"attach_at": ["msg-1"]}, "chat", "enable_reply_at"),
    ({"attach_emoji": 1}, "emoji", "use_new_send_logic"),
])
async def test_disabled_attachment_rejected_before_generation(reply_context, monkeypatch, argument, config, field):
    monkeypatch.setattr(getattr(global_config, config), field, False)
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    result = await reply_tool.handle_tool(
        reply_context, ToolInvocation("reply", arguments={"msg_id": "msg-1", "reply_reference": "测试附件", **argument})
    )
    assert not result.success
    generator.assert_not_called()
    with pytest.raises(ValueError):
        await reply_context.post_process_reply_message_items_async("你好", argument, skip_post_process=True)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP", "ko"])
def test_attachment_prompt_describes_media_without_duplicating_mentions(reply_context, locale):
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator._load_prompt = lambda name, **kwargs: load_prompt(name, locale=locale, **kwargs)
    arguments: Dict[str, Any] = {"attach_pic": [{"msg_id": "msg-1"}], "attach_at": ["msg-1"], "attach_emoji": 26}
    prompt = generator._build_reply_attachment_prompt(
        chat_history=[], reply_message=reply_context.runtime.find_source_message_by_id("msg-1"), reply_tool_args=arguments,
    )
    assert "26" in prompt and "msg-1" in prompt and "小明" in prompt and "URL" in prompt
    # @ 由发送链附加，不应在 Replyer 文本提示中要求再输出一次具体提及。
    assert "@小明" not in prompt
    assert generator._build_reply_attachment_prompt(
        chat_history=[], reply_message=reply_context.runtime.find_source_message_by_id("msg-1"),
        reply_tool_args={"attach_at": ["msg-1"]},
    ) == ""
    assert "{attachments}" not in prompt
    metadata = list_prompt_templates(locale=locale)["reply_attachments"].metadata
    assert metadata.display_name and metadata.description


@pytest.mark.asyncio
async def test_handoff_unknown_source_stops_before_replyer(reply_context, monkeypatch):
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", False)
    generator = AsyncMock()
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", generator)
    result = await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments={
        "msg_id": "msg-1", "reply_reference": "回应", "context_focus": [],
        "evidence_refs": ["invented-source"],
    }))
    assert not result.success and "找不到" in result.error_message
    generator.assert_not_called()


@pytest.mark.asyncio
async def test_handoff_turn_id_comes_from_host_not_model(reply_context, monkeypatch):
    from src.chat.replyer.handoff import HANDOFF_TURN_KEY
    from src.core.tooling import ToolExecutionContext
    monkeypatch.setattr(global_config.experimental, "persona_harness_v2", True)
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", False)
    monkeypatch.setattr(global_config.experimental, "replyer_review", True)
    monkeypatch.setattr(reply_tool, "is_bot_self", lambda *_: False)
    result = ReplyGenerationResult(review_request={"reason": "event_owner_conflict", "evidence_message_ids": [], "note": "synthetic"})
    generate = AsyncMock(return_value=(False, result))
    monkeypatch.setattr(reply_tool.replyer_manager, "get_replyer", lambda **_: SimpleNamespace(generate_reply_with_context=generate))
    await reply_tool.handle_tool(reply_context, ToolInvocation("reply", arguments={
        "msg_id": "msg-1", "reply_reference": "回应", "context_focus": [], HANDOFF_TURN_KEY: "spoofed",
    }), ToolExecutionContext(metadata={"logical_turn_id": "host-turn"}))
    assert generate.call_args.kwargs["reply_tool_args"][HANDOFF_TURN_KEY] == "host-turn"
