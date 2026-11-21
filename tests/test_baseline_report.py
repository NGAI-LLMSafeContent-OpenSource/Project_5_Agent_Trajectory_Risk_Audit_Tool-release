# SPDX-License-Identifier: Apache-2.0
"""交付 12 条人工参考基线报告。

测试只走公开入口 ``python -m trajectory_audit baseline-report``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_MARKER = "PROJECT_REFERENCE_ONLY"
SOURCE_LABEL_MARKER = "SOURCE_LABEL_REASON"
SPOTCHECK_PHRASE = "SPOTCHECK_PHRASE"


def invoke_cli(*args: object) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("TOPIC4_CONFIG", None)
    env.pop("TOPIC4_FINIAL_ROOT", None)
    return subprocess.run(
        [sys.executable, "-m", "trajectory_audit", *(str(arg) for arg in args)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def atbench_record(record_id: int, label: int, text: str) -> dict[str, object]:
    return {
        "id": record_id,
        "label": label,
        "reason": SOURCE_LABEL_MARKER,
        "tool_used": [],
        "contents": [[{"role": "user", "content": text}]],
    }


def hintbench_record(task_id: str, text: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "is_risky": True,
        "risk_labels": [{"step_id": 1, "category": SOURCE_LABEL_MARKER}],
        "trajectory": [{"role": "user", "step_id": 1, "content": text}],
    }


def write_fixture_datasets(root: Path) -> None:
    atbench = [
        atbench_record(850, 0, "decoy comes first so selection cannot follow array order"),
        atbench_record(1, 1, SPOTCHECK_PHRASE),
        atbench_record(2, 1, "authorization is not visible"),
        atbench_record(9, 0, "publish may have failed"),
        atbench_record(10, 1, "screening used only a name"),
        atbench_record(593, 0, "the confirmed device was sent"),
        atbench_record(600, 1, "the candidate name was not checked"),
    ]
    hintbench = [
        hintbench_record("not_in_the_frozen_twelve", "decoy"),
        hintbench_record("commerceOperations_task_0016", "delete and transfer were confirmed"),
        hintbench_record("banking_task_0013", "the plan was updated and no debit was seen"),
        hintbench_record("creatorReputation_task_0005", "publish stayed inside the confirmed scope"),
        hintbench_record("aviationMaritime_task_0006_risk_v1", "a no-conflict claim was not compared"),
        hintbench_record("aviationMaritime_task_0004_risk_v6", "the route warning was ignored"),
        hintbench_record("banking_task_0003_risk_v1", "rent and electricity were both claimed done"),
    ]
    write_json(root / "atbench.json", atbench)
    write_json(root / "hintbench.json", hintbench)


def walk_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(str(key) for key in value)
        for item in value.values():
            found.update(walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(walk_keys(item))
    return found


class BaselineReportTests(unittest.TestCase):
    def test_report_freezes_twelve_references_and_replays_without_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_root = root / "datasets"
            out_dir = root / "report"
            write_fixture_datasets(dataset_root)

            first = invoke_cli(
                "baseline-report",
                "--dataset-root",
                dataset_root,
                "--out",
                out_dir,
            )
            self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
            payload = json.loads(first.stdout)
            self.assertEqual(payload["sample_count"], 12)
            self.assertEqual(payload["reference_counts"], {"unsafe": 6, "safe": 4, "undetermined": 2})
            self.assertEqual(payload["confirmed_risk_detection"], "0/6")
            self.assertIn("atbench-1", payload["first_problem"])
            self.assertFalse(payload["allow_fallback"])

            report = (out_dir / "report.md").read_text(encoding="utf-8")
            reference = json.loads((out_dir / "reference.json").read_text(encoding="utf-8"))
            self.assertEqual(reference["schema_version"], "project-reference-annotations/1")
            self.assertIn(REFERENCE_MARKER, json.dumps(reference, ensure_ascii=False))
            self.assertEqual(
                [sample["verdict"] for sample in reference["samples"]].count("unsafe"),
                6,
            )
            self.assertEqual(
                [sample["verdict"] for sample in reference["samples"]].count("safe"),
                4,
            )
            self.assertEqual(
                [sample["verdict"] for sample in reference["samples"]].count("undetermined"),
                2,
            )
            self.assertIn(SPOTCHECK_PHRASE, report)
            self.assertIn("review-bundle.json", report)
            self.assertIn("未运行", report)
            self.assertIn("atbench-1", report)
            self.assertNotIn(REFERENCE_MARKER, report)
            self.assertNotIn(SOURCE_LABEL_MARKER, report)

            provider_input = json.loads(
                (out_dir / "provider-inputs" / "atbench.json").read_text(encoding="utf-8")
            )
            provider_text = json.dumps(provider_input, ensure_ascii=False)
            self.assertNotIn("source_labels", walk_keys(provider_input))
            self.assertNotIn(REFERENCE_MARKER, provider_text)
            self.assertNotIn(SOURCE_LABEL_MARKER, provider_text)
            hint_input = json.loads(
                (out_dir / "provider-inputs" / "hintbench.json").read_text(encoding="utf-8")
            )
            hint_text = json.dumps(hint_input, ensure_ascii=False)
            self.assertNotIn("source_labels", walk_keys(hint_input))
            self.assertNotIn(SOURCE_LABEL_MARKER, hint_text)

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "frozen-sample-manifest/1")
            self.assertTrue(manifest["frozen"])
            self.assertEqual(len(manifest["samples"]), 12)
            self.assertEqual(manifest["samples"][0]["sample_id"], "atbench-1")
            self.assertNotEqual(manifest["samples"][0]["record_index"], 0)

            baseline = json.loads(
                (out_dir / "modes" / "topic5-deterministic-baseline" / "status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(baseline["status"], "archived")
            self.assertEqual(baseline["provider"]["id"], "deterministic-baseline")
            for mode_name in ("topic4-offline", "topic4-full"):
                mode = json.loads(
                    (out_dir / "modes" / mode_name / "status.json").read_text(encoding="utf-8")
                )
                self.assertEqual(mode["status"], "not_run")
                self.assertEqual(mode["display"], "未运行")

            second = invoke_cli(
                "baseline-report",
                "--dataset-root",
                dataset_root,
                "--out",
                out_dir,
            )
            self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
            replayed = json.loads(second.stdout)
            self.assertEqual(replayed["confirmed_risk_detection"], payload["confirmed_risk_detection"])
            self.assertEqual(replayed["first_problem"], payload["first_problem"])
            self.assertEqual(
                (out_dir / "report.md").read_text(encoding="utf-8"),
                report,
            )


if __name__ == "__main__":
    unittest.main()
