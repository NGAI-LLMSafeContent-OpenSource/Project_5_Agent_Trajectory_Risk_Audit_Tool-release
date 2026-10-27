# SPDX-License-Identifier: Apache-2.0
"""Acceptance tests for ATBench / HINTBench smoke fixtures.

These tests drive only the public ``python -m trajectory_audit`` entry points and
assert observable artifacts. They never import adapter internals.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "fixtures" / "smoke"
ATBENCH_FIXTURE = FIXTURES / "atbench_smoke.json"
HINTBENCH_FIXTURE = FIXTURES / "hintbench_smoke.json"
PROVIDER_ID = "deterministic-baseline"
RUN_ARTIFACTS = (
    "run.json",
    "input_manifest.json",
    "trajectory.json",
    "audit_result.json",
    "metrics.json",
    "index.html",
)
EVENT_TYPES = {
    "message",
    "thought",
    "tool_call",
    "tool_result",
    "state_change",
    "unsupported_content",
}
DOWNSTREAM_MODULES = (
    "trajectory_audit/baseline.py",
    "trajectory_audit/runner.py",
    "trajectory_audit/batch.py",
    "trajectory_audit/metrics.py",
    "trajectory_audit/page.py",
    "trajectory_audit/providers.py",
    "trajectory_audit/model.py",
    "trajectory_audit/schema.py",
)


def invoke_cli(*args: object) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "trajectory_audit", *(str(arg) for arg in args)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_fixture(path: Path) -> dict:
    return read_json(path)


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_batch(fixture: Path, adapter: str, out_root: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = invoke_cli(
        "batch",
        "--input",
        fixture,
        "--out",
        out_root,
        "--provider",
        PROVIDER_ID,
        "--adapter",
        adapter,
    )
    payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
    return completed, payload


def collect_sample_artifacts(batch: dict) -> list[tuple[dict, Path, dict]]:
    batch_dir = Path(batch["batch_dir"])
    rows = []
    for sample in batch["samples"]:
        run_dir = batch_dir / sample["run_dir"]
        trajectory = read_json(run_dir / "trajectory.json")
        rows.append((sample, run_dir, trajectory))
    return rows


class SourceBatchSmokeTest(unittest.TestCase):
    def test_atbench_smoke_batch_produces_the_same_run_bundle_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed, batch = run_batch(ATBENCH_FIXTURE, "atbench", Path(tmp) / "runs")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(batch["status"], "complete")
            self.assertEqual(batch["records"]["total"], 3)
            self.assertEqual(batch["records"]["samples_completed"], 3)
            self.assertEqual(batch["failures"], [])

            batch_dir = Path(batch["batch_dir"])
            for name in ("batch.json", "input_manifest.json", "failures.json", "index.html"):
                self.assertTrue((batch_dir / name).is_file(), f"missing {name}")
            self.assertTrue((batch_dir / batch["artifacts"]["input"]).is_file())
            stored = read_json(batch_dir / "batch.json")
            self.assertEqual(stored["batch_id"], batch["batch_id"])
            self.assertEqual(stored["status"], "complete")
            self.assertEqual(stored["adapter"]["id"], "atbench")

            sample_ids = set()
            for sample, run_dir, trajectory in collect_sample_artifacts(batch):
                sample_ids.add(sample["sample_id"])
                self.assertEqual(sample["status"], "complete")
                for name in RUN_ARTIFACTS:
                    self.assertTrue((run_dir / name).is_file(), f"missing {name}")
                run = read_json(run_dir / "run.json")
                self.assertEqual(run["run_id"], sample["run_id"])
                self.assertEqual(run["sample"]["sample_id"], sample["sample_id"])
                self.assertEqual(run["tool"]["entry_point"], "python -m trajectory_audit batch")
                self.assertEqual(run["sample"]["source_format"], "atbench")
                self.assertEqual(run["adapter"]["id"], "atbench")
                self.assertEqual(run["sample"]["dataset"]["id"], "atbench")
                self.assertIn("ATBench@", run["sample"]["dataset"]["version"])
                self.assertEqual(trajectory["sample"]["source_format"], "atbench")
                self.assertEqual(trajectory["source"]["dataset_id"], "atbench")
                self.assertEqual(sample["verdict"], read_json(run_dir / "audit_result.json")["verdict"]["value"])
                metrics = read_json(run_dir / "metrics.json")
                if metrics["tools"]["results"]:
                    self.assertEqual(metrics["tools"]["linked_results"], 0)
                    self.assertEqual(
                        metrics["tools"]["positionally_linked_results"],
                        metrics["tools"]["results"],
                    )
                page = (run_dir / "index.html").read_text(encoding="utf-8")
                self.assertNotRegex(page, r'(?:src|href)\s*=\s*["\']https?://')
                self.assertNotIn("<script src", page)
                self.assertNotIn("fetch(", page)
            self.assertEqual(len(sample_ids), 3)

    def test_hintbench_smoke_batch_produces_the_same_run_bundle_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed, batch = run_batch(HINTBENCH_FIXTURE, "hintbench", Path(tmp) / "runs")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(batch["status"], "complete")
            self.assertEqual(batch["records"]["total"], 3)
            self.assertEqual(len(batch["samples"]), 3)

            batch_dir = Path(batch["batch_dir"])
            stored = read_json(batch_dir / "batch.json")
            self.assertEqual(stored["adapter"]["id"], "hintbench")
            self.assertEqual(stored["source"]["dataset_id"], "hintbench")

            for sample, run_dir, trajectory in collect_sample_artifacts(batch):
                for name in RUN_ARTIFACTS:
                    self.assertTrue((run_dir / name).is_file(), f"missing {name}")
                run = read_json(run_dir / "run.json")
                self.assertEqual(run["sample"]["source_format"], "hintbench")
                self.assertEqual(run["adapter"]["id"], "hintbench")
                self.assertEqual(trajectory["sample"]["sample_id"], sample["sample_id"])
                self.assertEqual(trajectory["source"]["dataset_id"], "hintbench")
                self.assertIn("HINTBench@", trajectory["source"]["dataset_version"])
                self.assertIn("source_labels", trajectory)
                self.assertIn("reference", trajectory)
                page = (run_dir / "index.html").read_text(encoding="utf-8")
                self.assertNotIn("<link", page)
                self.assertNotIn("XMLHttpRequest", page)

    def test_both_sources_share_the_same_unified_trajectory_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, atbench = run_batch(ATBENCH_FIXTURE, "atbench", Path(tmp) / "atbench")
            _, hintbench = run_batch(HINTBENCH_FIXTURE, "hintbench", Path(tmp) / "hintbench")
            trajectories = [
                trajectory
                for batch in (atbench, hintbench)
                for _, _, trajectory in collect_sample_artifacts(batch)
            ]
            self.assertEqual(len(trajectories), 6)

            top_keys = {tuple(sorted(trajectory.keys())) for trajectory in trajectories}
            self.assertEqual(len(top_keys), 1)
            event_keys = {
                tuple(sorted(event.keys()))
                for trajectory in trajectories
                for event in trajectory["events"]
            }
            self.assertEqual(len(event_keys), 1)
            self.assertIn("source_step_id", next(iter(event_keys)))

            seen_event_types = set()
            for trajectory in trajectories:
                self.assertEqual(trajectory["schema_version"], "trajectory/2")
                sequences = [event["sequence"] for event in trajectory["events"]]
                self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
                event_ids = [event["event_id"] for event in trajectory["events"]]
                self.assertEqual(len(event_ids), len(set(event_ids)))
                for event in trajectory["events"]:
                    self.assertIn(event["type"], EVENT_TYPES)
                    seen_event_types.add(event["type"])
                    source = event["source"]
                    self.assertEqual(source["dataset_id"], trajectory["source"]["dataset_id"])
                    self.assertEqual(
                        source["dataset_version"], trajectory["source"]["dataset_version"]
                    )
                    self.assertIsInstance(source["record_index"], int)
                    self.assertRegex(source["record_sha256"], r"^[0-9a-f]{64}$")
                    self.assertTrue(source["json_path"])
                    self.assertIn("raw", event)

            self.assertTrue({"message", "tool_call", "tool_result"} <= seen_event_types)
            self.assertIn("thought", seen_event_types)
            self.assertIn("state_change", seen_event_types)

            hintbench_steps: dict[tuple[str, object], int] = {}
            for trajectory in trajectories:
                for event in trajectory["events"]:
                    if event["source_step_id"] is None:
                        continue
                    key = (trajectory["sample"]["sample_id"], event["source_step_id"])
                    hintbench_steps[key] = hintbench_steps.get(key, 0) + 1
            self.assertTrue(
                any(count > 1 for count in hintbench_steps.values()),
                "一个来源步骤应能映射为多个事件",
            )

    def test_hintbench_source_step_labels_trace_to_the_source_step_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, batch = run_batch(HINTBENCH_FIXTURE, "hintbench", Path(tmp) / "runs")
            located = 0
            for _, _, trajectory in collect_sample_artifacts(batch):
                events_by_id = {event["event_id"]: event for event in trajectory["events"]}
                step_ids = {
                    step["source_step_id"]
                    for step in trajectory["source_steps"]
                    if step["source_step_id"] is not None
                }
                for label in trajectory["source_labels"]:
                    if label["scope"] == "trajectory":
                        continue
                    located += 1
                    self.assertEqual(label["scope"], "source_step")
                    self.assertTrue(label["located"])
                    self.assertIn(label["source_step_id"], step_ids)
                    self.assertTrue(label["event_ids"])
                    for event_id in label["event_ids"]:
                        self.assertIn(event_id, events_by_id)
                        self.assertEqual(
                            events_by_id[event_id]["source_step_id"], label["source_step_id"]
                        )
                    self.assertEqual(label["layer"], "source")
            self.assertGreaterEqual(located, 3)

    def test_atbench_labels_remain_trajectory_scoped_and_unlocated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, batch = run_batch(ATBENCH_FIXTURE, "atbench", Path(tmp) / "runs")
            verdicts = {}
            for _, _, trajectory in collect_sample_artifacts(batch):
                for label in trajectory["source_labels"]:
                    self.assertEqual(label["scope"], "trajectory")
                    self.assertFalse(label["located"])
                    self.assertIsNone(label["source_step_id"])
                    self.assertEqual(label["event_ids"], [])
                    self.assertEqual(label["layer"], "source")
                    verdicts[trajectory["sample"]["sample_id"]] = label["verdict"]
            self.assertEqual(verdicts["atbench-850"], "safe")
            self.assertEqual(verdicts["atbench-86"], "unsafe")
            self.assertEqual(verdicts["atbench-708"], "safe")

    def test_source_reference_and_detected_layers_do_not_impersonate_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, batch = run_batch(ATBENCH_FIXTURE, "atbench", Path(tmp) / "runs")
            for sample, run_dir, trajectory in collect_sample_artifacts(batch):
                self.assertEqual(trajectory["reference"]["status"], "absent")
                self.assertEqual(trajectory["reference"]["labels"], [])
                self.assertTrue(trajectory["source_labels"])

                audit = read_json(run_dir / "audit_result.json")
                self.assertEqual(audit["layer"], "detected")
                source_label_ids = {label["label_id"] for label in trajectory["source_labels"]}
                finding_ids = {finding["finding_id"] for finding in audit["findings"]}
                self.assertFalse(source_label_ids & finding_ids)
                # 来源标签 unsafe 不等于 detected 判定 unsafe。
                self.assertEqual(sample["verdict"], audit["verdict"]["value"])
                self.assertEqual(sample["verdict"], "undetermined")
                self.assertEqual(audit["checks"]["status"], "no_findings")

                metrics = read_json(run_dir / "metrics.json")
                self.assertEqual(metrics["source"]["dataset_id"], "atbench")
                self.assertEqual(metrics["source"]["source_labels"], len(trajectory["source_labels"]))
                self.assertEqual(metrics["reference"]["status"], "absent")
                self.assertEqual(metrics["audit"]["verdict"], "undetermined")

    def test_batch_page_links_every_sample_and_is_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, batch = run_batch(HINTBENCH_FIXTURE, "hintbench", Path(tmp) / "runs")
            page = (Path(batch["batch_dir"]) / "index.html").read_text(encoding="utf-8")
            self.assertIn(f'data-batch-status="{batch["status"]}"', page)
            for sample in batch["samples"]:
                self.assertIn(sample["sample_id"], page)
                self.assertIn(f'{sample["run_dir"]}/index.html', page)
            self.assertNotRegex(page, r'(?:src|href)\s*=\s*["\']https?://')
            self.assertNotIn("<script src", page)
            self.assertNotIn("<link", page)
            self.assertNotIn("fetch(", page)
            self.assertIn('id="batch-data"', page)


class SourceConversionFailureTest(unittest.TestCase):
    def build_atbench_batch(self, path: Path) -> Path:
        fixture = read_fixture(ATBENCH_FIXTURE)
        valid = copy.deepcopy(fixture["records"][0])
        invalid_schema = {
            "id": 999,
            "contents": [[{"role": "user", "content": "hello"}]],
            "label": 2,
        }
        unsupported = {
            "id": 998,
            "tool_used": [],
            "contents": [
                [
                    {"role": "user", "content": "hello"},
                    {"role": "agent", "thought": "", "action": "Unknown{not supported}"},
                ]
            ],
            "label": 0,
        }
        return write_json(
            path,
            {
                "schema_version": "source-batch/1",
                "source": fixture["source"],
                "records": [valid, invalid_schema, unsupported],
            },
        )

    def test_per_sample_failures_are_machine_readable_without_dropping_other_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_input = self.build_atbench_batch(Path(tmp) / "batch.json")
            completed, batch = run_batch(batch_input, "atbench", Path(tmp) / "runs")
            self.assertEqual(completed.returncode, 4, completed.stderr)
            self.assertEqual(batch["status"], "partial")
            self.assertEqual(len(batch["samples"]), 1)
            self.assertEqual(len(batch["failures"]), 2)

            failures_by_code = {failure["code"]: failure for failure in batch["failures"]}
            self.assertIn("schema_invalid", failures_by_code)
            self.assertIn("unsupported_record", failures_by_code)
            for failure in batch["failures"]:
                self.assertTrue(failure["sample_id"])
                self.assertIsInstance(failure["record_index"], int)
                self.assertTrue(failure["stage"])
                self.assertTrue(failure["reason"])

            batch_dir = Path(batch["batch_dir"])
            stored = read_json(batch_dir / "batch.json")
            self.assertEqual(stored["status"], "partial")
            self.assertEqual(stored["failures"], batch["failures"])
            self.assertEqual(read_json(batch_dir / "failures.json")["failures"], batch["failures"])
            self.assertTrue((batch_dir / batch["samples"][0]["run_dir"] / "run.json").is_file())
            page = (batch_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("schema_invalid", page)
            self.assertIn("unsupported_record", page)

    def test_dangling_label_reference_is_reported_as_source_integrity_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = read_fixture(HINTBENCH_FIXTURE)
            safe = copy.deepcopy(fixture["records"][0])
            broken = copy.deepcopy(fixture["records"][1])
            broken["risk_labels"][0]["risk_origin_step"] = 999
            batch_input = write_json(
                Path(tmp) / "batch.json",
                {
                    "schema_version": "source-batch/1",
                    "source": fixture["source"],
                    "records": [safe, broken],
                },
            )
            completed, batch = run_batch(batch_input, "hintbench", Path(tmp) / "runs")
            self.assertEqual(completed.returncode, 4, completed.stderr)
            self.assertEqual(len(batch["samples"]), 1)
            self.assertEqual(len(batch["failures"]), 1)
            failure = batch["failures"][0]
            self.assertEqual(failure["sample_id"], "slack_task_0008_risk_v7")
            # 悬空引用是来源数据缺陷，不是 adapter 的定位缺陷。
            self.assertEqual(failure["stage"], "source_integrity")
            self.assertEqual(failure["code"], "dangling_source_step_reference")
            self.assertEqual(failure["record_index"], 1)
            self.assertTrue(failure["reason"])

    def test_hintbench_label_without_locator_is_a_source_integrity_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = read_fixture(HINTBENCH_FIXTURE)
            safe = copy.deepcopy(fixture["records"][0])
            broken = copy.deepcopy(fixture["records"][1])
            broken["risk_labels"][0].pop("risk_origin_step", None)
            broken["risk_labels"][0].pop("step_id", None)
            batch_input = write_json(
                Path(tmp) / "batch.json",
                {
                    "schema_version": "source-batch/1",
                    "source": fixture["source"],
                    "records": [safe, broken],
                },
            )
            completed, batch = run_batch(batch_input, "hintbench", Path(tmp) / "runs")
            self.assertEqual(completed.returncode, 4, completed.stderr)
            self.assertEqual(len(batch["samples"]), 1)
            self.assertEqual(len(batch["failures"]), 1)
            failure = batch["failures"][0]
            self.assertEqual(failure["sample_id"], "slack_task_0008_risk_v7")
            self.assertEqual(failure["stage"], "source_integrity")
            self.assertEqual(failure["code"], "missing_source_step_reference")

    def test_duplicate_sample_ids_are_reported_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = read_fixture(ATBENCH_FIXTURE)
            first = copy.deepcopy(fixture["records"][0])
            second = copy.deepcopy(fixture["records"][0])
            batch_input = write_json(
                Path(tmp) / "batch.json",
                {
                    "schema_version": "source-batch/1",
                    "source": fixture["source"],
                    "records": [first, second],
                },
            )
            completed, batch = run_batch(batch_input, "atbench", Path(tmp) / "runs")
            self.assertEqual(completed.returncode, 4, completed.stderr)
            self.assertEqual(len(batch["samples"]), 1)
            self.assertEqual(len(batch["failures"]), 1)
            self.assertEqual(batch["failures"][0]["code"], "duplicate_sample_id")
            self.assertEqual(batch["failures"][0]["stage"], "sample_identity")
            self.assertTrue(
                (Path(batch["batch_dir"]) / batch["samples"][0]["run_dir"] / "run.json").is_file()
            )

    def test_completed_batch_identity_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"
            completed, first = run_batch(HINTBENCH_FIXTURE, "hintbench", out_root)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            batch_dir = Path(first["batch_dir"])
            before = {
                path.relative_to(batch_dir): path.read_bytes()
                for path in batch_dir.rglob("*")
                if path.is_file()
            }

            completed, _ = run_batch(HINTBENCH_FIXTURE, "hintbench", out_root)
            self.assertEqual(completed.returncode, 3, completed.stderr)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "batch_already_exists")
            self.assertEqual(error["details"]["batch_id"], first["batch_id"])

            after = {
                path.relative_to(batch_dir): path.read_bytes()
                for path in batch_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_batch_requires_an_explicit_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = invoke_cli(
                "batch",
                "--input",
                ATBENCH_FIXTURE,
                "--out",
                Path(tmp) / "runs",
                "--provider",
                PROVIDER_ID,
            )
            self.assertEqual(completed.returncode, 2)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "missing_adapter")

    def test_unknown_adapter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = invoke_cli(
                "batch",
                "--input",
                ATBENCH_FIXTURE,
                "--out",
                Path(tmp) / "runs",
                "--provider",
                PROVIDER_ID,
                "--adapter",
                "unknown-source",
            )
            self.assertEqual(completed.returncode, 2)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "unknown_adapter")
            self.assertIn("atbench", error["details"]["available"])


class DownstreamSourceIndependenceTest(unittest.TestCase):
    def test_downstream_modules_do_not_branch_on_dataset_names(self) -> None:
        for relative_path in DOWNSTREAM_MODULES:
            text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").lower()
            self.assertNotIn("atbench", text, relative_path)
            self.assertNotIn("hintbench", text, relative_path)

    def test_run_bundle_and_page_are_shared_by_both_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, atbench = run_batch(ATBENCH_FIXTURE, "atbench", Path(tmp) / "atbench")
            _, hintbench = run_batch(HINTBENCH_FIXTURE, "hintbench", Path(tmp) / "hintbench")
            artifact_keys = {tuple(sorted(batch["artifacts"])) for batch in (atbench, hintbench)}
            self.assertEqual(len(artifact_keys), 1)
            for batch in (atbench, hintbench):
                self.assertIn("index.html", (Path(batch["batch_dir"]) / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
