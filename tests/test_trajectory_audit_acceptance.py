# SPDX-License-Identifier: Apache-2.0
"""Acceptance tests for the frozen acceptance set and H1 verification.

The full-dataset H1 run needs 30 MB of pinned data, so these tests use a synthetic
registry with the same shapes: real adapter code, real schema validation, real
downstream probe, toy datasets. The committed ``acceptance-v1`` manifest is checked
separately without touching any dataset file.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trajectory_audit import acceptance, atbench, hintbench, openai_messages, pinned
from trajectory_audit.errors import RunError
from trajectory_audit.schema import locate_json_path, validate_trajectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "fixtures" / "smoke"
ATBENCH_FIXTURE = FIXTURES / "atbench_smoke.json"
HINTBENCH_FIXTURE = FIXTURES / "hintbench_smoke.json"
COMMITTED_MANIFEST = PROJECT_ROOT / "acceptance" / "acceptance-v1.json"
COMMITTED_REPORT = PROJECT_ROOT / "acceptance" / "acceptance-v1.h1.json"

TEST_MANIFEST_ID = "acceptance-test"

H1_CHECK_IDS = (
    "sources_pinned",
    "files_verified",
    "manifest_frozen",
    "records_accounted",
    "zero_conversion_errors",
    "exclusions_match_frozen_manifest",
    "schema_valid",
    "event_traceability",
    "source_label_traceability",
    "downstream_source_neutral",
    "acceptance_samples_complete",
)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_policy() -> dict:
    atbench_layers = {
        "safe": {"field": "label", "true_value": 0},
        "unsafe": {"field": "label", "true_value": 1},
    }
    hintbench_layers = {
        "safe": {"field": "is_risky", "true_value": False},
        "risky": {"field": "is_risky", "true_value": True},
    }
    return {
        "seed": 7,
        "algorithm": "stratified-largest-remainder/1",
        "selection_basis": "source labels only; detected results never influence selection",
        "risk_layers": {
            "toy_atbench": atbench_layers,
            "toy_hintbench": hintbench_layers,
        },
        "stratification": {
            "toy_atbench": {
                "safe": {"layer": atbench_layers["safe"], "field": "failure_mode"},
                "unsafe": {"layer": atbench_layers["unsafe"], "field": "risk_source"},
            },
            "toy_hintbench": {
                "safe": {"layer": hintbench_layers["safe"], "field": "env"},
                "risky": {"layer": hintbench_layers["risky"], "kind": "primary_category"},
            },
        },
        "quotas": {
            "toy_atbench": {"total": 6, "strata": {"safe": 2, "unsafe": 4}},
            "toy_hintbench": {"total": 6, "strata": {"safe": 2, "risky": 4}},
        },
    }


def toy_atbench_records() -> list[dict]:
    template = read_json(ATBENCH_FIXTURE)["records"][0]  # type: ignore[index]
    records: list[dict] = []
    safe_modes = (
        "benign",
        "benign",
        "procedural_deviation_or_inaction",
        "incorrect_tool_parameters",
        "failure_to_validate_tool_outputs",
        "benign",
    )
    for index, mode in enumerate(safe_modes):
        record = copy.deepcopy(template)
        record["id"] = f"toy-safe-{index}"
        record["label"] = 0
        record["failure_mode"] = mode
        record["risk_source"] = "none"
        records.append(record)
    unsafe_sources = (
        "indirect_prompt_injection",
        "tool_description_injection",
        "indirect_prompt_injection",
        "tool_description_injection",
        "direct_prompt_injection",
        "corrupted_tool_feedback",
        "inherent_agent_failures",
        "malicious_tool_execution",
    )
    for index, source in enumerate(unsafe_sources):
        record = copy.deepcopy(template)
        record["id"] = f"toy-unsafe-{index}"
        record["label"] = 1
        record["risk_source"] = source
        records.append(record)
    return records


def toy_hintbench_records() -> list[dict]:
    fixture = read_json(HINTBENCH_FIXTURE)["records"]  # type: ignore[index]
    safe_template = fixture[0]
    risky_template = fixture[1]
    records: list[dict] = []
    for index, env in enumerate(("alpha", "alpha", "beta", "gamma")):
        record = copy.deepcopy(safe_template)
        record["task_id"] = f"toy-hint-safe-{index}"
        record["env"] = env
        records.append(record)
    for index, category in enumerate(
        (
            "State Constraint",
            "State Constraint",
            "Goal Constraint",
            "Factual Constraint",
            "Capability Constraint",
        )
    ):
        record = copy.deepcopy(risky_template)
        record["task_id"] = f"toy-hint-risky-{index}"
        for label in record["risk_labels"]:
            label["category"] = category
        records.append(record)
    broken = copy.deepcopy(risky_template)
    broken["task_id"] = "toy-hint-risky-broken"
    broken["risk_labels"][0]["risk_origin_step"] = 999
    records.append(broken)
    return records


def write_toy_datasets(root: Path) -> dict[str, dict]:
    payloads = {
        "toy_atbench": ("atbench", "toy_atbench.json", toy_atbench_records()),
        "toy_hintbench": ("hintbench", "toy_hintbench.json", toy_hintbench_records()),
    }
    registry: dict[str, dict] = {}
    for dataset_id, (adapter_id, cache_name, records) in payloads.items():
        path = write_json(root / cache_name, records)
        raw = path.read_bytes()
        registry[dataset_id] = {
            "dataset_id": dataset_id,
            "adapter_id": adapter_id,
            "dataset_version": f"toy/{dataset_id}@v1",
            "upstream": "https://example.invalid/toy",
            "upstream_commit": "0" * 40,
            "url": f"https://example.invalid/{cache_name}",
            "license": "Apache-2.0",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "cache_name": cache_name,
            "layout": "json_array",
            "record_identity": "toy",
        }
    return registry


def reseal(manifest: dict) -> dict:
    """重算内容哈希，用于隔离「内容被改写」以外的单点校验。"""
    manifest["integrity"] = {
        "digest_version": acceptance.INTEGRITY_VERSION,
        "content_sha256": acceptance.content_sha256(manifest),
    }
    return manifest


def json_cli(*args: object) -> subprocess.CompletedProcess[str]:
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


from trajectory_audit import acceptance, atbench, hintbench, openai_messages, pinned  # noqa: E402
from trajectory_audit.errors import RunError  # noqa: E402
from trajectory_audit.schema import (  # noqa: E402
    locate_json_path,
    validate_trajectory,
)


class ToyFixtureCase(unittest.TestCase):
    """把固定来源表替换成玩具数据集，其余流程保持真实。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dataset_root = self.root / "datasets"
        self.registry = write_toy_datasets(self.dataset_root)
        self.policy = test_policy()
        for patcher in (
            mock.patch.dict(pinned.BY_DATASET, self.registry, clear=True),
            mock.patch.dict(
                pinned.ACCEPTANCE_POLICIES, {TEST_MANIFEST_ID: self.policy}, clear=True
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self, **kwargs: object) -> dict:
        return acceptance.build_manifest(
            dataset_root=self.dataset_root,
            manifest_id=TEST_MANIFEST_ID,
            **kwargs,  # type: ignore[arg-type]
        )

    def republish_atbench(self, extra_benign: int = 3) -> None:
        """模拟上游重新发布：追加记录并更新固定指纹，使选中集合发生变化。"""
        records = toy_atbench_records()
        for index in range(extra_benign):
            record = copy.deepcopy(records[0])
            record["id"] = f"toy-safe-extra-{index}"
            records.append(record)
        path = write_json(
            self.dataset_root / self.registry["toy_atbench"]["cache_name"], records
        )
        raw = path.read_bytes()
        self.registry["toy_atbench"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.registry["toy_atbench"]["bytes"] = len(raw)


class ManifestFreezeTest(ToyFixtureCase):
    def test_build_satisfies_quota_and_strata(self) -> None:
        manifest = self.build()
        per_stratum: dict[tuple[str, str], int] = {}
        for sample in manifest["samples"]:
            key = (sample["dataset_id"], sample["stratum"])
            per_stratum[key] = per_stratum.get(key, 0) + 1
        self.assertEqual(per_stratum[("toy_atbench", "safe")], 2)
        self.assertEqual(per_stratum[("toy_atbench", "unsafe")], 4)
        self.assertEqual(per_stratum[("toy_hintbench", "safe")], 2)
        self.assertEqual(per_stratum[("toy_hintbench", "risky")], 4)
        self.assertEqual(len(manifest["samples"]), 12)
        self.assertEqual(len({item["sample_id"] for item in manifest["samples"]}), 12)

    def test_records_with_dangling_labels_are_excluded_with_reason(self) -> None:
        manifest = self.build()
        self.assertEqual(len(manifest["excluded_records"]), 1)
        excluded = manifest["excluded_records"][0]
        self.assertEqual(excluded["sample_id"], "toy-hint-risky-broken")
        self.assertEqual(excluded["dataset_id"], "toy_hintbench")
        self.assertEqual(excluded["stage"], "source_integrity")
        self.assertEqual(excluded["code"], "dangling_source_step_reference")
        self.assertTrue(excluded["reason"])
        self.assertNotIn(
            excluded["sample_id"],
            {sample["sample_id"] for sample in manifest["samples"]},
        )

    def test_selection_is_reproducible_and_stamped(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(
            first["integrity"]["content_sha256"], second["integrity"]["content_sha256"]
        )
        self.assertEqual(first["samples"], second["samples"])
        self.assertEqual(first["policy"]["seed"], 7)
        self.assertEqual(
            first["policy"]["algorithm"], "stratified-largest-remainder/1"
        )
        self.assertIn("source labels only", first["policy"]["selection_basis"])

    def test_every_sample_carries_source_label_basis(self) -> None:
        manifest = self.build()
        for sample in manifest["samples"]:
            self.assertIn("risk_layer", sample["source_label"])
            self.assertTrue(sample["source_label"]["source_fields"])
            self.assertEqual(sample["stratum"], sample["source_label"]["risk_layer"])

    def test_manifest_validates(self) -> None:
        manifest = self.build()
        self.assertEqual(
            acceptance.validate_manifest(manifest, expected_id=TEST_MANIFEST_ID), []
        )

    def test_tampered_sample_breaks_frozen_hash(self) -> None:
        manifest = self.build()
        manifest["samples"][0]["substratum"] = "rewritten"
        issues = acceptance.validate_manifest(manifest)
        self.assertIn(
            "integrity.content_sha256", {issue["path"] for issue in issues}
        )

    def test_policy_drift_is_rejected_even_when_resealed(self) -> None:
        manifest = self.build()
        manifest["policy"]["quotas"]["toy_atbench"]["total"] = 99
        reseal(manifest)
        issues = acceptance.validate_manifest(manifest)
        self.assertIn("policy.quotas", {issue["path"] for issue in issues})

    def test_duplicate_sample_is_rejected(self) -> None:
        manifest = self.build()
        duplicate = copy.deepcopy(manifest["samples"][0])
        manifest["samples"].append(duplicate)
        reseal(manifest)
        issues = acceptance.validate_manifest(manifest)
        paths = {issue["path"] for issue in issues}
        self.assertIn("samples[12].record_index", paths)

    def test_unknown_manifest_id_is_rejected(self) -> None:
        with self.assertRaises(RunError) as caught:
            acceptance.build_manifest(
                dataset_root=self.dataset_root, manifest_id="acceptance-nope"
            )
        self.assertEqual(caught.exception.code, "unknown_acceptance_manifest")

    def test_hash_mismatch_refuses_to_freeze(self) -> None:
        path = self.dataset_root / self.registry["toy_atbench"]["cache_name"]
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(RunError) as caught:
            self.build()
        self.assertEqual(caught.exception.code, "dataset_hash_mismatch")

    def test_replacement_requires_reason(self) -> None:
        first = self.build()
        self.republish_atbench()
        with self.assertRaises(RunError) as caught:
            self.build(previous=first)
        self.assertEqual(caught.exception.code, "replacement_reason_required")

    def test_replacement_keeps_before_and_after_identity(self) -> None:
        first = self.build()
        self.republish_atbench()
        second = self.build(previous=first, replacement_reason="上游重新发布")
        self.assertTrue(second["replacements"])
        entry = second["replacements"][0]
        self.assertTrue(entry["removed"]["sample_id"])
        self.assertTrue(entry["added"]["sample_id"])
        self.assertEqual(entry["reason"], "上游重新发布")
        self.assertTrue(entry["decided_at"])
        current = {sample["sample_id"] for sample in second["samples"]}
        replaced_away = {
            item["removed"]["sample_id"]
            for item in second["replacements"]
            if item["removed"]["sample_id"]
        }
        self.assertFalse(current & replaced_away)
        self.assertEqual(acceptance.validate_manifest(second), [])
        self.assertNotEqual(
            first["integrity"]["content_sha256"], second["integrity"]["content_sha256"]
        )


class VerifyTest(ToyFixtureCase):
    def test_verify_passes_and_reports_every_check(self) -> None:
        manifest = self.build()
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.dataset_root
        )
        self.assertEqual(report["status"], "pass", report["failures"])
        self.assertEqual(tuple(check["id"] for check in report["checks"]), H1_CHECK_IDS)
        self.assertTrue(all(check["status"] == "pass" for check in report["checks"]))
        self.assertEqual(report["failures"], [])
        self.assertEqual(
            report["manifest_content_sha256"], manifest["integrity"]["content_sha256"]
        )

    def test_verify_accounts_for_every_record(self) -> None:
        manifest = self.build()
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.dataset_root
        )
        by_source = {source["dataset_id"]: source for source in report["sources"]}
        atbench_source = by_source["toy_atbench"]
        self.assertEqual(atbench_source["records_total"], 14)
        self.assertEqual(atbench_source["records_converted"], 14)
        self.assertEqual(atbench_source["records_source_invalid"], 0)
        hint_source = by_source["toy_hintbench"]
        self.assertEqual(hint_source["records_total"], 10)
        self.assertEqual(hint_source["records_converted"], 9)
        self.assertEqual(hint_source["records_source_invalid"], 1)
        self.assertEqual(
            hint_source["invalid_records"][0]["code"], "dangling_source_step_reference"
        )
        self.assertEqual(report["acceptance"]["selected"], 12)
        self.assertEqual(report["acceptance"]["verified"], 12)

    def test_verify_reports_event_types_and_traceability(self) -> None:
        manifest = self.build()
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.dataset_root
        )
        for source in report["sources"]:
            self.assertGreater(source["events_total"], 0)
            self.assertTrue(source["event_types"])
            self.assertEqual(source["traceability_failures"], [])
            self.assertEqual(source["label_failures"], [])
            self.assertEqual(source["schema_failures"], [])

    def test_downstream_probe_runs_real_pipeline_on_both_sources(self) -> None:
        manifest = self.build()
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.dataset_root
        )
        downstream = report["downstream"]
        self.assertEqual(downstream["dataset_name_mentions"], [])
        self.assertTrue(downstream["shared_adapter_interface"])
        self.assertEqual(
            sorted(downstream["pipeline_probes"]), ["toy_atbench", "toy_hintbench"]
        )
        for probe in downstream["pipeline_probes"].values():
            self.assertTrue(probe["ok"], probe)
            self.assertIn("trajectory", probe["payload_shape"])
            self.assertGreater(probe["page_bytes"], 0)

    def test_verify_detects_acceptance_sample_drift(self) -> None:
        manifest = self.build()
        manifest["samples"][0]["record_sha256"] = "0" * 64
        reseal(manifest)
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.dataset_root
        )
        self.assertEqual(report["status"], "fail")
        codes = {failure["code"] for failure in report["failures"]}
        self.assertIn("record_sha256_changed", codes)
        self.assertEqual(report["acceptance"]["verified"], 11)
    def test_verify_detects_unrecorded_source_invalid_record(self) -> None:
        manifest = self.build()
        manifest["excluded_records"] = []
        for source in manifest["sources"]:
            if source["dataset_id"] == "toy_hintbench":
                source["records_valid"] = 10
                source["records_invalid"] = 0
        reseal(manifest)
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.dataset_root
        )
        self.assertEqual(report["status"], "fail")
        codes = {failure["code"] for failure in report["failures"]}
        self.assertIn("unexpected_invalid_record", codes)

    def test_verify_detects_missing_dataset(self) -> None:
        manifest = self.build()
        report = acceptance.verify_manifest(
            manifest=manifest, dataset_root=self.root / "absent"
        )
        self.assertEqual(report["status"], "fail")
        checks = {check["id"]: check["status"] for check in report["checks"]}
        self.assertEqual(checks["files_verified"], "fail")
        self.assertEqual(checks["acceptance_samples_complete"], "fail")
        self.assertTrue(report["failures"])

    def test_report_markdown_is_generated(self) -> None:
        manifest = self.build()
        out_json = self.root / "reports" / "h1.json"
        out_md = self.root / "reports" / "h1.md"
        report = acceptance.write_report(
            manifest=manifest,
            dataset_root=self.dataset_root,
            out_json=out_json,
            out_markdown=out_md,
        )
        self.assertEqual(report["status"], "pass")
        self.assertTrue(out_json.is_file())
        text = out_md.read_text(encoding="utf-8")
        for identifier in H1_CHECK_IDS:
            self.assertIn(identifier, text)
        self.assertIn("H1 数据契约报告", text)
        self.assertIn("不覆盖 H2", text)


def real_payload() -> dict:
    record = read_json(ATBENCH_FIXTURE)["records"][0]  # type: ignore[index]
    trajectories = atbench.normalize_record(
        record,
        record_index=0,
        dataset_id="toy_atbench",
        dataset_version="toy/v1",
        input_sha256="0" * 64,
        record_json_path="",
        record_sha256="1" * 64,
        sample_id="atbench-toy",
    )
    return json.loads(json.dumps(trajectories[0].to_dict(), ensure_ascii=False))


class TrajectorySchemaTest(unittest.TestCase):
    def test_real_trajectory_passes(self) -> None:
        self.assertEqual(validate_trajectory(real_payload()), [])

    def test_duplicate_event_id_is_rejected(self) -> None:
        payload = real_payload()
        payload["events"][1]["event_id"] = payload["events"][0]["event_id"]
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("events[1].event_id", paths)

    def test_sequence_gap_is_rejected(self) -> None:
        payload = real_payload()
        payload["events"][1]["sequence"] = 9
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("events[1].sequence", paths)

    def test_unknown_event_type_is_rejected(self) -> None:
        payload = real_payload()
        payload["events"][0]["type"] = "telepathy"
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("events[0].type", paths)

    def test_event_locator_must_match_trajectory_source(self) -> None:
        payload = real_payload()
        payload["events"][0]["source"]["dataset_id"] = "somewhere-else"
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("events[0].source.dataset_id", paths)

    def test_located_label_must_resolve_to_events(self) -> None:
        record = read_json(HINTBENCH_FIXTURE)["records"][1]  # type: ignore[index]
        trajectories = hintbench.normalize_record(
            record,
            record_index=1,
            dataset_id="toy_hintbench",
            dataset_version="toy/v1",
            input_sha256="0" * 64,
            record_json_path="",
            record_sha256="1" * 64,
            sample_id="toy-hint",
        )
        payload = trajectories[0].to_dict()
        self.assertIn("source_step", {label["scope"] for label in payload["source_labels"]})
        payload["source_labels"][1]["event_ids"] = []
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("source_labels[1].event_ids", paths)

    def test_trajectory_scope_label_must_stay_unlocated(self) -> None:
        payload = real_payload()
        located = [label for label in payload["source_labels"] if label["located"]]
        self.assertFalse(located)
        payload["source_labels"][0]["event_ids"] = [payload["events"][0]["event_id"]]
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("source_labels[0].event_ids", paths)

    def test_unknown_top_level_field_is_rejected(self) -> None:
        payload = real_payload()
        payload["surprise"] = True
        paths = {issue["path"] for issue in validate_trajectory(payload)}
        self.assertIn("surprise", paths)


class JsonLocatorTest(unittest.TestCase):
    def test_resolves_nested_path(self) -> None:
        record = {"contents": [[{"action": {"name": "x"}}]]}
        found, value = locate_json_path(record, "contents[0][0].action")
        self.assertTrue(found)
        self.assertEqual(value, {"name": "x"})

    def test_missing_path_reports_absent(self) -> None:
        self.assertFalse(locate_json_path({"a": [1]}, "a[3]")[0])
        self.assertFalse(locate_json_path({"a": [1]}, "b")[0])
        self.assertFalse(locate_json_path({"a": "text"}, "a[0]")[0])

    def test_empty_path_returns_record(self) -> None:
        self.assertEqual(locate_json_path({"a": 1}, ""), (True, {"a": 1}))


class SourceIntegrityTest(unittest.TestCase):
    def test_smoke_records_have_no_defects(self) -> None:
        for record in read_json(HINTBENCH_FIXTURE)["records"]:  # type: ignore[index]
            self.assertEqual(hintbench.inspect_source(record), [])

    def test_dangling_reference_is_a_source_defect(self) -> None:
        record = copy.deepcopy(read_json(HINTBENCH_FIXTURE)["records"][1])  # type: ignore[index]
        record["risk_labels"][0]["risk_origin_step"] = 999
        defects = hintbench.inspect_source(record)
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["code"], "dangling_source_step_reference")
        self.assertEqual(defects[0]["stage"], "source_integrity")
        self.assertEqual(defects[0]["path"], "risk_labels[0].risk_origin_step")

    def test_missing_reference_is_a_source_defect(self) -> None:
        record = copy.deepcopy(read_json(HINTBENCH_FIXTURE)["records"][1])  # type: ignore[index]
        record["risk_labels"][0].pop("risk_origin_step", None)
        record["risk_labels"][0].pop("step_id", None)
        defects = hintbench.inspect_source(record)
        self.assertEqual(defects[0]["code"], "missing_source_step_reference")

    def test_sources_without_cross_references_have_no_defects(self) -> None:
        atbench_record = read_json(ATBENCH_FIXTURE)["records"][0]  # type: ignore[index]
        self.assertEqual(atbench.inspect_source(atbench_record), [])
        self.assertEqual(openai_messages.inspect_source({"sample_id": "x", "messages": []}), [])

    def test_expected_label_counts_match_adapters(self) -> None:
        atbench_record = read_json(ATBENCH_FIXTURE)["records"][0]  # type: ignore[index]
        hint_record = read_json(HINTBENCH_FIXTURE)["records"][1]  # type: ignore[index]
        self.assertEqual(atbench.expected_source_label_count(atbench_record), 1)
        self.assertEqual(
            hintbench.expected_source_label_count(hint_record),
            1 + len(hint_record["risk_labels"]),
        )
        self.assertEqual(openai_messages.expected_source_label_count({"messages": []}), 0)


class CommittedArtifactTest(unittest.TestCase):
    """已提交的冻结清单必须自洽；任何手工改动都会在 CI 里暴露。"""

    def test_committed_manifest_matches_frozen_policy(self) -> None:
        manifest = read_json(COMMITTED_MANIFEST)
        self.assertEqual(
            acceptance.validate_manifest(manifest, expected_id="acceptance-v1"), []
        )
        self.assertEqual(len(manifest["samples"]), 48)
        self.assertEqual(manifest["policy"]["seed"], 20261023)
        self.assertEqual(
            acceptance.content_sha256(manifest),
            manifest["integrity"]["content_sha256"],
        )

    def test_committed_manifest_quota_split(self) -> None:
        manifest = read_json(COMMITTED_MANIFEST)
        counts: dict[tuple[str, str], int] = {}
        for sample in manifest["samples"]:
            key = (sample["dataset_id"], sample["stratum"])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts[("atbench", "safe")], 8)
        self.assertEqual(counts[("atbench", "unsafe")], 16)
        self.assertEqual(counts[("hintbench", "safe")], 6)
        self.assertEqual(counts[("hintbench", "risky")], 18)

    def test_committed_report_belongs_to_committed_manifest(self) -> None:
        manifest = read_json(COMMITTED_MANIFEST)
        report = read_json(COMMITTED_REPORT)
        self.assertEqual(report["manifest_id"], "acceptance-v1")
        self.assertEqual(
            report["manifest_content_sha256"], manifest["integrity"]["content_sha256"]
        )
        self.assertEqual([check["id"] for check in report["checks"]], list(H1_CHECK_IDS))
        self.assertEqual(report["status"], "pass")

    def test_committed_exclusions_are_source_invalid(self) -> None:
        manifest = read_json(COMMITTED_MANIFEST)
        self.assertEqual(len(manifest["excluded_records"]), 3)
        for item in manifest["excluded_records"]:
            self.assertEqual(item["dataset_id"], "hintbench")
            self.assertEqual(item["stage"], "source_integrity")
            self.assertEqual(item["code"], "dangling_source_step_reference")
            self.assertTrue(item["reason"])


class AcceptanceCliTest(unittest.TestCase):
    def test_missing_action_is_rejected(self) -> None:
        completed = json_cli("acceptance")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stderr)["code"], "invalid_arguments")

    def test_build_and_verify_require_arguments(self) -> None:
        build = json_cli("acceptance", "build")
        self.assertEqual(build.returncode, 2)
        self.assertEqual(json.loads(build.stderr)["code"], "missing_output")
        verify = json_cli("acceptance", "verify", "--out", "x.json")
        self.assertEqual(verify.returncode, 2)
        self.assertEqual(json.loads(verify.stderr)["code"], "missing_manifest")

    def test_unknown_manifest_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = json_cli(
                "acceptance",
                "verify",
                "--manifest",
                Path(tmp) / "absent.json",
                "--out",
                Path(tmp) / "report.json",
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stderr)["code"], "manifest_not_found")

    def test_verify_without_datasets_reports_failure_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            completed = json_cli(
                "acceptance",
                "verify",
                "--manifest",
                COMMITTED_MANIFEST,
                "--dataset-root",
                Path(tmp) / "no-datasets",
                "--out",
                out,
            )
            self.assertTrue(out.is_file(), completed.stderr)
            report = read_json(out)
        self.assertEqual(completed.returncode, 4, completed.stdout + completed.stderr)
        self.assertEqual(report["status"], "fail")
        checks = {check["id"]: check["status"] for check in report["checks"]}
        self.assertEqual(checks["files_verified"], "fail")
        self.assertEqual(json.loads(completed.stdout)["status"], "fail")


if __name__ == "__main__":
    unittest.main()
