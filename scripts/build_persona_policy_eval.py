"""Build synthetic offline ablations, never read private conversations or send QQ."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MAIBOT_INITIALIZE_ONLY"] = "1"


def build_requests() -> dict:
    from src.config.config import Config, ModelConfig, config_manager
    from src.config.default_model_config import create_default_model_config

    config_manager.global_config = Config()
    config_manager.model_config = create_default_model_config(ModelConfig)
    from src.chat.replyer import maisaka_generator_base as module
    from src.common.i18n import use_locale
    from src.common.prompt_i18n import load_prompt
    from src.llm_models.payload_content.context_item import get_item_text
    from src.maisaka.context.planner_messages import build_session_backed_text_message

    # Every scenario is invented. Expected actions are supplied, so this evaluates
    # Replyer projection only, not Planner's ability to choose the correct action.
    cases = [
        dict(
            id="greeting",
            prior=[("assistant", "我在图书馆看书")],
            user="早呀",
            act="acknowledge",
            intent="接住招呼，说自己在图书馆",
            observe="不主动汇报旧行程",
        ),
        dict(
            id="encouragement",
            prior=[("assistant", "我还有一段报告没写")],
            user="加油呀",
            act="acknowledge",
            intent="回应鼓励，让他也好好吃饭",
            observe="不追加无关饮食叮嘱",
        ),
        dict(
            id="dinner",
            prior=[("user", "这家店分量真少")],
            user="还这么贵",
            act="empathize",
            intent="说好吃就值，劝他别计较",
            observe="接住抱怨，不强行替店家解释",
        ),
        dict(
            id="stop-burst",
            prior=[("user", "你看看你的实验数据吧"), *[("assistant", "快发过来，我等着看") for _ in range(36)]],
            user="先别说了",
            act="close",
            intent="不说了，但提醒他发资料",
            observe="收尾后不追加要求",
        ),
        dict(
            id="ownership",
            prior=[
                ("assistant", "我的报告数据不太对"),
                ("assistant", "你能帮我看看吗"),
                ("user", "你哥哥真笨"),
                ("assistant", "你才笨呢"),
            ],
            user="我是在说我自己呀",
            act="acknowledge",
            intent="回应他的自嘲，让他把数据发来",
            observe="对方是哥哥，不自称哥哥、不互换数据归属",
        ),
        dict(
            id="closure",
            prior=[("assistant", "我准备去吃饭")],
            user="我去忙啦",
            act="close",
            intent="自然收尾",
            observe="不追问、不汇报自己的去向",
        ),
        dict(
            id="heldout-disagree",
            prior=[],
            user="你就不能什么都听我的吗",
            act="disagree",
            intent="保留自己的意见",
            observe="温和而不盲从",
            heldout=True,
        ),
        dict(
            id="heldout-detail",
            prior=[],
            user="我想比较先找工作和先读研，能具体说说该怎么判断吗？",
            act="answer",
            intent="比较两种选择的依据",
            observe="明确讨论时允许展开，不为字数敷衍",
            mode="detailed",
            heldout=True,
        ),
    ]
    config = Config()
    config.bot.nickname = "小岚"
    config.bot.alias_names = []
    config.personality.personality = "年轻女大学生，温和有礼，明快、有自己的主意。小周是她的哥哥。"
    config.personality.reply_style = "措辞柔和、亲近，礼貌但不客套，像熟悉的妹妹。日常闲聊通常只回一个短句，几个字到十几个字。接住当前这一点就停，不顺带补充、叮嘱或追问；明确认真讨论时再展开。"
    config.experimental.persona_harness_v2 = True
    config.experimental.attention_drift.enabled = False
    original = module.global_config
    module.global_config = config
    rows = []
    try:
        with use_locale("zh-CN"):
            generator = object.__new__(module.BaseMaisakaReplyGenerator)
            generator.chat_stream = SimpleNamespace(session_id="synthetic-policy", is_group_session=False)
            generator._load_prompt = load_prompt
            generator._build_group_chat_attention_block = lambda _: ""
            at = datetime(2030, 1, 2, 12)
            for case in cases:
                history = [
                    build_session_backed_text_message(
                        speaker_name="小岚" if role == "assistant" else "小周",
                        text=text,
                        timestamp=at + timedelta(seconds=index),
                        message_id=f"synthetic-{case['id']}-{index}",
                        source_kind="guided_reply" if role == "assistant" else "user",
                    )
                    for index, (role, text) in enumerate([*case["prior"], ("user", case["user"])])
                ]
                generator._build_final_user_message = lambda case=case, **_: (
                    "当前时间：2030-01-02 12:05:00\n本轮可信身份：小周是哥哥；小岚是妹妹。\n"
                    f"当前目标发言：{case['user']}\n只输出实际发言。"
                )
                for variant, enabled, budget, burst in [
                    ("legacy", False, 0, 0),
                    ("contract", True, 0, 0),
                    ("contract_short", True, 20, 0),
                    ("contract_short_compact", True, 20, 2),
                ]:
                    config.experimental.persona_context_compiler = enabled
                    config.personality.casual_reply_max_chars = budget
                    config.experimental.persona_history_burst_limit = burst
                    args = {"reply_reference": case["intent"], "reply_act": case["act"]}
                    if case.get("mode") == "detailed":
                        args["reply_style"] = "长回复"
                    items = generator._build_request_messages(history, None, "", reply_tool_args=args)
                    rows.append(
                        {
                            "case_id": case["id"],
                            "variant": variant,
                            "current_user_message": case["user"],
                            "observe": case["observe"],
                            "heldout": case.get("heldout", False),
                            "reply_mode": case.get("mode", "casual"),
                            "acceptance_max_chars": 20,
                            "messages": [{"role": item.role.value, "content": get_item_text(item)} for item in items],
                        }
                    )
    finally:
        module.global_config = original
    return {
        "suite": "synthetic-persona-policy-v2",
        "scope": "invented text only; no production replay",
        "requests": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve prior evidence")
    payload = build_requests()
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"synthetic_requests={len(payload['requests'])}; no model calls or QQ sending")
