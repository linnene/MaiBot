"""主动任务的宿主可信上下文，不从模型参数或历史任务推断当前轮类型。"""

from datetime import datetime
from typing import Any, Sequence

from src.common.prompt_i18n import load_prompt
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage

PROACTIVE_CONTEXT_KEY = "_host_proactive_task"


def resolve_proactive_task(trigger_id: str, history: Sequence[LLMContextMessage]) -> dict[str, Any] | None:
    """只认当前触发消息对应的宿主任务；旧主动记录不能污染后续被动回复。"""
    if not trigger_id.startswith("proactive:"):
        return None
    for message in reversed(history):
        if (
            isinstance(message, SessionBackedMessage)
            and message.message_id == trigger_id
            and message.source_kind.startswith("plugin_proactive:")
        ):
            return {"task_id": trigger_id, "text": message.processed_plain_text}
    return None


def build_proactive_turn_prompt(
    task: dict[str, Any],
    history: Sequence[LLMContextMessage],
    *,
    anchor_id: str = "",
    anchor_time: datetime | None = None,
    now: datetime | None = None,
) -> str:
    """显式给出任务、完整时间和真实用户消息年龄；不推断用户当前处境。"""
    current = (now or datetime.now().astimezone()).astimezone()
    user_times = [
        message.timestamp.astimezone()
        for message in history
        if isinstance(message, SessionBackedMessage) and message.source_kind == "user"
    ]
    last_user = max(user_times) if user_times else None
    return load_prompt(
        "maisaka_proactive_turn",
        current_time=current.isoformat(timespec="seconds"),
        last_user_time=last_user.isoformat(timespec="seconds") if last_user else "unknown",
        silence_seconds=str(max(0, int((current - last_user).total_seconds()))) if last_user else "unknown",
        anchor_id=to_display_message_id(anchor_id) if anchor_id else "none",
        anchor_time=anchor_time.astimezone().isoformat(timespec="seconds") if anchor_time else "none",
        task_context=str(task["text"]),
    )
