"""确定性的角色表达上下文组装；不调用模型、不写记忆、不改变发送决策。"""

from dataclasses import replace
from typing import Sequence

import json

from src.common.prompt_i18n import load_prompt
from src.config.official_configs import PersonaDialogueExampleConfig
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItem,
    ContextItemBuilder,
    ContextTextPart,
    RoleType,
    get_item_text,
)

from .persona_policy import ReplyAct, select_expression_history
from .visible_reply import unwrap_self_history


def compile_persona_context(
    *,
    identity: str,
    voice: str,
    examples: Sequence[PersonaDialogueExampleConfig],
    history: Sequence[ContextItem],
    turn_context: str,
    act: str = "respond",
    reply_mode: str = "casual",
    casual_max_chars: int = 0,
    assistant_burst_limit: int = 0,
) -> list[ContextItem]:
    """只组合显式输入。示例不扮成 assistant 历史，任务不冒充对方说过的话。"""
    if not identity.strip():
        raise ValueError("Persona identity must not be empty")
    act = ReplyAct(act).value
    if reply_mode not in ("casual", "detailed"):
        raise ValueError("Unknown reply mode")
    if casual_max_chars < 0:
        raise ValueError("casual character budget must not be negative")
    selected_history, omitted = select_expression_history(history, assistant_burst_limit)
    # Visible assistant speech stays speech; time travels in host metadata rather
    # than an assistant-authored XML example the next reply might imitate.
    projected_history = []
    self_times = []
    for index, item in enumerate(selected_history):
        if (
            isinstance(item, AssistantMessageItem)
            and item.replay is None
            and all(isinstance(part, ContextTextPart) for part in item.parts)
        ):
            text = get_item_text(item)
            cleaned = unwrap_self_history(text)
            if cleaned and cleaned != text:
                item = replace(item, parts=(ContextTextPart(cleaned),))
            self_times.append(
                {
                    "history_index": index,
                    "source": "assistant",
                    "time": item.meta.timestamp.isoformat(timespec="seconds"),
                }
            )
        projected_history.append(item)
    demonstrations = []
    for example in examples:
        if not example.user.strip() or not example.assistant.strip():
            raise ValueError("A voice example must contain both user and assistant text")
        demonstrations.append(
            {
                "situation": example.situation,
                "user": example.user,
                "assistant": example.assistant,
            }
        )
    system_prompt = load_prompt(
        "persona_context_core",
        identity=identity.strip(),
        voice=voice.strip(),
        examples=json.dumps(demonstrations, ensure_ascii=False),
    )
    policy = {"reply_act": act, "mode": reply_mode}
    if reply_mode == "casual" and casual_max_chars:
        policy.update({"target_max_visible_chars": casual_max_chars, "target_sentences": 1})
    task = json.dumps(
        {
            "context": turn_context,
            "response_contract": policy,
            "history_selection": {"omitted_plain_assistant_items": omitted},
            "self_history_times": self_times,
        },
        ensure_ascii=False,
    )
    return [
        ContextItemBuilder().set_role(RoleType.System).add_text_content(system_prompt).build(),
        *projected_history,
        ContextItemBuilder()
        .set_role(RoleType.User)
        .add_text_content(
            load_prompt("persona_context_turn", task=task),
        )
        .build(),
    ]
