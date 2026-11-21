# SPDX-License-Identifier: Apache-2.0
"""Issue #15 验收测试：无标签 Provider 输入与 Judge 标签隔离。

测试优先通过 ``python -m trajectory_audit corpus`` 的生成产物断言外部行为；
白名单与摘要范围只在公开的投影/Schema 函数上断言，不复制实现细节。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from trajectory_audit import baseline, judge as judge_module
from trajectory_audit.audit_input import (
    AUDIT_INPUT_SCHEMA,
    AUDIT_TRAJECTORY_SCHEMA,
    audit_input_sha256,
    build_batch,
    canonical_item,
    project_policy,
    project_trajectory,
    validate_audit_input,
    validate_audit_trajectory,
)
from trajectory_audit.batch import execute_batch
from trajectory_audit.judge import JudgeResult, register_judge
from trajectory_audit.model import NormalizedTrajectory, sha256_json
from trajectory_audit.policy import load_policy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "trajectory_audit"
SMOKE = PROJECT_ROOT / "fixtures" / "smoke"
SINGLE_FIXTURE = FIXTURES / "single_clean_trajectory.json"
MULTI_FIXTURE = FIXTURES / "structural_batch.json"
ATBENCH_FIXTURE = SMOKE / "atbench_smoke.json"
HINTBENCH_FIXTURE = SMOKE / "hintbench_smoke.json"
PROVIDER_ID = "deterministic-baseline"

JUDGE_NAME = "test-provider-input-capture"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


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


def normalize_openai(payload: dict[str, Any]) -> NormalizedTrajectory:
    from trajectory_audit import openai_messages

    return openai_messages.normalize_record(
        payload,
        record_index=0,
        dataset_id="toy/source",
        dataset_version="toy/source@v1",
        input_sha256="0" * 64,
        record_json_path="",
        record_sha256=sha256_json(payload),
        sample_id="fallback",
    )[0]


def normalize_atbench(record: dict[str, Any]) -> NormalizedTrajectory:
    from trajectory_audit import atbench

    return atbench.normalize_record(
        record,
        record_index=0,
        dataset_id="toy/source",
        dataset_version="toy/source@v1",
        input_sha256="0" * 64,
        record_json_path="",
        record_sha256=sha256_json(record),
        sample_id="fallback",
    )[0]


def valid_item() -> dict[str, Any]:
    record = read_json(ATBENCH_FIXTURE)["records"][0]
    return canonical_item(normalize_atbench(record), None)


def all_keys(payload: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= all_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            keys |= all_keys(value)
    return keys


class CorpusCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def build(
        self,
        source: Path,
        *,
        name: str,
        adapter: str | None = None,
        policy: Path | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        out = self.root / name
        args: list[object] = ["corpus", "build", "--input", source, "--out", out]
        if adapter:
            args.extend(["--adapter", adapter])
        if policy:
            args.extend(["--policy", policy])
        completed = invoke_cli(*args)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        return summary, read_json(out)

    def test_single_fixture_builds_one_item_batch(self) -> None:
        summary, batch = self.build(SINGLE_FIXTURE, name="single.json")
        self.assertEqual(summary["status"], "written")
        self.assertEqual(batch["schema_version"], AUDIT_INPUT_SCHEMA)
        self.assertTrue(batch["batch_id"])
        self.assertEqual(len(batch["items"]), 1)
        item = batch["items"][0]
        self.assertEqual(set(item), {"sample_id", "audit_input_sha256", "trajectory", "policy"})
        self.assertIn("policy", item)
        self.assertIsNone(item["policy"])
        self.assertEqual(item["trajectory"]["schema_version"], AUDIT_TRAJECTORY_SCHEMA)
        self.assertEqual(validate_audit_input(batch), [])
        self.assertEqual(validate_audit_trajectory(item["trajectory"]), [])

        validated = invoke_cli("corpus", "validate", "--input", self.root / "single.json")
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "pass")

    def test_batch_fixture_builds_multi_item_batch(self) -> None:
        records = read_json(MULTI_FIXTURE)["records"]
        _, batch = self.build(MULTI_FIXTURE, name="multi.json")
        self.assertEqual(len(batch["items"]), len(records))
        self.assertEqual(
            [item["sample_id"] for item in batch["items"]],
            [record["sample_id"] for record in records],
        )
        for item in batch["items"]:
            self.assertIsNone(item["policy"])
            self.assertEqual(validate_audit_trajectory(item["trajectory"]), [])
        self.assertEqual(validate_audit_input(batch), [])

    def test_repeated_build_is_byte_identical(self) -> None:
        _, first = self.build(SINGLE_FIXTURE, name="first.json")
        _, second = self.build(SINGLE_FIXTURE, name="second.json")
        self.assertEqual(first, second)
        self.assertEqual(
            (self.root / "first.json").read_bytes(),
            (self.root / "second.json").read_bytes(),
        )

    def test_atbench_and_hintbench_fixtures_produce_audit_trajectories(self) -> None:
        for source, adapter, name in (
            (ATBENCH_FIXTURE, "atbench", "atbench.json"),
            (HINTBENCH_FIXTURE, "hintbench", "hintbench.json"),
        ):
            _, batch = self.build(source, name=name, adapter=adapter)
            self.assertEqual(validate_audit_input(batch), [])
            for item in batch["items"]:
                self.assertEqual(
                    item["trajectory"]["schema_version"], AUDIT_TRAJECTORY_SCHEMA
                )
                self.assertIsInstance(item["trajectory"]["tool_definitions"], list)
                self.assertIsInstance(
                    item["trajectory"]["coverage"]["unsupported_event_ids"], list
                )

    def test_policy_is_embedded_only_for_bound_samples(self) -> None:
        summary, batch = self.build(
            FIXTURES / "policy_allowed_batch.json",
            name="policy.json",
            policy=FIXTURES / "policy_allowed.json",
        )
        self.assertEqual(summary["policy_items"], 1)
        item = batch["items"][0]
        self.assertIsNotNone(item["policy"])
        self.assertEqual(item["policy"]["policy_id"], "policy-allowed-v1")
        # policy 的本地来源路径与文件哈希属于课题五血缘，不进入 provider 输入。
        self.assertNotIn("source", item["policy"])

    def test_unknown_source_fields_never_enter_input(self) -> None:
        canaries = {
            "label": "CANARY-SOURCE-LABEL-7f1a",
            "reason": "CANARY-SOURCE-REASON-7f1a",
            "benchmark": "CANARY-BENCHMARK-7f1a",
            "dataset": "CANARY-DATASET-7f1a",
            "reference": "CANARY-REFERENCE-7f1a",
            "detected": "CANARY-DETECTED-7f1a",
        }
        record = {
            "sample_id": "unknown-field-sample",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ],
            "source_label": canaries["label"],
            "source_reason": canaries["reason"],
            "benchmark_name": canaries["benchmark"],
            "dataset_id": canaries["dataset"],
            "reference": {"note": canaries["reference"]},
            "detected": {"finding": canaries["detected"]},
        }
        source = write_json(self.root / "source.json", record)
        _, batch = self.build(source, name="unknown.json")
        text = json.dumps(batch, ensure_ascii=False)
        for canary in canaries.values():
            self.assertNotIn(canary, text)
        self.assertNotIn("unmapped_top_level_fields", text)
        self.assertNotIn("source_label", text)

    def test_policy_nested_fields_never_reach_input_or_digest(self) -> None:
        canary = "CANARY-POLICY-NESTED-4d8b"
        policy = read_json(FIXTURES / "policy_allowed.json")
        policy["tools"][0]["source"] = canary
        policy["tools"][0]["raw_marker"] = canary
        policy["tools"][0]["constraints"][0]["raw_marker"] = canary
        policy_path = write_json(self.root / "policy-nested.json", policy)
        _, batch = self.build(
            FIXTURES / "policy_allowed_batch.json",
            name="policy-nested-out.json",
            policy=policy_path,
        )
        written = (self.root / "policy-nested-out.json").read_text(encoding="utf-8")
        self.assertNotIn(canary, written)
        self.assertEqual(validate_audit_input(batch), [])

        _, clean = self.build(
            FIXTURES / "policy_allowed_batch.json",
            name="policy-clean.json",
            policy=FIXTURES / "policy_allowed.json",
        )
        self.assertEqual(
            batch["items"][0]["audit_input_sha256"],
            clean["items"][0]["audit_input_sha256"],
        )

    def test_tool_definition_free_text_never_reaches_written_file(self) -> None:
        canary = "CANARY-TOOL-DEFINITION-6e2a"
        record = copy.deepcopy(read_json(ATBENCH_FIXTURE)["records"][0])
        record["tool_used"] = [canary, {"name": "safe_tool", "source_marker": canary}]
        source = write_json(self.root / "tool-canary.json", record)
        _, batch = self.build(
            source, name="tool-canary-out.json", adapter="atbench"
        )
        written = (self.root / "tool-canary-out.json").read_text(encoding="utf-8")
        self.assertNotIn(canary, written)
        self.assertEqual(
            batch["items"][0]["trajectory"]["tool_definitions"],
            [{"name": "safe_tool"}],
        )
        self.assertEqual(validate_audit_input(batch), [])

    def test_atbench_labels_and_reason_never_enter_input(self) -> None:
        record = copy.deepcopy(read_json(ATBENCH_FIXTURE)["records"][0])
        record["reason"] = "CANARY-ATBENCH-REASON-91c2"
        record["risk_source"] = "CANARY-ATBENCH-SOURCE-91c2"
        record["failure_mode"] = "CANARY-ATBENCH-MODE-91c2"
        record["real_world_harm"] = "CANARY-ATBENCH-HARM-91c2"
        record["benchmark_name"] = "CANARY-ATBENCH-BENCH-91c2"
        source = write_json(self.root / "atbench.json", record)
        _, batch = self.build(source, name="atbench-canary.json", adapter="atbench")
        text = json.dumps(batch, ensure_ascii=False)
        for canary in (
            "CANARY-ATBENCH-REASON-91c2",
            "CANARY-ATBENCH-SOURCE-91c2",
            "CANARY-ATBENCH-MODE-91c2",
            "CANARY-ATBENCH-HARM-91c2",
            "CANARY-ATBENCH-BENCH-91c2",
        ):
            self.assertNotIn(canary, text)
        self.assertTrue(batch["items"][0]["trajectory"]["events"])


class ProjectionWhitelistTest(unittest.TestCase):
    CANARIES = {
        "label": "CANARY-PROJECTION-LABEL-2b4d",
        "reference": "CANARY-PROJECTION-REFERENCE-2b4d",
        "raw": "CANARY-PROJECTION-RAW-2b4d",
        "step": "CANARY-PROJECTION-STEP-2b4d",
        "dataset": "CANARY-PROJECTION-DATASET-2b4d",
        "unmapped": "CANARY-PROJECTION-UNMAPPED-2b4d",
        "detected": "CANARY-PROJECTION-DETECTED-2b4d",
        "human": "CANARY-PROJECTION-HUMAN-2b4d",
        "event": "CANARY-PROJECTION-EVENT-2b4d",
    }

    def setUp(self) -> None:
        record = read_json(ATBENCH_FIXTURE)["records"][0]
        self.trajectory = normalize_atbench(record)
        self.assertTrue(self.trajectory.source_labels)

    def test_forbidden_layers_never_reach_the_projection(self) -> None:
        trajectory = copy.deepcopy(self.trajectory)
        label = trajectory.source_labels[0]
        trajectory.source_labels = [
            replace(
                label,
                reason=self.CANARIES["label"],
                categories=[self.CANARIES["label"]],
                source_categories=[self.CANARIES["label"]],
                raw={"reason": self.CANARIES["label"]},
                mapping={"note": self.CANARIES["label"]},
            )
        ]
        trajectory.reference = {
            "status": "present",
            "labels": [
                {
                    "reason": self.CANARIES["reference"],
                    "decision": self.CANARIES["human"],
                }
            ],
            "note": self.CANARIES["reference"],
        }
        trajectory.record_raw = {"secret": self.CANARIES["raw"]}
        trajectory.source_steps = [
            replace(step, raw={"secret": self.CANARIES["step"]})
            for step in trajectory.source_steps
        ]
        trajectory.dataset_id = self.CANARIES["dataset"]
        trajectory.dataset_version = self.CANARIES["dataset"]
        trajectory.unmapped_top_level_fields = [self.CANARIES["unmapped"]]
        trajectory.detected = {"findings": [{"reason": self.CANARIES["detected"]}]}

        payload = canonical_item(trajectory, None)
        text = json.dumps(payload, ensure_ascii=False)
        for canary in self.CANARIES.values():
            self.assertNotIn(canary, text)
        keys = all_keys(payload)
        for forbidden in (
            "source",
            "raw",
            "source_labels",
            "source_steps",
            "reference",
            "dataset_id",
            "dataset_version",
            "record",
            "findings",
            "detected",
        ):
            self.assertNotIn(forbidden, keys)

    def test_non_object_tool_shapes_are_dropped(self) -> None:
        trajectory = copy.deepcopy(self.trajectory)
        trajectory.tool_definitions = [
            {"name": "safe_tool", "function": self.CANARIES["event"]}
        ]
        payload = project_trajectory(trajectory)
        self.assertNotIn(self.CANARIES["event"], json.dumps(payload))
        self.assertEqual(payload["tool_definitions"], [{"name": "safe_tool"}])
        self.assertEqual(validate_audit_trajectory(payload), [])

        projected_policy = project_policy(
            {
                "schema_version": "audit-policy/1",
                "policy_id": "p",
                "policy_version": "1",
                "scope": "trajectory",
                "tools": self.CANARIES["event"],
            }
        )
        self.assertNotIn(self.CANARIES["event"], json.dumps(projected_policy))
        self.assertNotIn("tools", projected_policy)

    def test_unknown_event_content_keys_are_dropped(self) -> None:
        trajectory = copy.deepcopy(self.trajectory)
        first = trajectory.events[0]
        trajectory.events[0] = replace(
            first,
            content={
                **first.content,
                "source": {"raw": self.CANARIES["event"]},
                "raw": self.CANARIES["event"],
                "surprise": self.CANARIES["event"],
            },
        )
        payload = canonical_item(trajectory, None)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.CANARIES["event"], text)
        self.assertEqual(validate_audit_trajectory(payload["trajectory"]), [])

    def test_missing_tool_definitions_and_unsupported_set_become_empty_arrays(self) -> None:
        trajectory = copy.deepcopy(self.trajectory)
        trajectory.tool_definitions = None
        payload = project_trajectory(trajectory)
        self.assertEqual(payload["tool_definitions"], [])
        self.assertEqual(payload["coverage"]["unsupported_event_ids"], [])
        self.assertEqual(payload["coverage"]["tool_definition_count"], 0)
        self.assertEqual(validate_audit_trajectory(payload), [])

    def test_unknown_tool_definition_fields_are_dropped(self) -> None:
        trajectory = copy.deepcopy(self.trajectory)
        self.assertTrue(trajectory.tool_definitions)
        trajectory.tool_definitions = [
            {
                **trajectory.tool_definitions[0],
                "source_marker": self.CANARIES["event"],
                "function": {"name": "x", "secret": self.CANARIES["event"]},
            }
        ]
        payload = project_trajectory(trajectory)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.CANARIES["event"], text)
        self.assertNotIn("source_marker", text)
        self.assertEqual(validate_audit_trajectory(payload), [])
        tampered = copy.deepcopy(payload)
        tampered["tool_definitions"][0]["source_marker"] = True
        paths = {issue["path"] for issue in validate_audit_trajectory(tampered)}
        self.assertIn("tool_definitions[0].source_marker", paths)


class DigestScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        record = read_json(ATBENCH_FIXTURE)["records"][0]
        self.trajectory = normalize_atbench(record)

    def test_source_labels_and_reference_do_not_change_digest(self) -> None:
        baseline = canonical_item(self.trajectory, None)["audit_input_sha256"]
        changed = copy.deepcopy(self.trajectory)
        label = changed.source_labels[0]
        changed.source_labels = [
            replace(
                label,
                verdict="safe" if label.verdict == "unsafe" else "unsafe",
                reason="REWRITTEN",
                raw={"rewritten": True},
            )
        ]
        changed.reference = {"status": "present", "labels": [{"x": 1}], "note": "REWRITTEN"}
        changed.record_raw = {"rewritten": True}
        changed.dataset_id = "rewritten/dataset"
        changed.unmapped_top_level_fields = ["rewritten"]
        after = canonical_item(changed, None)["audit_input_sha256"]
        self.assertEqual(baseline, after)

    def test_provider_visible_event_change_changes_digest(self) -> None:
        baseline = canonical_item(self.trajectory, None)["audit_input_sha256"]
        changed = copy.deepcopy(self.trajectory)
        first = changed.events[0]
        content = dict(first.content)
        if "text" in content:
            content["text"] = content["text"] + " CHANGED"
        else:
            content["changed"] = True
        changed.events[0] = replace(first, content=content)
        self.assertNotEqual(
            baseline, canonical_item(changed, None)["audit_input_sha256"]
        )

    def test_policy_changes_digest(self) -> None:
        policy = {
            "schema_version": "audit-policy/1",
            "policy_id": "policy-x",
            "policy_version": "1",
            "scope": "trajectory",
            "tools": [],
        }
        without = canonical_item(self.trajectory, None)
        with_policy = canonical_item(self.trajectory, policy)
        self.assertNotEqual(
            without["audit_input_sha256"], with_policy["audit_input_sha256"]
        )
        self.assertEqual(
            with_policy["audit_input_sha256"],
            audit_input_sha256(with_policy["trajectory"], with_policy["policy"]),
        )

    def test_declared_digest_must_match_recomputed_value(self) -> None:
        batch = build_batch([canonical_item(self.trajectory, None)])
        batch["items"][0]["audit_input_sha256"] = "0" * 64
        paths = {issue["path"] for issue in validate_audit_input(batch)}
        self.assertIn("items[0].audit_input_sha256", paths)


class SchemaValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = build_batch([valid_item()])

    def test_valid_batch_passes(self) -> None:
        self.assertEqual(validate_audit_input(self.batch), [])
        self.assertEqual(
            validate_audit_trajectory(self.batch["items"][0]["trajectory"]), []
        )

    def test_duplicate_event_identity_is_rejected(self) -> None:
        payload = copy.deepcopy(self.batch)
        events = payload["items"][0]["trajectory"]["events"]
        events[1]["event_id"] = events[0]["event_id"]
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[0].trajectory.events[1].event_id", paths)

    def test_sequence_must_be_strictly_increasing(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["items"][0]["trajectory"]["events"][1]["sequence"] = 9
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[0].trajectory.events[1].sequence", paths)

    def test_coverage_must_reference_existing_events(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["items"][0]["trajectory"]["coverage"]["unsupported_event_ids"] = [
            "evt-9999"
        ]
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[0].trajectory.coverage.unsupported_event_ids", paths)

    def test_policy_key_must_be_explicit(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["items"][0].pop("policy")
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[0].policy", paths)

    def test_requested_taxonomy_is_rejected(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["requested_taxonomy"] = ["unsafe"]
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("requested_taxonomy", paths)

    def test_unknown_event_content_key_is_rejected(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["items"][0]["trajectory"]["events"][0]["content"]["surprise"] = True
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[0].trajectory.events[0].content.surprise", paths)

    def test_duplicate_sample_identity_is_rejected(self) -> None:
        duplicate = copy.deepcopy(self.batch["items"][0])
        payload = build_batch([self.batch["items"][0], duplicate])
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[1].sample_id", paths)

    def test_unknown_policy_field_is_rejected(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["items"][0]["policy"] = {
            "schema_version": "audit-policy/1",
            "policy_id": "policy-x",
            "policy_version": "1",
            "scope": "trajectory",
            "source": {"path": "local", "sha256": "0" * 64},
        }
        paths = {issue["path"] for issue in validate_audit_input(payload)}
        self.assertIn("items[0].policy.source", paths)

    def test_cli_validate_reports_issues_with_exit_code_four(self) -> None:
        payload = copy.deepcopy(self.batch)
        payload["items"][0]["trajectory"]["events"][1]["sequence"] = 9
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp) / "tampered.json", payload)
            completed = invoke_cli("corpus", "validate", "--input", path)
        self.assertEqual(completed.returncode, 4, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["issues"])


class JudgeProjectionTest(unittest.TestCase):
    captured: list[tuple[dict[str, Any], Any]] = []

    def setUp(self) -> None:
        self.captured = []
        JudgeProjectionTest.captured = self.captured
        self.addCleanup(judge_module._REGISTRY.pop, JUDGE_NAME, None)

    def test_judge_receives_the_same_audit_trajectory_projection(self) -> None:
        captured = self.captured

        class CaptureJudge:
            def evaluate(
                self,
                *,
                trajectory: dict[str, Any],
                policy: dict[str, Any] | None,
                findings: list[Any],
            ) -> JudgeResult:
                captured.append((trajectory, policy))
                return JudgeResult(status="abstain", reason="captured")

        register_judge(JUDGE_NAME, CaptureJudge)
        policy = load_policy(FIXTURES / "policy_allowed.json")
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_batch(
                input_path=FIXTURES / "policy_allowed_batch.json",
                out_root=Path(tmp) / "runs",
                provider_id=PROVIDER_ID,
                adapter_id="openai-messages",
                policy=policy,
                judge_name=JUDGE_NAME,
            )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(captured), len(result["samples"]))
        bound_policies = [item[1] for item in captured if item[1] is not None]
        self.assertTrue(bound_policies)
        for trajectory, projected_policy in captured:
            self.assertEqual(trajectory["schema_version"], AUDIT_TRAJECTORY_SCHEMA)
            self.assertEqual(validate_audit_trajectory(trajectory), [])
            self.assertEqual(
                set(trajectory),
                {"schema_version", "events", "tool_definitions", "coverage"},
            )
            keys = all_keys(trajectory)
            for forbidden in (
                "source",
                "raw",
                "source_labels",
                "reference",
                "dataset_id",
                "dataset_version",
            ):
                self.assertNotIn(forbidden, keys)

            if projected_policy is None:
                continue
            self.assertEqual(projected_policy["schema_version"], "audit-policy/1")
            policy_keys = all_keys(projected_policy)
            for forbidden in ("source", "source_path", "source_sha256"):
                self.assertNotIn(forbidden, policy_keys)
            self.assertNotIn(policy.source_path, json.dumps(projected_policy))


    def test_judge_never_sees_free_text_tool_shapes(self) -> None:
        canary = "CANARY-JUDGE-TOOL-FUNCTION-9a1c"
        captured: list[dict[str, Any]] = []

        class CaptureJudge:
            def evaluate(
                self,
                *,
                trajectory: dict[str, Any],
                policy: dict[str, Any] | None,
                findings: list[Any],
            ) -> JudgeResult:
                captured.append(trajectory)
                return JudgeResult(status="abstain", reason="captured")

        trajectory = normalize_atbench(read_json(ATBENCH_FIXTURE)["records"][0])
        trajectory.tool_definitions = [{"name": "safe_tool", "function": canary}]
        baseline.audit(trajectory, judge=CaptureJudge())
        self.assertEqual(len(captured), 1)
        self.assertNotIn(canary, json.dumps(captured[0]))
        self.assertEqual(
            captured[0]["tool_definitions"], [{"name": "safe_tool"}]
        )


class CorpusCliErrorTest(unittest.TestCase):
    def test_missing_arguments_are_machine_readable(self) -> None:
        build = invoke_cli("corpus", "build")
        self.assertEqual(build.returncode, 2)
        self.assertEqual(json.loads(build.stderr)["code"], "missing_input")
        validate = invoke_cli("corpus", "validate")
        self.assertEqual(validate.returncode, 2)
        self.assertEqual(json.loads(validate.stderr)["code"], "missing_input")

    def test_missing_action_is_rejected(self) -> None:
        completed = invoke_cli("corpus")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stderr)["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
