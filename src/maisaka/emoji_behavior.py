"""表情包发送倾向、冷却和近期去重状态。"""

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterable, Sequence

from src.config.config import global_config
from src.core.tooling import ToolAvailabilityContext


@dataclass
class _EmojiSessionState:
    """单个聊天流的轻量表情行为状态。"""

    replies_since_emoji: int = 1_000_000
    recent_hashes: deque[str] = field(default_factory=lambda: deque(maxlen=64))


class EmojiBehaviorController:
    """在进程内维护每个聊天流的表情冷却与最近使用记录。"""

    def __init__(self) -> None:
        self._states: dict[str, _EmojiSessionState] = {}
        self._lock = Lock()

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        return str(session_id or "").strip()

    def _get_state(self, session_id: str) -> _EmojiSessionState:
        normalized_session_id = self._normalize_session_id(session_id)
        return self._states.setdefault(normalized_session_id, _EmojiSessionState())

    def cooldown_remaining(self, session_id: str) -> int:
        """返回再次发送表情前还需要经过的普通回复轮数。"""

        config = global_config.emoji.behavior
        if not config.enabled or config.cooldown_reply_turns <= 0:
            return 0
        with self._lock:
            state = self._get_state(session_id)
            return max(0, config.cooldown_reply_turns - state.replies_since_emoji)

    def recent_hashes(self, session_id: str) -> tuple[str, ...]:
        """按由旧到新的顺序返回当前会话需排除的表情哈希。"""

        config = global_config.emoji.behavior
        if not config.enabled or config.recent_exclusion_count <= 0:
            return ()
        with self._lock:
            state = self._get_state(session_id)
            return tuple(state.recent_hashes)[-config.recent_exclusion_count :]

    def filter_candidates(self, session_id: str, candidates: Sequence[object]) -> list[object]:
        """从候选池移除当前会话最近发送过的表情。"""

        excluded_hashes = set(self.recent_hashes(session_id))
        if not excluded_hashes:
            return list(candidates)
        return [
            candidate
            for candidate in candidates
            if str(getattr(candidate, "file_hash", "") or "").strip() not in excluded_hashes
        ]

    def validate_attachment(self, session_id: str, emoji_hash: str) -> None:
        """校验表情冷却与重复约束，不满足时直接拒绝发送。"""

        config = global_config.emoji.behavior
        if not config.enabled:
            return
        remaining = self.cooldown_remaining(session_id)
        if remaining > 0:
            raise ValueError(f"表情包仍在冷却中，还需经过 {remaining} 轮普通回复。")
        if emoji_hash in set(self.recent_hashes(session_id)):
            raise ValueError("这个表情包刚刚用过，请从新的表情包列表中换一个。")

    def record_reply(self, session_id: str, emoji_hashes: Iterable[str] = ()) -> None:
        """在一次 reply 工具完整发送成功后推进会话状态。"""

        normalized_hashes = [str(emoji_hash or "").strip() for emoji_hash in emoji_hashes]
        normalized_hashes = [emoji_hash for emoji_hash in normalized_hashes if emoji_hash]
        with self._lock:
            state = self._get_state(session_id)
            if normalized_hashes:
                state.replies_since_emoji = 0
                state.recent_hashes.extend(normalized_hashes)
            else:
                state.replies_since_emoji += 1

    def reset(self) -> None:
        """清空进程内状态，供测试和重载场景使用。"""

        with self._lock:
            self._states.clear()


emoji_behavior_controller = EmojiBehaviorController()


def build_emoji_behavior_guidance(context: ToolAvailabilityContext | None) -> str:
    """构造给 Planner 的简短行为说明；倾向值是决策参考，不是硬随机抽签。"""

    config = global_config.emoji.behavior
    if not config.enabled:
        return "在表情比文字更自然时再查看候选，不必为了使用功能而强行发送。"

    is_group_chat = context.is_group_chat if context is not None else None
    tendency = config.group_tendency if is_group_chat is True else config.private_tendency
    percentage = round(tendency * 100)
    chat_kind = "群聊" if is_group_chat is True else "私聊"
    guidance = (
        f"在轻松、调侃、撒娇、惊讶或接梗等适合表情的场景，{chat_kind}可大致按 {percentage}% 的倾向考虑使用；"
        "这是语境倾向，不是每轮硬抽签。对方刚发表情时，可在合适语境下更积极跟表情。"
    )
    if context is not None:
        remaining = emoji_behavior_controller.cooldown_remaining(context.session_id)
        if remaining > 0:
            guidance += f" 当前仍在冷却中，还要经过 {remaining} 轮普通回复，本轮不要调用表情工具。"
    if config.allow_emoji_only:
        guidance += " 能靠一个表情自然表达时，可以只发表情，不必硬配文字。"
    return guidance
