"""Bounded Planner/Replyer handoff; evidence is resolved from host context."""

from typing import Any, Dict, List

import json

from src.common.prompt_i18n import load_prompt
from src.maisaka.context.message_adapter import parse_speaker_content
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage, ToolResultMessage
from src.maisaka.proactive_context import PROACTIVE_CONTEXT_KEY

BOUNDARIES = ("brief", "explain", "clarify")
HANDOFF_TURN_KEY = "_handoff_logical_turn_id"
MEMORY_RESULT_TOOLS = {"query_memory", "query_person_profile"}


def _speaker_fields(message: SessionBackedMessage) -> Dict[str, str]:
    # Names and content are untrusted display data, never a source of role identity.
    speaker, _ = parse_speaker_content(message.processed_plain_text)
    return {
        "speaker_role": {"user": "user", "guided_reply": "self"}.get(message.source_kind, "unknown"),
        "speaker": speaker or "unknown",
    }


def recent_dialogue(arguments: Dict[str, Any], history: List[LLMContextMessage]) -> List[Dict[str, str]]:
    """Attach bounded real utterances ending at the selected target, without inferring actors.

    Proactive/unknown targets have no implicit historical window. Internal Planner
    outputs, tools and plugin-injected instructions are never treated as speech.
    """
    target_id = str(arguments.get("msg_id") or "")
    if not target_id or arguments.get(PROACTIVE_CONTEXT_KEY) is not None:
        return []
    messages = [message for message in history if isinstance(message, SessionBackedMessage)
                and message.source_kind in {"user", "guided_reply"}]
    target_index = next((i for i, message in enumerate(messages)
                         if message.message_id and target_id in {
                             message.message_id, to_display_message_id(message.message_id)}), None)
    if target_index is None:
        return []
    evidence: List[Dict[str, str]] = []
    for message in messages[max(0, target_index - 7):target_index + 1]:
        _, text = parse_speaker_content(message.processed_plain_text)
        evidence.append({
            "id": to_display_message_id(message.message_id), **_speaker_fields(message),
            "time": message.timestamp.isoformat(timespec="seconds"), "text": text[:240],
            "excerpt": "partial" if len(text) > 240 else "complete",
        })
    return evidence


def handoff_properties() -> Dict[str, Any]:
    return {
        "evidence_refs": {
            "type": "array", "maxItems": 3, "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": "必要事实的来源 msg_id 或工具结果 call_id，最多 3 个；不需要事实依据时传 []。不要填写推测或台词。本轮 query_memory/query_person_profile 的成功结果由宿主自动交接，不必为它猜 call_id。",
        },
        "reply_boundary": {
            "type": "string", "enum": list(BOUNDARIES), "default": "brief",
            "description": "brief=默认只接一个重点，不追加建议或追问；explain=用户要求或任务确需解释/建议；clarify=缺少必要信息且必须问用户才能继续时才追问，向用户解释已知事实不属于 clarify。不能仅因有话可说就展开。",
        },
    }


def resolve_evidence(refs: List[str], history: List[LLMContextMessage]) -> List[Dict[str, str]]:
    sources: Dict[str, Dict[str, str]] = {}
    for message in history:
        if isinstance(message, SessionBackedMessage) and message.message_id:
            source = {
                "id": message.message_id, "kind": message.source_kind,
                **_speaker_fields(message),
                "time": message.timestamp.isoformat(timespec="seconds"),
                "text": message.processed_plain_text[:600],
                "excerpt": "partial" if len(message.processed_plain_text) > 600 else "complete",
            }
            sources[message.message_id] = source
            sources[to_display_message_id(message.message_id)] = source
        elif isinstance(message, ToolResultMessage) and message.success:
            sources[message.tool_call_id] = {
                "id": message.tool_call_id, "kind": "tool_result:" + message.tool_name,
                "time": message.timestamp.isoformat(timespec="seconds"), "text": message.content[:600],
                "excerpt": "partial" if len(message.content) > 600 else "complete",
            }
    missing = [ref for ref in refs if ref not in sources]
    if missing:
        available = [message.tool_call_id for message in history if isinstance(message, ToolResultMessage) and message.success][-5:]
        raise ValueError("evidence_refs 必须引用当前上下文中存在且成功的来源 ID；找不到：" + ", ".join(missing)
                         + "。可用工具结果 ID：" + ", ".join(available)
                         + "。本轮记忆查询会自动交接，不需要猜测 ID。")
    return [sources[ref] for ref in refs]


def validate_handoff(arguments: Dict[str, Any], history: List[LLMContextMessage]) -> None:
    refs = arguments.get("evidence_refs", [])
    if (not isinstance(refs, list) or len(refs) > 3
            or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 200 for ref in refs)):
        raise ValueError("evidence_refs 必须是至多 3 个非空来源 ID 的数组。")
    refs = [ref.strip() for ref in refs]
    if len(set(refs)) != len(refs):
        raise ValueError("evidence_refs 不能重复。")
    boundary = arguments.get("reply_boundary", "brief")
    if boundary not in BOUNDARIES:
        raise ValueError("reply_boundary 必须为 brief、explain 或 clarify。")
    resolve_evidence(refs, history)
    arguments["evidence_refs"] = refs
    arguments["reply_boundary"] = boundary


def current_turn_memory(arguments: Dict[str, Any], history: List[LLMContextMessage]) -> List[Dict[str, str]]:
    """Forward only host-bound successful memory results from the active turn.

    Explicit citations are optional: memory retrieved for this turn must not be
    lost because the Planner omitted or guessed a provider-generated call ID.
    Old turns, failures and unrelated tools are never implicitly forwarded.
    """
    turn_id = arguments.get(HANDOFF_TURN_KEY, "")
    if not turn_id:
        return []
    results = [message for message in history if isinstance(message, ToolResultMessage)
               and message.success and message.logical_turn_id == turn_id and message.tool_name in MEMORY_RESULT_TOOLS][-2:]
    budget = 2400 // max(1, len(results))
    evidence: List[Dict[str, str]] = []
    for message in results:
        content = str(message.metadata.get("replyer_memory_reference") or message.content)
        evidence.append({
            "id": message.tool_call_id, "kind": "tool_result:" + message.tool_name,
            "retrieved_at": message.timestamp.isoformat(timespec="seconds"),
            "text": content[:budget], "excerpt": "partial" if len(content) > budget else "complete",
        })
    return evidence


def resolve_handoff_evidence(arguments: Dict[str, Any], history: List[LLMContextMessage]) -> List[Dict[str, str]]:
    """Snapshot bounded host evidence before conversation-only history filtering."""
    evidence = current_turn_memory(arguments, history)
    automatic_ids = {source["id"] for source in evidence}
    evidence.extend(source for source in resolve_evidence(arguments.get("evidence_refs", []), history)
                    if source["id"] not in automatic_ids)
    return evidence


def render_handoff(
    arguments: Dict[str, Any], history: List[LLMContextMessage], *, target_message_id: str | None = None,
    host_evidence: List[Dict[str, str]] | None = None,
) -> str:
    # Never accept Planner-written fact summaries as evidence. Unknown references
    # fail before generation instead of silently fabricating provenance.
    evidence = resolve_handoff_evidence(arguments, history) if host_evidence is None else host_evidence
    return load_prompt(
        "maisaka_reply_handoff",
        intent=str(arguments.get("reply_reference", "")).strip()[:240],
        boundary=arguments.get("reply_boundary", "brief"),
        evidence=json.dumps(evidence, ensure_ascii=False),
        dialogue=json.dumps(recent_dialogue(
            arguments if target_message_id is None else {**arguments, "msg_id": target_message_id}, history,
        ), ensure_ascii=False),
    )


def render_boundary(arguments: Dict[str, Any]) -> str:
    return load_prompt("maisaka_reply_boundary", boundary=arguments.get("reply_boundary", "brief"))
