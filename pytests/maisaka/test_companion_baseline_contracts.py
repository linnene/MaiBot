"""Synthetic guardrails for the retained companion slice; no production IDs or model calls."""

from types import SimpleNamespace

import pytest

from src.config.config import global_config
from src.config.official_configs import RelationshipIdentityBindingConfig, RelationshipIdentityConfig
from src.maisaka.emoji_behavior import EmojiBehaviorController
from src.maisaka.identity import build_relationship_identity_injection
from src.maisaka.turn_trigger.gates import FrequencyThresholdTurnGate


@pytest.mark.parametrize(
    "platform,user_id,verified", [("qq", "bound-user", True), ("qq", "other", False), ("other", "bound-user", False)]
)
def test_relationship_uses_platform_and_id_not_same_nickname(platform, user_id, verified):
    config = RelationshipIdentityConfig(
        enabled=True,
        bindings=[
            RelationshipIdentityBindingConfig(
                platform="qq",
                user_id="bound-user",
                display_name="same-nickname",
                relationship="哥哥",
                guidance="verified-only-marker",
            )
        ],
    )
    target = SimpleNamespace(
        platform=platform,
        message_id="synthetic-message",
        message_info=SimpleNamespace(
            user_info=SimpleNamespace(user_id=user_id, user_nickname="same-nickname", user_cardname="")
        ),
    )
    text = build_relationship_identity_injection(anchor_message=target, pending_messages=None, config=config)
    assert ("verified-only-marker" in text) is verified
    assert "bound-user" not in text


def test_emoji_cooldown_and_recent_dedup_are_session_scoped(monkeypatch):
    config = global_config.emoji.behavior
    monkeypatch.setattr(config, "enabled", True)
    monkeypatch.setattr(config, "cooldown_reply_turns", 2)
    monkeypatch.setattr(config, "recent_exclusion_count", 2)
    controller = EmojiBehaviorController()
    controller.record_reply("private", ["old-emoji"])
    assert controller.cooldown_remaining("private") == 2
    assert controller.cooldown_remaining("group") == 0
    controller.record_reply("private")
    controller.record_reply("private")
    assert controller.cooldown_remaining("private") == 0
    with pytest.raises(ValueError, match="刚刚用过"):
        controller.validate_attachment("private", "old-emoji")
    controller.validate_attachment("group", "old-emoji")


@pytest.mark.parametrize("group,expected", [(False, 30), (True, 768)])
def test_frequency_sparse_history_preserves_private_cap_and_group_policy(monkeypatch, group, expected):
    from src.maisaka.turn_trigger import gates

    monkeypatch.setattr(gates.time, "time", lambda: 1000)
    runtime = SimpleNamespace(
        chat_stream=SimpleNamespace(is_group_session=group),
        _get_recent_average_external_message_interval=lambda: 768,
        _last_external_message_received_at=1000,
        _last_message_received_at=1000,
    )
    gate = FrequencyThresholdTurnGate(runtime)
    triggered, delay, _ = gate._calculate_idle_compensation(pending_count=1, trigger_threshold=2)
    assert triggered is False
    assert delay == expected
    assert gate._calculate_idle_compensation(pending_count=0, trigger_threshold=2)[:2] == (False, None)
