from datetime import datetime
import json

import pytest

from src.chat.replyer.persona_context import compile_persona_context
from src.chat.replyer.persona_policy import measure_reply_length, select_expression_history
from src.llm_models.payload_content.context_item import ContextItemBuilder, ContextItemMeta, RoleType, get_item_text


def item(role, text):
    return (
        ContextItemBuilder()
        .set_role(role)
        .set_meta(ContextItemMeta.create(timestamp=datetime(2030, 1, 2, 12)))
        .add_text_content(text)
        .build()
    )


def task(items):
    return json.loads(get_item_text(items[-1]).strip().split("\n", 1)[1].rsplit("\n", 1)[0])


def test_history_cap_preserves_user_anchors_and_original_identity():
    user = item(RoleType.User, "资料是你的")
    own = [item(RoleType.Assistant, f"发资料 {n}") for n in range(20)]
    stop = item(RoleType.User, "先别说了")
    source = [user, *own, stop]
    selected, omitted = select_expression_history(source, 2)
    assert selected == [user, own[0], own[-1], stop]
    assert selected[1] is own[0] and selected[-1] is stop
    assert omitted == 18 and len(source) == 22
    assert select_expression_history(source, 0) == (source, 0)


def test_history_cap_never_crosses_images_or_tools():
    own = [item(RoleType.Assistant, str(n)) for n in range(4)]
    image = ContextItemBuilder().set_role(RoleType.Assistant).add_image_content("png", "synthetic").build()
    tool = (
        ContextItemBuilder()
        .set_role(RoleType.Tool)
        .set_meta(ContextItemMeta.create(logical_turn_id="test-turn"))
        .set_tool_call_id("test-call")
        .add_text_content("tool evidence")
        .build()
    )
    history = [*own[:2], image, *own[2:], tool]
    assert select_expression_history(history, 2) == (history, 0)


@pytest.mark.parametrize("invalid", [-1, 1])
def test_invalid_budget_fails_explicitly(invalid):
    with pytest.raises(ValueError):
        select_expression_history([], invalid)


def test_length_contract_is_separate_from_voice_and_detailed_mode():
    common = dict(
        identity="小岚",
        voice="温和有礼",
        examples=[],
        history=[],
        turn_context="你好",
        act="acknowledge",
        casual_max_chars=20,
    )
    first = task(compile_persona_context(**common))
    common["voice"] = "更活泼的语气"
    second = task(compile_persona_context(**common))
    assert first["response_contract"] == second["response_contract"]
    assert first["response_contract"]["target_max_visible_chars"] == 20
    assert first["response_contract"]["target_sentences"] == 1
    assert (
        "target_max_visible_chars"
        not in task(compile_persona_context(**common, reply_mode="detailed"))["response_contract"]
    )
    assert measure_reply_length("好 呀\n", 2) == {"visible_chars": 2, "within_budget": True}
    assert measure_reply_length("好呀。", 2)["within_budget"] is False


def test_own_time_is_host_metadata_not_assistant_markup():
    history = item(RoleType.Assistant, '<self_message time="2030-01-02T12:00:00">\n我的报告还没写\n</self_message>')
    compiled = compile_persona_context(
        identity="小岚", voice="温和", examples=[], history=[history], turn_context="报告写完了吗", act="answer"
    )
    assert get_item_text(compiled[1]) == "我的报告还没写"
    assert compiled[1].meta is history.meta
    assert task(compiled)["self_history_times"] == [
        {"history_index": 0, "source": "assistant", "time": "2030-01-02T12:00:00"}
    ]


@pytest.mark.parametrize(
    "kwargs", [{"act": "说自己也要回去，叮嘱他"}, {"reply_mode": "anything"}, {"casual_max_chars": -1}]
)
def test_free_form_act_or_invalid_contract_rejected(kwargs):
    with pytest.raises(ValueError):
        compile_persona_context(identity="小岚", voice="", examples=[], history=[], turn_context="", **kwargs)
