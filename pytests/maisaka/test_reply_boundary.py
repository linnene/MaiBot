from datetime import datetime

import pytest

from src.maisaka.context.planner_messages import build_session_backed_text_message
from src.maisaka.reply_boundary import latest_user_input_id, normal_reply_block_reason


def message(kind: str):
    return build_session_backed_text_message(
        speaker_name="synthetic", text="synthetic", timestamp=datetime(2026, 1, 1), source_kind=kind,
    )


@pytest.mark.parametrize("history,self_target,proactive,blocked", [
    (["user"], False, False, False),
    (["user", "guided_reply"], False, False, True),
    (["user", "guided_reply", "user"], False, False, False),
    (["user"], True, False, True),
    (["user", "guided_reply", "memory_reference"], False, False, True),
    (["user", "guided_reply"], True, True, False),
])
def test_send_boundary(history, self_target, proactive, blocked):
    messages = [message(kind) for kind in history]
    first_user = next(m for m in messages if m.source_kind == "user")
    reason = normal_reply_block_reason(
        messages, target_is_self=self_target, proactive=proactive,
        answered_input_id=first_user.context_item_id if "guided_reply" in history else "",
    )
    assert bool(reason) is blocked


def test_new_message_during_generation_is_not_consumed_by_previous_send():
    history = [message("user")]
    generating_input_id = latest_user_input_id(history)
    history.extend([message("user"), message("guided_reply")])
    assert normal_reply_block_reason(
        history, target_is_self=False, proactive=False, answered_input_id=generating_input_id,
    ) == ""


def test_memory_recall_retains_provenance_without_inventing_subject():
    from src.maisaka.memory.heuristic_injector import HeuristicMemoryInjector
    from src.services.memory_service import MemoryHit

    text = HeuristicMemoryInjector._format_reference([
        MemoryHit(content="机器人说自己下午去图书馆", source="chat_summary:synthetic", hit_type="paragraph",
                  metadata={"event_time_start": "2026-01-01T12:00:00"}),
    ], max_chars=2400)
    assert '"event_subject": "unknown"' in text
    assert "2026-01-01T12:00:00" in text
    assert "chat_summary:synthetic" in text
    assert "Bot schedules are not user schedules" in text
