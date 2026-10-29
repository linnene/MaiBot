"""Replyer-to-Planner control channel; never interpret visible prose as control."""

from dataclasses import dataclass
from typing import Any, Dict, Sequence

from src.common.prompt_i18n import load_prompt
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption

REVIEW_TOOL = "request_reply_review"
REVIEW_REASONS = ("speaker_conflict", "event_owner_conflict", "stale_context", "unsupported_claim", "intent_conflict")


def review_tool() -> ToolOption:
    """Expose an optional function call alongside normal short speech."""
    return ToolOption(
        name=REVIEW_TOOL, description=load_prompt("maisaka_reply_review"),
        parameters_schema_override={
            "type": "object", "additionalProperties": False,
            "properties": {
                "reason": {"type": "string", "enum": list(REVIEW_REASONS)},
                "evidence_message_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                "note": {"type": "string", "maxLength": 240},
            },
            "required": ["reason", "evidence_message_ids", "note"],
        },
    )


def parse_review_calls(calls: Sequence[ToolCall]) -> Dict[str, Any] | None:
    """Any tool call suppresses speech; malformed calls are explicit protocol errors."""
    if not calls:
        return None
    invalid = {"reason": "invalid_review_call", "evidence_message_ids": [], "note": "Invalid Replyer control call."}
    if len(calls) != 1 or calls[0].func_name != REVIEW_TOOL:
        return invalid
    args = calls[0].args
    if not isinstance(args, dict) or set(args) != {"reason", "evidence_message_ids", "note"}:
        return invalid
    ids, note = args["evidence_message_ids"], args["note"]
    if (args["reason"] not in REVIEW_REASONS or not isinstance(ids, list) or len(ids) > 4
            or any(not isinstance(item, str) or not item or len(item) > 128 for item in ids)
            or not isinstance(note, str) or not note.strip() or len(note) > 240):
        return invalid
    return {"reason": args["reason"], "evidence_message_ids": list(ids), "note": note.strip()}


@dataclass
class ReplyReviewBudget:
    """One replan per host input, shared by all reply calls for that input."""

    input_key: str = ""
    review_count: int = 0

    def exhausted(self, input_key: str) -> bool:
        if self.input_key != input_key:
            self.input_key, self.review_count = input_key, 0
        return self.review_count >= 2

    def record(self, input_key: str) -> bool:
        """Return whether Planner may retry; a second veto stops the turn."""
        self.exhausted(input_key)
        self.review_count += 1
        return self.review_count < 2


def review_history_content(review: Dict[str, Any], *, retry_allowed: bool) -> Dict[str, Any]:
    """A tool result, not a fabricated user message or a visible reply."""
    return {"status": "needs_review", "sent": False, "retry_allowed": retry_allowed,
            "review": review, "instruction": (
                "Recheck the cited real messages and revise the reply intent. Do not quote internal feedback."
                if retry_allowed else "Review budget exhausted. Wait for new user input; send nothing."
            )}
