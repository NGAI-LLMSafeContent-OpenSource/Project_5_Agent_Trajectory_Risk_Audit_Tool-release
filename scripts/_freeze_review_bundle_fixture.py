# SPDX-License-Identifier: Apache-2.0
"""Generate fixtures/workbench/review_bundle_*.json from raw samples + fake provider."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
sys.path.insert(0, str(ROOT))

from trajectory_audit.review_bundle import project_review_bundle_from_run_dir  # noqa: E402
from trajectory_audit.runner import execute_run  # noqa: E402

FIXTURES = {
    "atbench": {
        "input": ROOT / "fixtures/workbench/atbench_single_unsafe.json",
        "out": ROOT / "fixtures/workbench/review_bundle_atbench.json",
        "adapter_id": "atbench",
        "run_id": "run-fixture-atbench-86",
    },
    "hintbench": {
        "input": ROOT / "fixtures/workbench/hintbench_single_risky.json",
        "out": ROOT / "fixtures/workbench/review_bundle_hintbench.json",
        "adapter_id": "hintbench",
        "run_id": "run-fixture-hintbench-slack_task_0008_risk_v7",
    },
}


def freeze_one(name: str) -> Path:
    spec = FIXTURES[name]
    cfg = ROOT / "fixtures/topic4_provider/fake_unsafe_located_config.json"
    with tempfile.TemporaryDirectory() as tmp:
        summary = execute_run(
            input_path=spec["input"],
            out_root=Path(tmp) / "runs",
            provider_id="topic4-cli",
            adapter_id=spec["adapter_id"],
            provider_options={
                "config": str(cfg),
                "command": f"{sys.executable} -m tools.fake_topic4_provider",
                "profile": "strict-v1",
            },
        )
        bundle = project_review_bundle_from_run_dir(Path(summary["run_dir"]))
    # Stabilize volatile ids for a frozen UI fixture.
    bundle["provenance"]["run_id"] = spec["run_id"]
    bundle["provenance"]["created_at"] = "2026-11-05T00:00:00Z"
    bundle["provenance"]["completed_at"] = "2026-11-05T00:00:01Z"
    out: Path = spec["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "adapter",
        nargs="*",
        choices=[*FIXTURES.keys(), "all"],
        default=["all"],
        help="Which frozen Review Bundle fixture(s) to regenerate (default: all)",
    )
    args = parser.parse_args()
    selected = list(FIXTURES.keys()) if "all" in args.adapter or not args.adapter else args.adapter
    for name in selected:
        path = freeze_one(name)
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
