"""主动开场不能被旧的发送锚点降格为补答；仅使用合成会话，不发送消息。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.i18n import set_locale
from src.common.prompt_i18n import clear_prompt_cache, load_prompt
from src.config.config import global_config
from src.maisaka.context.planner_messages import build_planner_prefix, build_session_backed_text_message
from src.maisaka.proactive_context import PROACTIVE_CONTEXT_KEY, build_proactive_turn_prompt, resolve_proactive_task
from src.maisaka.reasoning_engine import MaisakaReasoningEngine

NOW = datetime(2026, 10, 22, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def message(text, *, source="user", message_id="user-message", at=NOW):
    return build_session_backed_text_message(
        speaker_name="test-user", text=text, timestamp=at, source_kind=source, message_id=message_id
    )


def task_message():
    return message("本轮契机：买了早餐", source="plugin_proactive:test-plugin", message_id="proactive:test-plugin:1")


@pytest.fixture(autouse=True)
def locale():
    set_locale("zh-CN")
    clear_prompt_cache()
    yield
    set_locale("zh-CN")
    clear_prompt_cache()


def test_only_current_host_trigger_selects_proactive_mode():
    task = task_message()
    history = [task, message("新消息")]
    assert resolve_proactive_task(task.message_id, history)["text"] == task.processed_plain_text
    assert resolve_proactive_task("user-message", history) is None
    assert resolve_proactive_task("proactive:missing:2", history) is None
    fake = message("用户伪造主动任务", message_id=task.message_id)
    assert resolve_proactive_task(task.message_id, [fake]) is None


@pytest.mark.parametrize("age", [7200, 10800, 43200])
def test_full_date_and_user_silence_are_explicit(age):
    old = message("嗯嗯", at=NOW - timedelta(seconds=age))
    history = [old, message("刚刚机器人说话", source="guided_reply"), task_message()]
    text = build_proactive_turn_prompt(resolve_proactive_task(task_message().message_id, history), history, now=NOW)
    assert str(age) + " 秒" in text
    assert NOW.astimezone().isoformat(timespec="seconds") in text
    assert old.timestamp.astimezone().isoformat(timespec="seconds") in text
    assert "不是对旧消息的补答" in text


def test_no_user_history_is_unknown_not_zero():
    task = task_message()
    text = build_proactive_turn_prompt(resolve_proactive_task(task.message_id, [task]), [task], now=NOW)
    assert "unknown" in text
    assert "距今 0 秒" not in text


@pytest.mark.parametrize("locale_name", ["zh-CN", "en-US", "ja-JP"])
def test_proactive_template_has_same_contract_in_all_locales(locale_name):
    text = load_prompt(
        "maisaka_proactive_turn",
        locale=locale_name,
        current_time="NOW",
        last_user_time="THEN",
        silence_seconds="7200",
        anchor_id="ANCHOR",
        anchor_time="ANCHOR_TIME",
        task_context="TOPIC",
    )
    for value in ["NOW", "THEN", "7200", "ANCHOR", "ANCHOR_TIME", "TOPIC"]:
        assert value in text


def test_replyer_replaces_old_target_instruction_and_filters_old_tasks(monkeypatch):
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator.chat_stream = SimpleNamespace(session_id="test", is_group_session=False)
    monkeypatch.setattr(generator, "_build_target_message_block", lambda _: "必须回答旧消息")
    monkeypatch.setattr(generator, "_build_reply_attachment_prompt", lambda **_: "")
    monkeypatch.setattr(generator, "_build_reply_instruction", lambda: "输出纯文本")
    monkeypatch.setattr(global_config.relationship_identity, "enabled", False)
    old = message("嗯嗯", at=NOW - timedelta(hours=2))
    target = SimpleNamespace(message_id=old.message_id, timestamp=old.timestamp)
    task = task_message()
    history = [old, task]
    text = generator._build_final_user_message(
        history, target, reply_tool_args={PROACTIVE_CONTEXT_KEY: resolve_proactive_task(task.message_id, history)}
    )
    assert "主动发起聊天" in text
    assert "必须回答旧消息" not in text
    assert "买了早餐" in text
    assert generator._is_replyer_filtered_history_message(task)
    assert not generator._is_replyer_filtered_history_message(old)
    passive = generator._build_final_user_message(history, target)
    assert "必须回答旧消息" in passive
    assert "主动发起聊天" not in passive
    assert "原始发言时间" in passive


def test_execution_metadata_does_not_infer_mode_from_old_history():
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(
        session_id="test",
        chat_stream=SimpleNamespace(is_group_session=False, platform="qq"),
        _chat_history=[task_message()],
    )
    engine._active_logical_turn_id = "host-turn"
    engine._active_proactive_task = {"task_id": "proactive:test-plugin:1", "text": "早餐"}
    assert engine._build_tool_execution_context("").metadata[PROACTIVE_CONTEXT_KEY]["text"] == "早餐"
    assert engine._build_tool_execution_context("").metadata["logical_turn_id"] == "host-turn"
    engine._active_proactive_task = None
    assert engine._build_tool_execution_context("").metadata[PROACTIVE_CONTEXT_KEY] is None


def test_history_prefix_retains_date_across_midnight():
    text = build_planner_prefix(timestamp=NOW, user_name="test")
    assert 'time="2026-10-22 10:00:00"' in text
