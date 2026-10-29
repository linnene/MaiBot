"""Read-only Linux companion baseline. Stdlib only; never imports MaiBot or calls a model.

Run through SSH stdin to avoid installing instrumentation on production. Output is
aggregate JSON: no chat text, account IDs, credentials, endpoints or prompt bodies.
CPU percent is delta process CPU / wall time (100 = one logical core). RSS sums
process resident pages and may double-count shared pages; cgroup memory includes cache.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import sqlite3
import subprocess
import time
import tomllib


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=30).rstrip("\n")


def summarize(values: list[float]) -> dict[str, Any]:
    """Nearest-rank percentiles; missing samples are not zero-filled."""
    clean = sorted(v for v in values if math.isfinite(v) and v >= 0)
    if not clean:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "max": None}
    return {
        "n": len(clean),
        "mean": sum(clean) / len(clean),
        "p50": clean[math.ceil(len(clean) * 0.5) - 1],
        "p95": clean[math.ceil(len(clean) * 0.95) - 1],
        "max": clean[-1],
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_config(instance: Path) -> dict[str, Any]:
    """Strict scalar allowlist, not a blacklist over arbitrary config values."""
    bot = tomllib.loads((instance / "config/bot_config.toml").read_text())
    paths = [
        "chat.reply_timing.talk_value",
        "chat.reply_timing.private_talk_value",
        "chat.reply_timing.mentioned_bot_reply",
        "chat.reply_timing.inevitable_at_reply",
        "chat.reply_timing.reply_trigger_mode",
        "chat.max_context_size",
        "chat.max_private_context_size",
        "chat.mid_term_memory",
        "chat.reply_style.enable_reply_quote",
        "experimental.persona_harness_v2",
        "experimental.replyer_retro_prompt",
        "experimental.planner_unsent_reply_retry",
        "expression.use_vector_expression",
        "a_memorix.plugin.enabled",
        "a_memorix.embedding.model_name",
        "a_memorix.embedding.dimension",
        "a_memorix.embedding.fallback.enabled",
        "a_memorix.embedding.fallback.allow_metadata_only_write",
        "expression.expression_self_reflect",
        "voice.enable_asr",
        "mcp.enable",
        "expression.enable_expression",
        "jargon.enable_jargon",
        "a_memorix.enabled",
        "a_memorix.integration.chat_summary_writeback_enabled",
        "a_memorix.integration.heuristic_memory_cross_chat_enabled",
        "a_memorix.integration.heuristic_memory_private_to_group_enabled",
        "a_memorix.integration.heuristic_memory_group_to_private_enabled",
        "emoji.enable_emoji",
        "emoji.use_new_send_logic",
        "emoji.behavior.private_tendency",
        "emoji.behavior.group_tendency",
        "debug.enable_reply_effect_tracking",
        "telemetry.enable",
        "telemetry.enabled",
        "tts.enable_tts",
        "voice.enable_voice",
    ]
    projection: dict[str, Any] = {}
    for key in paths:
        value: Any = bot
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        projection[key] = value if isinstance(value, (str, int, float, bool, type(None))) else None
    # Hashes pin config identity without exporting private config values.
    hashes = {
        name: digest(instance / "config" / name)
        for name in ("bot_config.toml", "model_config.toml", "adapter_policy.toml")
        if (instance / "config" / name).is_file()
    }
    policy = tomllib.loads((instance / "config/adapter_policy.toml").read_text())
    scope = {
        kind: {"default_action": values.get("default_action"), "allow_count": len(values.get("allow_ids", []))}
        for kind, values in policy.get("defaults", {}).items()
    }
    model = tomllib.loads((instance / "config/model_config.toml").read_text())
    return {
        "flags": projection,
        "file_sha256": hashes,
        "scope_counts": scope,
        "task_routes": {
            name: value.get("model_list", []) for name, value in model.get("model_task_config", {}).items()
        },
        "configured_model_identifiers": [m.get("model_identifier") for m in model.get("models", [])],
    }


def plugins(instance: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted((instance / "plugins").glob("*/_manifest.json")):
        manifest = json.loads(path.read_text())
        config_path = path.parent / "config.toml"
        config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        flags = {}
        for section, values in config.items():
            if not isinstance(values, dict):
                continue
            # Only booleans controlling modules; never serialize IDs, scenes, lists or free text.
            for key, value in values.items():
                if isinstance(value, bool) and (
                    key == "enabled" or key.startswith("enable_") or key.endswith("_enabled")
                ):
                    flags[f"{section}.{key}"] = value
        result.append(
            {
                "id": manifest.get("id"),
                "version": manifest.get("version"),
                "manifest_sha256": digest(path),
                "config_sha256": digest(config_path) if config_path.exists() else None,
                "python_tree_sha256": hashlib.sha256(
                    "\n".join(
                        str(p.relative_to(path.parent)) + ":" + digest(p) for p in sorted(path.parent.rglob("*.py"))
                    ).encode()
                ).hexdigest(),
                "flags": flags,
            }
        )
    return result


def database_metrics(path: Path, since: datetime, until: datetime) -> dict[str, Any]:
    """SQL projections never select message text, tool arguments, profile facts or IDs."""
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    start, stop = since.isoformat(sep=" "), until.isoformat(sep=" ")
    result: dict[str, Any] = {"since": since.astimezone().isoformat(), "until": until.astimezone().isoformat()}
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result["table_count"] = len(tables)
    result["availability"] = {
        name: name in tables for name in ("llm_usage", "model_task_usage", "maisaka_monitor_events")
    }
    result["rows"] = {
        name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        for name in (
            "mai_messages",
            "llm_usage",
            "model_task_usage",
            "tool_records",
            "maisaka_monitor_events",
            "maisaka_reply_effects",
            "images",
            "expressions",
            "jargons",
            "person_info",
        )
        if name in tables
    }
    if "llm_usage" in tables:
        records = db.execute(
            "SELECT request_type,model_name,time_cost,prompt_tokens,completion_tokens,total_tokens,cost "
            "FROM llm_usage WHERE timestamp >= ? AND timestamp < ?",
            (start, stop),
        ).fetchall()
        groups: dict[tuple[str, str, str], list[tuple]] = defaultdict(list)
        for row in records:
            source = "offline_or_probe" if any(word in row[0] for word in ("offline", "probe", "eval")) else "natural"
            groups[(source, row[0], row[1])].append(row)
        result["llm_usage"] = [
            {
                "population": source,
                "request_type": kind,
                "model": model,
                "calls": len(rows),
                "duration_seconds": summarize([r[2] for r in rows if isinstance(r[2], (int, float))]),
                "prompt_tokens": sum(r[3] or 0 for r in rows),
                "completion_tokens": sum(r[4] or 0 for r in rows),
                "total_tokens": sum(r[5] or 0 for r in rows),
                "recorded_cost_cny": sum(r[6] or 0 for r in rows),
                "invalid_duration_count": sum(not isinstance(r[2], (int, float)) or r[2] < 0 for r in rows),
            }
            for (source, kind, model), rows in sorted(groups.items())
        ]
        result["llm_quality"] = {
            "rows": len(records),
            "token_sum_mismatch": sum(r[3] is not None and r[4] is not None and r[5] != r[3] + r[4] for r in records),
            "missing_duration": sum(r[2] is None for r in records),
        }
        first, last = db.execute(
            "SELECT min(timestamp),max(timestamp) FROM llm_usage WHERE timestamp >= ? AND timestamp < ?", (start, stop)
        ).fetchone()
        result["llm_quality"].update(first_record=first, last_record=last)
    if "model_task_usage" in tables:
        result["task_attempts"] = [
            dict(zip(("task", "attempts", "failures"), row, strict=True))
            for row in db.execute(
                "SELECT task_name,sum(request_count),sum(failure_count) FROM model_task_usage "
                "WHERE bucket_time >= ? AND bucket_time < ? GROUP BY task_name",
                (start, stop),
            )
        ]
    if "tool_records" in tables:
        result["tools"] = dict(
            db.execute(
                "SELECT tool_name,count(*) FROM tool_records WHERE timestamp >= ? AND timestamp < ? GROUP BY tool_name",
                (start, stop),
            )
        )
    if "maisaka_monitor_events" in tables:
        result["event_types"] = dict(
            db.execute(
                "SELECT event_type,count(*) FROM maisaka_monitor_events WHERE timestamp >= ? AND timestamp < ? GROUP BY event_type",
                (since.timestamp(), until.timestamp()),
            )
        )
        result["planner_end_reasons"] = dict(
            db.execute(
                "SELECT coalesce(json_extract(payload_json,'$.final_state.end_reason'),'unknown'),count(*) "
                "FROM maisaka_monitor_events WHERE event_type='planner.finalized' AND json_valid(payload_json) "
                "AND timestamp >= ? AND timestamp < ? GROUP BY 1",
                (since.timestamp(), until.timestamp()),
            )
        )
        # Only numeric, explicitly named JSON fields cross the privacy boundary.
        result["planner_observed"] = {}
        for name, field in {
            "duration_ms": "planner.duration_ms",
            "prompt_tokens": "planner.prompt_tokens",
            "selected_history_count": "request.selected_history_count",
        }.items():
            values = [
                r[0]
                for r in db.execute(
                    "SELECT json_extract(payload_json,?) FROM maisaka_monitor_events "
                    "WHERE event_type='planner.finalized' AND json_valid(payload_json) AND timestamp >= ? AND timestamp < ?",
                    ("$." + field, since.timestamp(), until.timestamp()),
                )
            ]
            result["planner_observed"][name] = summarize([v for v in values if isinstance(v, (int, float))])
    if "maisaka_reply_effects" in tables:
        result["reply_effects"] = [
            dict(zip(("chat_type", "status", "count"), row, strict=True))
            for row in db.execute(
                "SELECT chat_type,status,count(*) FROM maisaka_reply_effects WHERE created_at >= ? AND created_at < ? "
                "GROUP BY chat_type,status",
                (start, stop),
            )
        ]
    db.close()
    return result


def memory_storage(instance: Path) -> dict[str, Any]:
    root = instance / "data/a-memorix"
    path = root / "metadata/metadata.db"
    if not path.exists():
        return {"available": False}
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = {
        name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        for name in (
            "paragraphs",
            "relations",
            "entities",
            "episodes",
            "fact_claims",
            "fact_evidence",
            "image_embeddings",
            "image_embedding_jobs",
        )
        if name in tables
    }
    db.close()
    return {
        "available": True,
        "rows": counts,
        "faiss_index_npy_file_count": sum(
            1 for p in root.rglob("*") if p.is_file() and p.suffix in {".faiss", ".index", ".npy"}
        ),
    }


def process_sample(control_group: str) -> dict[str, Any]:
    group = Path("/sys/fs/cgroup") / control_group.lstrip("/")
    pids: set[int] = set()
    for file in group.rglob("cgroup.procs"):
        pids.update(int(line) for line in file.read_text().splitlines())
    rows = []
    for pid in sorted(pids):
        try:
            fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
            rows.append(
                {
                    "pid": pid,
                    "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                    "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
                    "threads": int(fields[17]),
                }
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
    memory = group / "memory.current"
    cpu = group / "cpu.stat"
    cpu_fields = dict(line.split() for line in cpu.read_text().splitlines()) if cpu.exists() else {}
    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "monotonic": time.monotonic(),
        "processes": rows,
        "rss_bytes": sum(row["rss_bytes"] for row in rows),
        "cgroup_memory_bytes": int(memory.read_text()) if memory.exists() else None,
        "cgroup_cpu_seconds": int(cpu_fields["usage_usec"]) / 1e6 if "usage_usec" in cpu_fields else None,
    }


def resource_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    cpu = []
    for before, after in zip(samples, samples[1:], strict=False):
        elapsed = after["monotonic"] - before["monotonic"]
        if before["cgroup_cpu_seconds"] is not None and after["cgroup_cpu_seconds"] is not None and elapsed > 0:
            delta = after["cgroup_cpu_seconds"] - before["cgroup_cpu_seconds"]
            if delta >= 0:
                cpu.append(100 * delta / elapsed)
    return {
        "cpu_percent_one_core": summarize(cpu),
        "rss_bytes": summarize([s["rss_bytes"] for s in samples]),
        "cgroup_memory_bytes": summarize(
            [s["cgroup_memory_bytes"] for s in samples if s["cgroup_memory_bytes"] is not None]
        ),
    }


def collect(repo: Path, instance: Path, duration: float, interval: float, hours: float) -> dict[str, Any]:
    service = dict(
        line.split("=", 1)
        for line in command(
            "systemctl",
            "show",
            "maibot",
            "-p",
            "ControlGroup",
            "-p",
            "MainPID",
            "-p",
            "ActiveState",
            "-p",
            "NRestarts",
            "-p",
            "ExecMainStartTimestamp",
            "-p",
            "ActiveEnterTimestampMonotonic",
        ).splitlines()
    )
    if service["ActiveState"] != "active" or not service["ControlGroup"].startswith("/system.slice/"):
        raise RuntimeError("MaiBot service is not active in the expected service cgroup")
    begun = datetime.now().astimezone()
    config = safe_config(instance)
    manifests = plugins(instance)
    sizes = {}
    for name, path in {
        "git": repo / ".git",
        "venv": repo / ".venv",
        "src": repo / "src",
        "prompts": repo / "prompts",
        "frontend": repo / "dashboard",
        "logs": instance / "logs",
        "data": instance / "data",
        "plugins": instance / "plugins",
    }.items():
        sizes[name] = int(command("du", "-s", "--block-size=1", str(path)).split()[0]) if path.exists() else None
    changed = command("git", "-C", str(repo), "status", "--porcelain").splitlines()
    source_hashes = {}
    for line in changed:
        relative = line[3:]
        path = repo / relative
        if path.is_file() and path.suffix in {".py", ".prompt"}:
            source_hashes[relative] = digest(path)
    packages = []
    for site in (repo / ".venv/lib").glob("python*/site-packages"):
        packages.extend(
            {"name": d.metadata["Name"], "version": d.version}
            for d in importlib.metadata.distributions(path=[str(site)])
        )
    samples = []
    start = time.monotonic()
    while True:
        samples.append(process_sample(service["ControlGroup"]))
        remaining = duration - (time.monotonic() - start)
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    ended = datetime.now().astimezone()
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    active_age = uptime - int(service["ActiveEnterTimestampMonotonic"]) / 1e6
    boot = ended - timedelta(seconds=max(0, active_age))
    db = instance / "data/MaiBot.db"
    windows = {
        "trailing_history": database_metrics(
            db, (begun - timedelta(hours=hours)).replace(tzinfo=None), begun.replace(tzinfo=None)
        ),
        "current_service_boot": database_metrics(db, boot.replace(tzinfo=None), begun.replace(tzinfo=None)),
        "resource_sample_window": database_metrics(db, begun.replace(tzinfo=None), ended.replace(tzinfo=None)),
    }
    return {
        "schema_version": 1,
        "started_at": begun.isoformat(),
        "ended_at": ended.isoformat(),
        "lookback_hours": hours,
        "duration_seconds": samples[-1]["monotonic"] - samples[0]["monotonic"],
        "interval_seconds": interval,
        "service": service,
        "repo_head": command("git", "-C", str(repo), "rev-parse", "HEAD"),
        "python_runtime_version": command(
            str(repo / ".venv/bin/python"), "-c", "import platform;print(platform.python_version())"
        ),
        "locks_sha256": {p.name: digest(p) for p in [repo / "uv.lock", repo / "pyproject.toml"] if p.exists()},
        "repo_dirty_paths": changed,
        "dirty_source_sha256": source_hashes,
        "config": config,
        "plugins": manifests,
        "config_unchanged_during_sample": config["file_sha256"] == safe_config(instance)["file_sha256"],
        "package_versions": sorted(packages, key=lambda x: x["name"].lower()),
        "disk_bytes_allocated": sizes,
        "resources": {"summary": resource_summary(samples), "samples": samples},
        "windows": windows,
        "memory_storage": memory_storage(instance),
        "limits": [
            "natural traffic only; not a controlled load test",
            "no end-to-end latency claim from LLM durations",
            "current-boot window may contain configuration changes",
            "llm_usage is not a count of failed API attempts",
            "cost is recorded historical accounting, not verified current provider billing",
            "readonly live DB, not immutable full backup",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--hours", type=float, default=24)
    args = parser.parse_args()
    if args.duration < 0 or args.interval <= 0 or args.hours <= 0:
        parser.error("duration must be nonnegative; interval and hours must be positive")
    print(
        json.dumps(
            collect(args.repo, args.instance, args.duration, args.interval, args.hours), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
