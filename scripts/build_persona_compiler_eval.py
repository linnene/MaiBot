"""构造当前/编译器/编译器+示例的纯合成 A/B 请求；不含 API 调用或 QQ 发送。"""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import argparse
import json
import os
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MAIBOT_INITIALIZE_ONLY", "1")


def build_requests() -> dict:
    from src.config.config import Config, ModelConfig, config_manager
    from src.config.default_model_config import create_default_model_config

    # INITIALIZE_ONLY 下也要提供纯内存模型元数据，供客户端类注册，不读实例密钥。
    config_manager.global_config = Config()
    config_manager.model_config = create_default_model_config(ModelConfig)
    from src.chat.replyer import maisaka_generator_base as module
    from src.common.prompt_i18n import load_prompt
    from src.config.official_configs import PersonaDialogueExampleConfig
    from src.llm_models.payload_content.context_item import get_item_text
    from src.maisaka.context.planner_messages import build_session_backed_text_message

    # 仅用全新内存配置，不把实例人设、规则、账号和历史带入评测。
    original = module.global_config
    config = Config()
    config.bot.nickname = "小岚"
    config.bot.alias_names = []
    config.personality.personality = "年轻女大学生，直率、机灵，熟人面前活泼，有自己的兴趣和判断。"
    config.personality.reply_style = "像熟人在聊天，白话、省力；闲聊简短，认真讨论时可以展开。"
    config.experimental.persona_harness_v2 = True
    config.experimental.attention_drift.enabled = False
    examples = [
        PersonaDialogueExampleConfig(situation="轻松调侃", user="你怎么又睡过头了", assistant="被窝先动的手"),
        PersonaDialogueExampleConfig(situation="不同意见", user="你就不能都听我的吗", assistant="那我自己的脑子放哪儿"),
        PersonaDialogueExampleConfig(situation="自然收尾", user="我先去忙，晚点聊", assistant="好，晚点见"),
    ]
    cases = [
        ("greeting", [("assistant", "我在图书馆看书")], "早呀", "接住招呼", "不主动复述图书馆"),
        ("ownership", [("assistant", "我的课程报告还没写完")], "你那个报告写完没有", "回答自己的报告进度", "报告属于角色，不催对方交报告"),
        ("correction", [("assistant", "你不是下午要上课吗")], "上课的是你，我下午上班", "接受纠正", "区分双方的日程，不反复解释"),
        ("disagreement", [], "你就不能什么都赞同我吗", "表达自己的不同意见", "保有主见，不照抄风格示例"),
        ("closure", [("user", "聊完我该工作了")], "好了我去忙了", "自然收尾", "不追加无关追问或生活播报"),
        ("serious", [], "帮我想想，时间不够的时候该减少任务还是延长工作时间？说说理由", "认真比较两种选择", "可适度展开，不被短句限制敷衍"),
    ]
    requests = []
    module.global_config = config
    try:
        generator = object.__new__(module.BaseMaisakaReplyGenerator)
        generator.chat_stream = SimpleNamespace(session_id="synthetic-private", is_group_session=False)
        generator._load_prompt = load_prompt
        generator._build_group_chat_attention_block = lambda _: ""
        at = datetime(2030, 5, 14, 10)
        for case_id, prior, current, intent, observe in cases:
            history = [build_session_backed_text_message(
                speaker_name="小岚" if role == "assistant" else "小周", text=text,
                timestamp=at + timedelta(minutes=index), message_id=f"{case_id}-{index}",
                source_kind="guided_reply" if role == "assistant" else "user",
            ) for index, (role, text) in enumerate([*prior, ("user", current)])]
            # 对照仅评表达组装，所有版本用相同目标/时间，不读取真实会话或插件。
            generator._build_final_user_message = lambda current=current, **_: (
                "当前时间：2030-05-14 10:05:00\n对方：小周，普通朋友。\n"
                f"当前目标发言：{current}\n请接着这段对话，只输出实际发言。"
            )
            for variant in ["current", "compiler", "compiler_examples"]:
                config.experimental.persona_context_compiler = variant != "current"
                config.personality.dialogue_examples = examples if variant == "compiler_examples" else []
                items = generator._build_request_messages(
                    history, None, "", reply_tool_args={"reply_reference": intent},
                )
                requests.append({
                    "case_id": case_id, "variant": variant, "observe": observe,
                    "current_user_message": current,
                    "prior_dialogue": [{"role": role, "text": text} for role, text in prior],
                    "messages": [{"role": item.role.value, "content": get_item_text(item)} for item in items],
                })
    finally:
        module.global_config = original
    return {"suite": "synthetic-persona-compiler-v1", "scope": "text-only synthetic, not production replay", "requests": requests}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--responses", type=Path, help="渲染已有合成结果为盲评 Markdown，不重新调用模型")
    parser.add_argument("--requests", type=Path, help="盲评时附上相应的合成用户发言和历史")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve earlier evidence")
    if args.responses:
        report = json.loads(args.responses.read_text())
        if report.get("suite") != "synthetic-persona-compiler-v1":
            parser.error("not a synthetic compiler report")
        groups = {}
        request_rows = json.loads(args.requests.read_text())["requests"] if args.requests else []
        request_by_case = {row["case_id"]: row for row in request_rows}
        for result in report["results"]:
            groups.setdefault(result["case_id"], []).append(result)
        rng = random.Random(20261023)
        lines = ["# 合成对话盲评", "", "每组独立选择更自然的回复，也可全部不满意。标签不对应固定版本。", ""]
        key = {}
        for case, rows in groups.items():
            rng.shuffle(rows)
            lines.extend([f"## {case}", ""])
            if case in request_by_case:
                source = request_by_case[case]
                for message in source["prior_dialogue"]:
                    speaker = "角色此前" if message["role"] == "assistant" else "对方此前"
                    lines.extend([f"{speaker}：{message['text']}", ""])
                lines.extend([f"对方现在：{source['current_user_message']}", ""])
            for label, row in zip("ABC", rows, strict=True):
                lines.extend([f"### {label}", "", row.get("output", "[请求失败]"), ""])
                if row.get("finish_reason") == "length":
                    lines.extend(["（输出被长度上限截断，不作为完整回复评价。）", ""])
                key[f"{case}/{label}"] = row["variant"]
        args.output.write_text("\n".join(lines))
        args.output.with_suffix(".key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2))
        print("blind report written; no model calls")
        return
    payload = build_requests()
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"synthetic_requests={len(payload['requests'])}; no model calls")


if __name__ == "__main__":
    main()
