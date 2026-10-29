"""Host-owned send boundaries; model text cannot authorize a proactive turn."""

from typing import Sequence

from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage


def latest_user_input_id(history: Sequence[LLMContextMessage]) -> str:
    """Capture the input before generation, not after a potentially long send."""
    for message in reversed(history):
        if isinstance(message, SessionBackedMessage) and message.source_kind == "user":
            return message.message_id or message.context_item_id
    return ""


def normal_reply_block_reason(
    history: Sequence[LLMContextMessage], *, target_is_self: bool, proactive: bool,
    answered_input_id: str = "",
) -> str:
    """Permit one ordinary reply per new input, independent of quoting settings."""
    if proactive:
        return ""
    if target_is_self:
        return "不能把自己的发言作为普通回复目标。请等待对方的新消息，不要继续补充或催促。"
    if answered_input_id and latest_user_input_id(history) == answered_input_id:
        return "已经发出回复，之后没有新的用户消息。请等待，不要换一个旧消息继续发送。"
    return ""
