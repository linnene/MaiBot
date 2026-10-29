import os
from pathlib import Path

from scripts.evaluate_replyer_harness import _bootstrap_instance_dir, build_minimal_candidate_items


def _item(item_type: str, text: str) -> dict:
    return {
        "item_type": item_type,
        "meta": {"item_id": "test", "timestamp": "2026-10-20T00:00:00"},
        "parts": [{"type": "text", "text": text}],
    }


def _texts(items: list[dict]) -> list[str]:
    return [str(item["parts"][0]["text"]) for item in items]


def test_minimal_candidate_removes_directing_layers_and_keeps_history() -> None:
    snapshot = {
        "request_items": [
            _item(
                "SystemMessageItem",
                "稳定人格\n你可以参考【回复信息参考】中的信息，但是视情况而定，不用完全遵守。",
            ),
            _item("UserMessageItem", '<message user="SyntheticUser">上课了'),
            _item("UserMessageItem", "【表达习惯参考】\n- 照抄套路"),
            _item("UserMessageItem", "<reply_intent>\n先安慰再追问\n</reply_intent>"),
            {
                "item_type": "UserMessageItem",
                "meta": {"item_id": "image", "timestamp": "2026-10-20T00:00:00"},
                "parts": [{"type": "image", "reference": {"path": "data/emoji/missing.jpeg"}}],
            },
            _item(
                "UserMessageItem",
                "当前时间：2026-10-20 15:56:54\n\n"
                "你想要回复的消息是 SyntheticUser 发送的 msg_id为 1 的消息\n"
                "- 发言内容：我上课了\n\n【对话连续性】追问他的课程",
            ),
            _item("UserMessageItem", "请简短的回复，允许奇怪表达"),
        ]
    }

    texts = _texts(build_minimal_candidate_items(snapshot))

    assert len(texts) == 3
    assert texts[1] == '<message user="SyntheticUser">上课了'
    assert "表达习惯" not in "\n".join(texts)
    assert "reply_intent" not in "\n".join(texts)
    assert "对话连续性" not in "\n".join(texts)
    assert "我上课了" in texts[-1]
    assert "不确定的经历或细节" in texts[0]
    assert "按原句主语区分双方经历" in texts[0]
    assert "不要主动汇报自己的行程和状态" in texts[0]


def test_minimal_candidate_does_not_mutate_snapshot() -> None:
    original_system = "稳定人格"
    snapshot = {"request_items": [_item("SystemMessageItem", original_system)]}

    build_minimal_candidate_items(snapshot)

    assert snapshot["request_items"][0]["parts"][0]["text"] == original_system


def test_bootstrap_instance_dir_accepts_equals_form(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MAIBOT_INSTANCE_DIR", raising=False)

    _bootstrap_instance_dir([f"--instance-dir={tmp_path}"])

    assert Path(os.environ["MAIBOT_INSTANCE_DIR"]) == tmp_path.resolve()
