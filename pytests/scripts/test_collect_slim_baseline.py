from datetime import datetime
from pathlib import Path
import json
import sqlite3
import subprocess
import sys

from scripts.collect_slim_baseline import database_metrics, plugins, safe_config, summarize


def test_runner_refuses_to_overwrite_existing_evidence(tmp_path: Path):
    marker = tmp_path / "earlier.json"
    marker.write_text("earlier baseline")
    runner = Path(__file__).resolve().parents[2] / "scripts/run_slim_baseline.py"
    result = subprocess.run(
        [sys.executable, str(runner), "--output-dir", str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "preserve earlier baseline" in result.stderr
    assert marker.read_text() == "earlier baseline"


def test_missing_values_are_not_zero_and_percentiles_are_nearest_rank():
    assert summarize([])["mean"] is None
    assert summarize([float("nan"), -1, 1, 3, 9]) == {"n": 3, "mean": 13 / 3, "p50": 3, "p95": 9, "max": 9}


def test_config_projection_does_not_export_secrets_ids_or_persona(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "bot_config.toml").write_text(
        '[personality]\npersonality="PRIVATE_SENTINEL"\n[chat.reply_timing]\ntalk_value=0.05\n'
    )
    (config / "model_config.toml").write_text(
        '[[api_providers]]\napi_key="SECRET_SENTINEL"\nbase_url="ENDPOINT_SENTINEL"\n'
        '[model_task_config.embedding]\nmodel_list=["synthetic-embedding"]\n'
    )
    (config / "adapter_policy.toml").write_text(
        '[defaults.private]\ndefault_action="block"\nallow_ids=["ACCOUNT_SENTINEL"]\n'
    )
    data = safe_config(tmp_path)
    assert data["flags"]["chat.reply_timing.talk_value"] == 0.05
    assert data["flags"]["a_memorix.plugin.enabled"] is None
    assert data["scope_counts"]["private"]["allow_count"] == 1
    assert data["task_routes"]["embedding"] == ["synthetic-embedding"]
    for secret in ["PRIVATE_SENTINEL", "SECRET_SENTINEL", "ACCOUNT_SENTINEL", "ENDPOINT_SENTINEL"]:
        assert secret not in json.dumps(data)


def test_plugin_projection_only_exports_boolean_switches(tmp_path: Path):
    plugin = tmp_path / "plugins" / "synthetic"
    plugin.mkdir(parents=True)
    (plugin / "_manifest.json").write_text('{"id":"synthetic","version":"1"}')
    (plugin / "config.toml").write_text(
        '[plugin]\nenabled=true\napi_key="SECRET_SENTINEL"\n[users]\nallow_ids=["ACCOUNT_SENTINEL"]\n'
    )
    data = plugins(tmp_path)
    assert data[0]["flags"] == {"plugin.enabled": True}
    assert "SENTINEL" not in json.dumps(data)


def test_sql_window_and_projection_preserve_population_and_private_boundary(tmp_path: Path):
    path = tmp_path / "test.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE llm_usage (request_type TEXT,model_name TEXT,time_cost REAL,prompt_tokens INT,
          completion_tokens INT,total_tokens INT,cost REAL,timestamp TEXT,session_id TEXT);
        CREATE TABLE mai_messages (processed_plain_text TEXT);
        INSERT INTO mai_messages VALUES ('PRIVATE_SENTINEL');
        INSERT INTO llm_usage VALUES ('maisaka.replyer','flash',2,10,3,13,0.001,'2026-10-22 10:00:00','ACCOUNT_SENTINEL');
        INSERT INTO llm_usage VALUES ('test.offline_eval','flash',3,10,4,14,0.002,'2026-10-22 10:01:00','');
        INSERT INTO llm_usage VALUES ('maisaka.replyer','flash',99,10,3,13,0.001,'2026-10-22 11:00:00','');
    """)
    db.commit()
    db.close()
    data = database_metrics(path, datetime(2026, 10, 22, 10), datetime(2026, 10, 22, 11))
    assert data["llm_quality"]["rows"] == 2
    assert data["llm_quality"]["token_sum_mismatch"] == 0
    assert {r["population"]: r["calls"] for r in data["llm_usage"]} == {"natural": 1, "offline_or_probe": 1}
    assert "SENTINEL" not in json.dumps(data)
