# SPDX-License-Identifier: Apache-2.0
"""80 条开发集的来源标签对照。

测试只走公开入口 ``python -m trajectory_audit source-label-report``。
来源标签不是人工裁决，报告也不读取 12 条项目参考标注。
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
SOURCE_NOTE = "SOURCE_LABEL_NOT_PROJECT_TRUTH"
HUMAN_MARKER = "baseline-12-human"
ENGLISH = "SPOTCHECK_ENGLISH"
CHINESE = "中文对照：算法把来源安全样本判成了不安全。"


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


def message(event_id: str, sequence: int, text: str, step: int | None = None) -> dict[str, object]:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "type": "message",
        "actor": "user",
        "source_step_id": step,
        "content": {"role": "user", "text": text, "parts": [{"type": "text", "text": text}]},
    }


def tool_call(event_id: str, sequence: int, name: str, step: int) -> dict[str, object]:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "type": "tool_call",
        "actor": "agent",
        "source_step_id": step,
        "content": {
            "tool": name,
            "arguments_status": "object",
            "arguments": {"q": name},
        },
    }


def tool_error(event_id: str, sequence: int, call_id: str, step: int) -> dict[str, object]:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "type": "tool_result",
        "actor": "tool",
        "source_step_id": step,
        "content": {
            "call_event_id": call_id,
            "link_status": "positional",
            "declared_status": "error",
            "output": "failed",
        },
    }


def item(sample_id: str, events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "audit_input_sha256": "0" * 64,
        "trajectory": {
            "schema_version": "audit-trajectory/1",
            "events": events,
            "tool_definitions": [],
            "coverage": {},
        },
        "policy": None,
    }


def unsafe_events() -> list[dict[str, object]]:
    return [
        message("evt-0001", 1, ENGLISH, 1),
        tool_call("evt-0002", 2, "lookup", 2),
        tool_error("evt-0003", 3, "evt-0002", 3),
        tool_call("evt-0004", 4, "send", 4),
    ]


def write_corpus(root: Path) -> None:
    write_json(
        root / "input-batch.json",
        {
            "schema_version": "topic4-audit-input/1",
            "batch_id": "audit-input-fixture",
            "items": [
                item("sample-a", [message("evt-0001", 1, "plain unsafe source")]),
                item("sample-b", [message("evt-0001", 1, "plain safe source")]),
                item("sample-c", unsafe_events()),
                item("sample-d", unsafe_events()),
            ],
        },
    )
    write_json(
        root / "self-check-reference.json",
        {
            "schema_version": "provider-corpus-reference/1",
            "corpus_version": "fixture",
            "notice": SOURCE_NOTE,
            "is_project_truth": False,
            "samples": [
                {
                    "sample_id": "sample-a",
                    "source_sample_id": "atbench-1",
                    "dataset_id": "atbench",
                    "source_label": {
                        "kind": "source_label",
                        "risk_layer": "unsafe",
                        "risk_source": "tool_description_injection",
                    },
                },
                {
                    "sample_id": "sample-b",
                    "source_sample_id": "atbench-2",
                    "dataset_id": "atbench",
                    "source_label": {
                        "kind": "source_label",
                        "risk_layer": "safe",
                        "risk_source": "benign",
                    },
                },
                {
                    "sample_id": "sample-c",
                    "source_sample_id": "hint-risky",
                    "dataset_id": "hintbench",
                    "source_label": {
                        "kind": "source_label",
                        "risk_layer": "risky",
                        "categories": ["Goal Constraint"],
                    },
                },
                {
                    "sample_id": "sample-d",
                    "source_sample_id": "hint-safe",
                    "dataset_id": "hintbench",
                    "source_label": {
                        "kind": "source_label",
                        "risk_layer": "safe",
                    },
                },
                {
                    "sample_id": "sample-e",
                    "source_sample_id": "atbench-9",
                    "dataset_id": "atbench",
                    "source_label": {
                        "kind": "source_label",
                        "risk_layer": "unsafe",
                        "risk_source": "direct_prompt_injection",
                    },
                },
            ],
        },
    )
    write_json(
        root / "not-the-source-report.json",
        {
            "schema_version": "project-reference-annotations/1",
            "annotation_version": HUMAN_MARKER,
            "samples": [{"sample_id": "sample-a", "verdict": "safe"}],
        },
    )


class SourceLabelReportTests(unittest.TestCase):
    def test_report_counts_source_labels_without_human_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "report"
            gloss = root / "gloss.json"
            write_corpus(corpus)
            write_json(gloss, {"sample-d": CHINESE})

            completed = invoke_cli(
                "source-label-report",
                "--corpus",
                corpus,
                "--gloss",
                gloss,
                "--out",
                out_dir,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["sample_count"], 5)
            self.assertEqual(
                payload["counts"],
                {"一致": 1, "不一致": 1, "未判定": 2, "运行失败": 1},
            )
            self.assertTrue(payload["isolated_from_human_reference"])
            self.assertFalse(payload["allow_fallback"])

            report = (out_dir / "report.md").read_text(encoding="utf-8")
            provider_input = (out_dir / "provider-input.json").read_text(encoding="utf-8")
            self.assertNotIn(SOURCE_NOTE, report)
            self.assertNotIn(SOURCE_NOTE, provider_input)
            self.assertNotIn(HUMAN_MARKER, report)
            self.assertNotIn(HUMAN_MARKER, provider_input)
            self.assertIn(ENGLISH, report)
            self.assertIn(CHINESE, report)
            self.assertLess(report.index("sample-d"), report.index("sample-c"))
            self.assertIn("步骤 3", report)

            document = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            by_source = {row["dataset_id"]: row for row in document["by_source"]}
            self.assertEqual(by_source["atbench"]["denominator"], 3)
            self.assertEqual(by_source["atbench"]["未判定"], 2)
            self.assertEqual(by_source["atbench"]["运行失败"], 1)
            self.assertEqual(by_source["hintbench"]["denominator"], 2)
            self.assertEqual(by_source["hintbench"]["一致"], 1)
            self.assertEqual(by_source["hintbench"]["不一致"], 1)
            categories = {
                (row["dataset_id"], row["category"]): row for row in document["by_category"]
            }
            self.assertEqual(categories[("atbench", "tool_description_injection")]["未判定"], 1)
            self.assertEqual(categories[("hintbench", "Goal Constraint")]["一致"], 1)
            self.assertEqual(categories[("hintbench", "未分类")]["不一致"], 1)
            for row in by_source.values():
                self.assertEqual(
                    row["denominator"],
                    row["一致"] + row["不一致"] + row["未判定"] + row["运行失败"],
                )
            review = document["priority_review"]
            self.assertEqual(
                [item["sample_id"] for item in review],
                ["sample-d", "sample-a", "sample-e", "sample-c"],
            )
            self.assertEqual(review[0]["outcome"], "不一致")
            self.assertIn("优化方向", review[0]["reason"])
            self.assertEqual(review[0]["chinese"], CHINESE)
            self.assertEqual(review[0]["english"], ENGLISH)
            self.assertIsNone(review[0]["human_adjudication"])
            self.assertEqual(review[3]["reason_kind"], "agreement_spot_check")

    def test_frozen_development_corpus_counts_every_source_category(self) -> None:
        corpus = PROJECT_ROOT / "corpus" / "topic4-provider-corpus-v2" / "development"
        reference = json.loads((corpus / "self-check-reference.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "report"
            completed = invoke_cli(
                "source-label-report",
                "--corpus",
                corpus,
                "--out",
                out_dir,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            document = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            report = (out_dir / "report.md").read_text(encoding="utf-8")
            provider_input = (out_dir / "provider-input.json").read_text(encoding="utf-8")

            self.assertEqual(document["sample_count"], 80)
            self.assertNotIn(reference["notice"], report)
            self.assertNotIn(reference["notice"], provider_input)
            self.assertNotIn(HUMAN_MARKER, report)
            self.assertNotIn(HUMAN_MARKER, provider_input)

            by_source = {row["dataset_id"]: row for row in document["by_source"]}
            source_counts: dict[str, int] = {}
            category_counts: dict[tuple[str, str], int] = {}
            for sample in reference["samples"]:
                source_counts[sample["dataset_id"]] = source_counts.get(sample["dataset_id"], 0) + 1
                for category in _reference_categories(sample):
                    key = (sample["dataset_id"], category)
                    category_counts[key] = category_counts.get(key, 0) + 1
            self.assertEqual(by_source["atbench"]["denominator"], source_counts["atbench"])
            self.assertEqual(by_source["hintbench"]["denominator"], source_counts["hintbench"])
            reported = {
                (row["dataset_id"], row["category"]): row for row in document["by_category"]
            }
            self.assertEqual(set(reported), set(category_counts))
            for key, denominator in category_counts.items():
                row = reported[key]
                self.assertEqual(row["denominator"], denominator)
                self.assertEqual(
                    row["denominator"],
                    row["一致"] + row["不一致"] + row["未判定"] + row["运行失败"],
                )
            for item in document["priority_review"]:
                self.assertIsNone(item["human_adjudication"])
                self.assertTrue(item["reason"])
                self.assertTrue(item["evidence"])
            self.assertIn("检测发现", report)


def _reference_categories(sample: dict[str, object]) -> list[str]:
    label = sample["source_label"]
    if not isinstance(label, dict):
        return ["未分类"]
    if sample["dataset_id"] == "atbench":
        risk_source = label.get("risk_source")
        if isinstance(risk_source, str) and risk_source:
            return [risk_source]
        return ["未分类"]
    raw = label.get("categories")
    names = [item for item in raw if isinstance(item, str) and item] if isinstance(raw, list) else []
    return names or ["未分类"]


if __name__ == "__main__":
    unittest.main()
