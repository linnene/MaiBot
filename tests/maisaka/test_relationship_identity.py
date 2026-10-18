from datetime import datetime

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.official_configs import (
    RelationshipIdentityBindingConfig,
    RelationshipIdentityConfig,
)
from src.maisaka.identity.relationship import build_relationship_identity_injection


def make_message(
    *,
    platform: str,
    user_id: str,
    nickname: str,
    text: str = "hello",
    message_id: str = "message-001",
) -> SessionMessage:
    message = SessionMessage(message_id=message_id, timestamp=datetime.now(), platform=platform)
    message.session_id = "session-test"
    message.message_info = MessageInfo(user_info=UserInfo(user_id=user_id, user_nickname=nickname))
    message.raw_message = MessageSequence([TextComponent(text)])
    message.processed_plain_text = text
    return message


def make_config() -> RelationshipIdentityConfig:
    return RelationshipIdentityConfig(
        enabled=True,
        bindings=[
            RelationshipIdentityBindingConfig(
                platform="example",
                user_id="stable-user-a",
                relationship="trusted_contact",
                display_name="Anchor",
                exclusive=True,
                guidance="可以分享已确认的共同安排",
            )
        ],
    )


def test_relationship_matches_platform_and_stable_user_id_without_leaking_id():
    message = make_message(platform="EXAMPLE", user_id="stable-user-a", nickname="Changed nickname")

    injection = build_relationship_identity_injection(
        anchor_message=message,
        pending_messages=[],
        config=make_config(),
    )

    assert 'Anchor [对应消息 msg_id="message-001"]：trusted_contact' in injection
    assert "可以分享已确认的共同安排" in injection
    assert "stable-user-a" not in injection
    assert "平台与稳定用户 ID 校验" in injection


def test_nickname_claim_cannot_impersonate_protected_relationship():
    message = make_message(platform="example", user_id="different-user", nickname="Anchor")

    injection = build_relationship_identity_injection(
        anchor_message=message,
        pending_messages=[],
        config=make_config(),
    )

    assert "未命中任何可信关系绑定" in injection
    assert "受保护关系：trusted_contact" in injection
    assert "different-user" not in injection


def test_disabled_relationship_identity_produces_no_injection():
    message = make_message(platform="example", user_id="stable-user-a", nickname="Anchor")
    config = make_config().model_copy(update={"enabled": False})

    assert not build_relationship_identity_injection(
        anchor_message=message,
        pending_messages=[],
        config=config,
    )


def test_duplicate_stable_identity_is_rejected():
    binding = RelationshipIdentityBindingConfig(
        platform="example",
        user_id="same-user",
        relationship="trusted_contact",
    )

    with pytest.raises(ValueError, match="关系身份绑定重复"):
        RelationshipIdentityConfig(
            enabled=True,
            bindings=[binding, binding.model_copy(update={"platform": "EXAMPLE"})],
        )
