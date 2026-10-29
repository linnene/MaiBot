"""Typed expression handoff and loss-bounded history selection; no semantic guessing."""

from enum import Enum
from typing import Sequence

from src.llm_models.payload_content.context_item import AssistantMessageItem, ContextItem, ContextTextPart


class ReplyAct(str, Enum):
    RESPOND = "respond"
    ACKNOWLEDGE = "acknowledge"
    ANSWER = "answer"
    EMPATHIZE = "empathize"
    CLARIFY = "clarify"
    DISAGREE = "disagree"
    CLOSE = "close"


def select_expression_history(history: Sequence[ContextItem], burst_limit: int = 0) -> tuple[list[ContextItem], int]:
    """Cap only uninterrupted plain self-speech, retaining its beginning and end.

    This is a request projection, not deletion from storage. A zero limit disables
    selection. User, tool, image and provider replay items are never removed, and
    form barriers: tool/reasoning chains and original item identities stay intact.
    """
    if burst_limit < 0 or burst_limit == 1:
        raise ValueError("assistant burst limit must be zero or at least two")
    if not burst_limit:
        return list(history), 0
    selected: list[ContextItem] = []
    burst: list[ContextItem] = []
    omitted = 0

    def flush() -> None:
        nonlocal omitted
        if len(burst) > burst_limit:
            selected.extend([burst[0], *burst[-(burst_limit - 1) :]])
            omitted += len(burst) - burst_limit
        else:
            selected.extend(burst)
        burst.clear()

    for item in history:
        if (
            isinstance(item, AssistantMessageItem)
            and item.replay is None
            and item.phase is None
            and all(isinstance(part, ContextTextPart) for part in item.parts)
        ):
            burst.append(item)
        else:
            flush()
            selected.append(item)
    flush()
    return selected, omitted


def measure_reply_length(text: str, casual_max_chars: int) -> dict[str, int | bool]:
    """Offline acceptance metric; never truncate or replace an actual utterance."""
    count = len("".join(text.split()))
    return {"visible_chars": count, "within_budget": not casual_max_chars or count <= casual_max_chars}
