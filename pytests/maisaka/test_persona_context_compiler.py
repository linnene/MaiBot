"""纯合成对话：不读取生产快照、不调用模型、不发送 QQ。"""

import pytest

from src.chat.replyer.persona_context import compile_persona_context
from src.common.i18n import set_locale
from src.config.config import global_config
from src.config.official_configs import PersonaDialogueExampleConfig
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_item_text
from pytests.maisaka.test_retro_replyer_prompt import build_retro_generator, build_history_message


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_examples_are_demonstrations_not_chat_history(locale):
    set_locale(locale)
    user = ContextItemBuilder().set_role(RoleType.User).add_text_content("我今天去实验室了").build()
    try:
        items = compile_persona_context(
            identity="小岚，直率，有自己的主意",
            voice="随意、简短，认真时展开",
            examples=[PersonaDialogueExampleConfig(user="早点睡", assistant="等我把这页看完嘛")],
            history=[user],
            turn_context="reply to latest user",
            act="acknowledge",
        )
        assert items[1] is user
        assert len(items) == 3
        assert "早点睡" in get_item_text(items[0])
        assert "早点睡" not in get_item_text(items[1])
        assert '<generation_request source="host"' in get_item_text(items[-1])
        assert "acknowledge" in get_item_text(items[-1])
        assert user.meta.timestamp == items[1].meta.timestamp
    finally:
        set_locale("zh-CN")


def test_missing_identity_or_incomplete_example_fails_explicitly():
    common = dict(voice="", history=[], turn_context="")
    with pytest.raises(ValueError, match="identity"):
        compile_persona_context(identity="", examples=[], **common)
    with pytest.raises(ValueError, match="both"):
        compile_persona_context(identity="小岚", examples=[PersonaDialogueExampleConfig(user="hi")], **common)


def test_enabled_path_uses_shared_compiler_without_old_directing_layers(monkeypatch):
    generator = build_retro_generator(is_group_session=False)
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", True)
    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", True)
    monkeypatch.setattr(generator, "_build_personality_prompt", lambda: "小岚，直率、有好奇心")
    monkeypatch.setattr(generator, "_select_reply_style", lambda: "白话，闲聊简短")
    monkeypatch.setattr(generator, "_build_final_user_message", lambda **_: "当前时间：2030-01-02，当前用户：小周")
    monkeypatch.setattr(generator, "_select_temporary_reply_style", lambda: pytest.fail("no random voice"))
    history = build_history_message("我下午去实验室")
    items = generator._build_request_messages(
        chat_history=[history],
        reply_message=None,
        reply_reason="PLANNER_LONG_ANALYSIS",
        expression_habits="COPY_THIS_CATCHPHRASE",
        reply_tool_args={"reply_reference": "TAINTED_PLANNER_DRAFT", "reply_act": "answer"},
    )
    text = "\n".join(get_item_text(item) for item in items)
    assert "persona_context_compiler:v2" in text
    assert "我下午去实验室" in text and "answer" in text
    assert "TAINTED_PLANNER_DRAFT" not in text
    assert "PLANNER_LONG_ANALYSIS" not in text and "COPY_THIS_CATCHPHRASE" not in text
    assert items[1].meta.timestamp == history.timestamp


def test_disabled_path_is_unchanged(monkeypatch):
    generator = build_retro_generator(is_group_session=False)
    monkeypatch.setattr(global_config.experimental, "persona_context_compiler", False)
    monkeypatch.setattr(global_config.experimental, "replyer_retro_prompt", True)
    sentinel = object()
    monkeypatch.setattr(generator, "_build_retro_request_messages", lambda **_: sentinel)
    assert generator._build_request_messages([], None, "") is sentinel
