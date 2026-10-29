"""Run the fixed synthetic companion suite; preserve failures rather than silently skipping them."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    manifest_path = repo / "evals/slim-baseline/suite.json"
    manifest = json.loads(manifest_path.read_text())
    output = args.output_dir.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("output-dir must be empty; preserve earlier baseline evidence")
    output.mkdir(parents=True, exist_ok=True)
    xml = output / "structural.junit.xml"
    started = datetime.now().astimezone().isoformat()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *manifest["tests"], f"--junitxml={xml}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    (output / "structural.stdout.txt").write_text(result.stdout + result.stderr)
    report = {
        "suite_id": manifest["suite_id"],
        "started_at": started,
        "ended_at": datetime.now().astimezone().isoformat(),
        "python": platform.python_version(),
        "repo_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "suite_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "pytest_exit_code": result.returncode,
        "locks_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [repo / "uv.lock", repo / "pyproject.toml"]
            if p.exists()
        },
        "kind": manifest["kind"],
        "failure_cases": [],
        "unexpected_failures": [],
    }
    if xml.exists():
        suites = ET.parse(xml).getroot()
        counts = {
            key: sum(int(s.attrib.get(key, 0)) for s in suites.iter("testsuite"))
            for key in ["tests", "failures", "errors", "skipped"]
        }
        counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
        report["counts"] = counts
        for case in suites.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                name = case.attrib.get("classname", "") + "::" + case.attrib.get("name", "")
                report["failure_cases"].append(name)
                if not any(known in name for known in manifest["known_baseline_failures_to_verify_not_skip"]):
                    report["unexpected_failures"].append(name)
    report["collection_complete"] = "counts" in report and result.returncode in (0, 1)
    (output / "structural-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
