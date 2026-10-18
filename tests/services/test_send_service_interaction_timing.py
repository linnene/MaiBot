from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.message import SessionMessage
from src.services import send_service


@pytest.mark.asyncio
async def test_simulated_typing_reports_planned_and_actual_duration(monkeypatch):
    message = SessionMessage(message_id="message-1", timestamp=datetime.now(), platform="example")
    message.session_id = "typing-session"
    runtime = SimpleNamespace(
        update_interaction_stage=Mock(),
        emit_interaction_timing_completed=AsyncMock(),
    )
    monkeypatch.setitem(heartflow_manager.heartflow_chat_list, message.session_id, runtime)
    monkeypatch.setattr(send_service.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(send_service, "time", SimpleNamespace(monotonic=Mock(side_effect=[10.0, 11.25])))

    await send_service._wait_for_simulated_typing(message, 2.5)

    assert runtime.update_interaction_stage.call_args_list[0].kwargs == {
        "stage": "模拟打字",
        "detail": "预计等待 2.5 秒后发送",
        "interaction_kind": "typing",
        "planned_duration_seconds": 2.5,
    }
    assert runtime.update_interaction_stage.call_args_list[1].kwargs == {
        "stage": "发送中",
        "detail": "模拟打字完成，实际用时 1.2 秒",
        "interaction_kind": "typing",
        "planned_duration_seconds": 2.5,
    }
    runtime.emit_interaction_timing_completed.assert_awaited_once_with(
        step="interaction.typing.completed",
        title="模拟打字完成",
        planned_seconds=2.5,
        elapsed_seconds=1.25,
    )
