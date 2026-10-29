# ruff: noqa: E402

"""Offline A/B replay for Replyer prompt-harness experiments.

The script reads persisted Replyer request snapshots and asks the configured
``replyer`` model for a candidate response.  It never enters the chat sending
pipeline, so generated text cannot be delivered to QQ.

This is NOT an offline-model or local-only test: unless --dry-run is used,
the retained snapshot persona and conversation text are sent to the configured
model provider. Obtain permission for that exact corpus before running it.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(1, str(PROJECT_ROOT))


def _bootstrap_instance_dir(argv: list[str]) -> None:
    """Set the instance root before importing modules that bind config paths."""

    for index, argument in enumerate(argv):
        if argument == "--instance-dir" and index + 1 < len(argv):
            os.environ["MAIBOT_INSTANCE_DIR"] = str(Path(argv[index + 1]).expanduser().resolve())
            return
        if argument.startswith("--instance-dir="):
            os.environ["MAIBOT_INSTANCE_DIR"] = str(Path(argument.split("=", 1)[1]).expanduser().resolve())
            return


_bootstrap_instance_dir(sys.argv[1:])
# Importing MaiBot modules must not initialize or upgrade configuration before
# the evaluator has validated its CLI arguments.
os.environ.setdefault("MAIBOT_INITIALIZE_ONLY", "1")

EXPRESSION_PREFIXES = (
    "【表达习惯参考",
    "【表达方式参考",
)
STYLE_DIRECTIVE_PREFIXES = (
    "请简短的回复",
    "你的说话风格可以尝试：",
)
REFERENCE_SYSTEM_LINE = "你可以参考【回复信息参考】中的信息，但是视情况而定，不用完全遵守。"
FACTUAL_UNCERTAINTY_LINE = "不确定的经历或细节不要补成具体事实。"
TURN_OWNERSHIP_LINE = "聊天记录中 user 标记的是对方发言，assistant 或 self_message 标记的是你的发言；按原句主语区分双方经历。"
LIFE_RELEVANCE_LINE = "除非对方问起、当前话题直接相关，或确实有一件值得说的事，不要主动汇报自己的行程和状态。"


def _read_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("request_items"), list):
        raise ValueError(f"{path} is not a persisted Replyer request snapshot")
    return payload


def _item_text(item: dict[str, Any]) -> str:
    return "".join(
        str(part.get("text") or "")
        for part in item.get("parts") or []
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _replace_item_text(item: dict[str, Any], text: str) -> dict[str, Any]:
    rewritten = copy.deepcopy(item)
    for part in rewritten.get("parts") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            part["text"] = text
            return rewritten
    raise ValueError("cannot rewrite a snapshot item without a text part")


def _minimal_target_message(text: str) -> str:
    """Keep only time and the explicit target-message block."""

    time_match = re.search(r"^当前时间：[^\n]+", text)
    target_match = re.search(
        r"(你想要回复的消息是[\s\S]*?(?:\n- 发言内容：[^\n]*))",
        text,
    )
    if target_match is None:
        return text
    sections = []
    if time_match is not None:
        sections.append(time_match.group(0))
    sections.append(target_match.group(1).strip())
    sections.append("直接接着这段聊天回复，只输出实际发言。")
    return "\n\n".join(sections)


def build_minimal_candidate_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Remove per-turn directing layers while retaining persona and chat history."""

    candidate: list[dict[str, Any]] = []
    for raw_item in snapshot["request_items"]:
        item = copy.deepcopy(raw_item)
        raw_parts = item.get("parts")
        if isinstance(raw_parts, list):
            # Historical media references are instance-relative and are not
            # needed for a wording-only experiment. Keep any accompanying
            # textual placeholder such as "[message type] sticker".
            item["parts"] = [
                part
                for part in raw_parts
                if not isinstance(part, dict)
                or str(part.get("type") or "").strip().lower() not in {"image", "image_url", "input_image"}
            ]
            if not item["parts"]:
                continue
        text = _item_text(item).strip()
        if text.startswith(EXPRESSION_PREFIXES) or text.startswith("<reply_intent>"):
            continue
        if text.startswith(STYLE_DIRECTIVE_PREFIXES):
            continue
        if "你想要回复的消息是" in text:
            candidate.append(_replace_item_text(item, _minimal_target_message(text)))
            continue
        if item.get("item_type") == "SystemMessageItem":
            system_text = _item_text(item).replace(REFERENCE_SYSTEM_LINE, "").strip()
            for line in (TURN_OWNERSHIP_LINE, LIFE_RELEVANCE_LINE, FACTUAL_UNCERTAINTY_LINE):
                if line not in system_text:
                    system_text = f"{system_text}\n{line}"
            candidate.append(_replace_item_text(item, system_text))
            continue
        candidate.append(item)
    return candidate


def _saved_output(snapshot: dict[str, Any]) -> str:
    outputs = snapshot.get("output_items") or []
    return _item_text(outputs[0]) if outputs and isinstance(outputs[0], dict) else ""


def _diagnostics(text: str) -> dict[str, Any]:
    return {
        "characters": len(text.strip()),
        "question_marks": text.count("？") + text.count("?"),
        "lines": len([line for line in text.splitlines() if line.strip()]),
    }


async def _generate(items: list[dict[str, Any]], temperature: float) -> tuple[str, dict[str, Any]]:
    from src.common.data_models.llm_service_data_models import LLMGenerationOptions
    from src.llm_models.request_snapshot import deserialize_persisted_context_items_snapshot
    from src.services.llm_service import LLMServiceClient

    context_items = deserialize_persisted_context_items_snapshot(items)
    client = LLMServiceClient(task_name="replyer", request_type="persona_harness.offline_eval")
    result = await client.generate_response_with_context(
        lambda _client, _model_info=None: context_items,
        LLMGenerationOptions(temperature=temperature, max_tokens=256),
    )
    usage = {
        "model": result.model_name,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
    }
    return result.response, usage


async def _run(args: argparse.Namespace) -> int:
    from src.config.config import config_manager

    config_manager.initialize()
    report: list[dict[str, Any]] = []
    for snapshot_arg in args.snapshots:
        path = Path(snapshot_arg).expanduser().resolve()
        snapshot = _read_snapshot(path)
        candidate_items = build_minimal_candidate_items(snapshot)
        entry: dict[str, Any] = {
            "snapshot": str(path),
            "baseline": _saved_output(snapshot),
            "baseline_diagnostics": _diagnostics(_saved_output(snapshot)),
            "request_item_count": len(snapshot["request_items"]),
            "candidate_item_count": len(candidate_items),
        }
        if args.dry_run:
            entry["candidate_prompt"] = [_item_text(item) for item in candidate_items]
        else:
            outputs = []
            for _ in range(args.samples):
                content, usage = await _generate(candidate_items, args.temperature)
                outputs.append({"content": content, "diagnostics": _diagnostics(content), "usage": usage})
            entry["candidates"] = outputs
        report.append(entry)

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).expanduser().resolve().write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline A/B evaluation for Replyer prompt harness changes.")
    parser.add_argument("snapshots", nargs="+", help="Persisted Replyer request snapshot JSON files")
    parser.add_argument(
        "--instance-dir",
        help="MaiBot instance directory containing config/ and data/ (must be parsed before imports)",
    )
    parser.add_argument("--samples", type=int, default=2, choices=range(1, 6))
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--dry-run", action="store_true", help="Only show transformed prompts; do not call an LLM")
    parser.add_argument("--output", help="Write the JSON report to this file")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
