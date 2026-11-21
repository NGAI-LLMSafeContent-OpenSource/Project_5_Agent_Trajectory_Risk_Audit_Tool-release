# SPDX-License-Identifier: Apache-2.0
"""Issue #16 验收测试：80 条 Development Corpus 的抽样、冻结与隔离。

测试优先通过公开的构建/校验入口与落盘字节断言外部行为；只有「用小数据跑完整
行为」这一处替换了固定来源注册表与冻结策略，不复制抽样算法内部结构。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from trajectory_audit import development, pinned
from trajectory_audit.audit_input import validate_audit_input
from trajectory_audit.errors import RunError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "fixtures" / "smoke"
ATBENCH_FIXTURE = FIXTURES / "atbench_smoke.json"
HINTBENCH_FIXTURE = FIXTURES / "hintbench_smoke.json"
COMMITTED_CORPUS = PROJECT_ROOT / "corpus" / "topic4-provider-corpus-v2"
COMMITTED_DEVELOPMENT = COMMITTED_CORPUS / "development"
COMMITTED_ACCEPTANCE = PROJECT_ROOT / "acceptance" / "acceptance-v1.json"
DATASET_ROOT = PROJECT_ROOT / ".cache" / "datasets"

TOY_VERSION = "toy-corpus"
TOY_ACCEPTANCE_ID = "acceptance-toy"


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


def toy_atbench_records() -> list[dict[str, Any]]:
    template = read_json(ATBENCH_FIXTURE)["records"][0]
    records: list[dict[str, Any]] = []
    for index, mode in enumerate(("benign", "benign", "benign", "benign", "benign", "benign")):
        record = copy.deepcopy(template)
        record["id"] = f"toy-safe-{index}"
        record["label"] = 0
        record["risk_source"] = "benign"
        record["failure_mode"] = mode
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


def toy_hintbench_records() -> list[dict[str, Any]]:
    fixture = read_json(HINTBENCH_FIXTURE)["records"]
    safe_template = fixture[0]
    risky_template = fixture[1]
    records: list[dict[str, Any]] = []
    for index, env in enumerate(("alpha", "alpha", "beta", "gamma")):
        record = copy.deepcopy(safe_template)
        record["task_id"] = f"toy-hint-safe-{index}"
        record["env"] = env
        records.append(record)
    for index, category in enumerate(
        ("State Constraint", "State Constraint", "Goal Constraint", "Factual Constraint", "Capability Constraint")
    ):
        record = copy.deepcopy(risky_template)
        record["task_id"] = f"toy-hint-risky-{index}"
        for label in record["risk_labels"]:
            label["category"] = category
        records.append(record)
    return records


def toy_registry(root: Path) -> dict[str, dict[str, Any]]:
    payloads = {
        "toy_atbench": ("atbench", "toy_atbench.json", toy_atbench_records()),
        "toy_hintbench": ("hintbench", "toy_hintbench.json", toy_hintbench_records()),
    }
    registry: dict[str, dict[str, Any]] = {}
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
            "record_identity": "toy record index 与来源 id",
        }
    return registry


def toy_policy() -> dict[str, Any]:
    return {
        "seed": 7,
        "algorithm": "coverage-first-distribution-fill/1",
        "selection_basis": "source labels and source metadata only",
        "isolated_from": TOY_ACCEPTANCE_ID,
        "sources": {
            "toy_atbench": {
                "quotas": {"safe": 3, "unsafe": 4},
                "risk_layers": {
                    "safe": {"field": "label", "true_value": 0},
                    "unsafe": {"field": "label", "true_value": 1},
                },
                "distribution": {"safe": "failure_mode", "unsafe": "risk_source"},
                "coverage": [
                    {
                        "id": "toy_atbench.benign",
                        "strata": ["safe"],
                        "kind": "field",
                        "field": "failure_mode",
                        "values": ["benign"],
                        "detail": "普通安全样本",
                    },
                    {
                        "id": "toy_atbench.unsafe_risk_source",
                        "strata": ["unsafe"],
                        "kind": "field",
                        "field": "risk_source",
                        "values": [
                            "corrupted_tool_feedback",
                            "direct_prompt_injection",
                            "tool_description_injection",
                        ],
                        "detail": "风险来源覆盖",
                    },
                ],
            },
            "toy_hintbench": {
                "quotas": {"safe": 2, "risky": 3},
                "risk_layers": {
                    "safe": {"field": "is_risky", "true_value": False},
                    "risky": {"field": "is_risky", "true_value": True},
                },
                "distribution": {"safe": "env", "risky": "primary_category"},
                "coverage": [
                    {
                        "id": "toy_hintbench.constraint",
                        "strata": ["risky"],
                        "kind": "categories",
                        "values": [
                            "Capability Constraint",
                            "Factual Constraint",
                            "Goal Constraint",
                        ],
                        "detail": "约束类别覆盖",
                    },
                    {
                        "id": "toy_hintbench.safe_env",
                        "strata": ["safe"],
                        "kind": "field",
                        "field": "env",
                        "values": ["alpha", "beta"],
                        "detail": "代表性 safe 环境",
                    },
                ],
            },
        },
    }


def toy_acceptance_manifest(claimed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "acceptance-manifest/1",
        "manifest_id": TOY_ACCEPTANCE_ID,
        "frozen": True,
        "samples": claimed or [],
        "integrity": {"digest_version": "canonical-json-sha256/1", "content_sha256": "f" * 64},
    }


class ToyCorpusCase(unittest.TestCase):
    """用玩具来源跑完整构建流程，避免依赖 30 MB 固定数据。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.datasets = self.root / "datasets"
        self.datasets.mkdir()
        self.registry = toy_registry(self.datasets)
        self.policy = toy_policy()
        self.acceptance = toy_acceptance_manifest()
        patcher_registry = mock.patch.dict(
            pinned.BY_DATASET, self.registry, clear=True
        )
        patcher_policies = mock.patch.dict(
            pinned.DEVELOPMENT_CORPUS_POLICIES, {TOY_VERSION: self.policy}, clear=True
        )
        patcher_registry.start()
        patcher_policies.start()
        self.addCleanup(patcher_registry.stop)
        self.addCleanup(patcher_policies.stop)

    def build(self, out: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest, artifacts = development.build_development_corpus(
            version=TOY_VERSION,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
        )
        if out is not None:
            development.write_development_corpus(
                version=TOY_VERSION,
                dataset_root=self.datasets,
                acceptance_manifest=self.acceptance,
                out_dir=out,
            )
        return manifest, artifacts


class DevelopmentCorpusBuildTest(ToyCorpusCase):
    def test_build_matches_declared_quotas_and_schema(self) -> None:
        manifest, artifacts = self.build()
        batch = artifacts[development.INPUT_FILENAME]
        self.assertEqual(validate_audit_input(batch), [])
        self.assertEqual(len(batch["items"]), 12)
        self.assertEqual(len(manifest["samples"]), 12)

        counts: dict[tuple[str, str], int] = {}
        for sample in manifest["samples"]:
            key = (sample["dataset_id"], sample["stratum"])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts[("toy_atbench", "safe")], 3)
        self.assertEqual(counts[("toy_atbench", "unsafe")], 4)
        self.assertEqual(counts[("toy_hintbench", "safe")], 2)
        self.assertEqual(counts[("toy_hintbench", "risky")], 3)

        for item in batch["items"]:
            self.assertIsNone(item["policy"])
            self.assertRegex(item["audit_input_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(item["trajectory"]["schema_version"], "audit-trajectory/1")

    def test_every_declared_coverage_value_is_covered(self) -> None:
        _, artifacts = self.build()
        coverage = artifacts[development.COVERAGE_FILENAME]
        self.assertEqual(coverage["schema_version"], development.COVERAGE_SCHEMA)
        for block in coverage["dimensions"]:
            self.assertEqual(block["uncovered"], [], block["id"])
            self.assertTrue(block["covered"], block["id"])

    def test_build_is_reproducible(self) -> None:
        first, artifacts = self.build()
        second, artifacts_again = self.build()
        self.assertEqual(
            first["integrity"]["content_sha256"], second["integrity"]["content_sha256"]
        )
        self.assertEqual(artifacts[development.INPUT_FILENAME], artifacts_again[development.INPUT_FILENAME])

    def test_coverage_gap_fails_instead_of_incomplete_corpus(self) -> None:
        self.policy["sources"]["toy_atbench"]["coverage"][0]["values"] = ["benign", "does_not_exist"]
        with self.assertRaises(RunError) as context:
            self.build()
        self.assertEqual(context.exception.code, "corpus_coverage_gap")
        self.assertEqual(context.exception.exit_code, 2)
        missing = context.exception.details["missing"]
        self.assertEqual(missing[0]["value"], "does_not_exist")

    def test_quota_beyond_candidates_fails(self) -> None:
        self.policy["sources"]["toy_hintbench"]["quotas"]["safe"] = 99
        with self.assertRaises(RunError) as context:
            self.build()
        self.assertEqual(context.exception.code, "corpus_quota_unmet")


class AcceptanceIsolationTest(ToyCorpusCase):
    def test_acceptance_identities_are_excluded(self) -> None:
        claimed = [
            {"sample_id": "toy-unsafe-0", "dataset_id": "toy_atbench", "record_index": 6},
            {"sample_id": "toy-safe-0", "dataset_id": "toy_atbench", "record_index": 0},
        ]
        self.acceptance = toy_acceptance_manifest(claimed)
        manifest, artifacts = self.build()
        selected = {sample["sample_id"] for sample in manifest["samples"]}
        self.assertNotIn("toy-unsafe-0", selected)
        self.assertNotIn("toy-safe-0", selected)

        isolation = manifest["isolation"]
        self.assertEqual(isolation["isolated_from"], TOY_ACCEPTANCE_ID)
        self.assertEqual(isolation["sample_id_intersection"], [])
        self.assertEqual(isolation["record_intersection"], [])
        self.assertEqual(isolation["excluded_candidates"]["records"], 2)
        self.assertTrue(isolation["evidence_sha256"])

        reference = artifacts[development.REFERENCE_FILENAME]
        reference_ids = {sample["sample_id"] for sample in reference["samples"]}
        self.assertEqual(reference_ids, selected)

    def test_verify_recomputes_zero_overlap(self) -> None:
        claimed = [
            {"sample_id": "toy-unsafe-0", "dataset_id": "toy_atbench", "record_index": 6}
        ]
        self.acceptance = toy_acceptance_manifest(claimed)
        out = self.root / "corpus"
        self.build(out)
        report = development.verify_development_corpus(
            root=out,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
        )
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["reproducible"], "verified")

        tampered = copy.deepcopy(self.acceptance)
        tampered["samples"] = claimed + [
            {"sample_id": "toy-safe-1", "dataset_id": "toy_atbench", "record_index": 1}
        ]
        tampered["integrity"]["content_sha256"] = "e" * 64
        bad = development.verify_development_corpus(
            root=out, dataset_root=None, acceptance_manifest=tampered
        )
        self.assertEqual(bad["status"], "fail")


class SourceLabelLeakageTest(ToyCorpusCase):
    def test_source_labels_stay_out_of_the_formal_input(self) -> None:
        canaries = {
            "reason": "CANARY-CORPUS-REASON-1a2b",
            "label_note": "CANARY-CORPUS-LABEL-1a2b",
        }
        records = read_json(self.datasets / "toy_atbench.json")
        for record in records:
            record["reason"] = canaries["reason"]
            record["real_world_harm"] = canaries["label_note"]
        write_json(self.datasets / "toy_atbench.json", records)
        raw = (self.datasets / "toy_atbench.json").read_bytes()
        self.registry["toy_atbench"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.registry["toy_atbench"]["bytes"] = len(raw)

        out = self.root / "corpus"
        self.build(out)
        written = (out / development.INPUT_FILENAME).read_text(encoding="utf-8")
        for canary in canaries.values():
            self.assertNotIn(canary, written)
        self.assertNotIn("risk_source", written)
        self.assertNotIn('"label"', written)

        reference = (out / development.REFERENCE_FILENAME).read_text(encoding="utf-8")
        reference_payload = json.loads(reference)
        self.assertFalse(reference_payload["is_project_truth"])
        self.assertTrue(
            any("risk_source" in sample["source_label"] for sample in reference_payload["samples"])
        )


class FreezeSemanticsTest(ToyCorpusCase):
    def test_second_build_reports_unchanged(self) -> None:
        out = self.root / "corpus"
        self.build(out)
        first = (out / development.MANIFEST_FILENAME).read_bytes()
        status = development.write_development_corpus(
            version=TOY_VERSION,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
            out_dir=out,
        )
        self.assertEqual(status["status"], "unchanged")
        self.assertEqual(first, (out / development.MANIFEST_FILENAME).read_bytes())

    def test_changed_selection_refuses_to_overwrite(self) -> None:
        out = self.root / "corpus"
        self.build(out)
        original = (out / development.MANIFEST_FILENAME).read_bytes()

        self.policy["seed"] = 999999
        with self.assertRaises(RunError) as context:
            development.write_development_corpus(
                version=TOY_VERSION,
                dataset_root=self.datasets,
                acceptance_manifest=self.acceptance,
                out_dir=out,
            )
        self.assertEqual(context.exception.code, "corpus_version_conflict")
        self.assertEqual(context.exception.exit_code, 3)
        self.assertEqual(original, (out / development.MANIFEST_FILENAME).read_bytes())

    def test_tampered_artifact_fails_verification(self) -> None:
        out = self.root / "corpus"
        self.build(out)
        target = out / development.INPUT_FILENAME
        payload = read_json(target)
        payload["items"][0]["trajectory"]["events"][0]["content"]["text"] = "tampered"
        write_json(target, payload)
        report = development.verify_development_corpus(
            root=out, acceptance_manifest=self.acceptance
        )
        self.assertEqual(report["status"], "fail")
        paths = {issue["path"] for issue in report["issues"]}
        self.assertTrue(any(path.startswith("artifacts[") for path in paths), paths)

    def test_unknown_version_is_rejected(self) -> None:
        with self.assertRaises(RunError) as context:
            development.build_development_corpus(
                version="does-not-exist",
                dataset_root=self.datasets,
                acceptance_manifest=self.acceptance,
            )
        self.assertEqual(context.exception.code, "unknown_corpus_version")


class CommittedCorpusTest(unittest.TestCase):
    """已提交的 80 条交付物在不读数据缓存的前提下也必须自洽。"""

    def setUp(self) -> None:
        if not COMMITTED_DEVELOPMENT.is_dir():
            self.skipTest("尚未生成 topic4-provider-corpus-v2")
        self.manifest = read_json(
            COMMITTED_DEVELOPMENT / development.MANIFEST_FILENAME
        )
        self.batch = read_json(COMMITTED_DEVELOPMENT / development.INPUT_FILENAME)

    def test_counts_and_schemas(self) -> None:
        self.assertEqual(self.manifest["schema_version"], development.CORPUS_MANIFEST_SCHEMA)
        self.assertTrue(self.manifest["frozen"])
        self.assertEqual(len(self.batch["items"]), 80)
        self.assertEqual(
            self.manifest["input_schema_version"], "topic4-audit-input/1"
        )
        self.assertEqual(
            self.manifest["trajectory_schema_version"], "audit-trajectory/1"
        )
        self.assertEqual(validate_audit_input(self.batch), [])

    def test_quota_split(self) -> None:
        counts: dict[tuple[str, str], int] = {}
        for sample in self.manifest["samples"]:
            key = (sample["dataset_id"], sample["stratum"])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(
            counts,
            {
                ("atbench", "safe"): 16,
                ("atbench", "unsafe"): 32,
                ("hintbench", "safe"): 8,
                ("hintbench", "risky"): 24,
            },
        )

    def test_every_policy_is_explicitly_null(self) -> None:
        for item in self.batch["items"]:
            self.assertIsNone(item["policy"])

    def test_zero_overlap_with_acceptance_v1(self) -> None:
        acceptance = read_json(COMMITTED_ACCEPTANCE)
        acceptance_samples = {sample["sample_id"] for sample in acceptance["samples"]}
        acceptance_records = {
            (sample["dataset_id"], sample["record_index"])
            for sample in acceptance["samples"]
        }
        development_samples = {sample["sample_id"] for sample in self.manifest["samples"]}
        development_records = {
            (sample["dataset_id"], sample["record_index"])
            for sample in self.manifest["samples"]
        }
        self.assertEqual(development_samples & acceptance_samples, set())
        self.assertEqual(development_records & acceptance_records, set())
        self.assertEqual(self.manifest["isolation"]["sample_id_intersection"], [])
        self.assertEqual(self.manifest["isolation"]["record_intersection"], [])

    def test_coverage_matrix_has_no_gaps(self) -> None:
        coverage = read_json(COMMITTED_DEVELOPMENT / development.COVERAGE_FILENAME)
        for block in coverage["dimensions"]:
            self.assertEqual(block["uncovered"], [], block["id"])

    def test_no_source_labels_in_formal_input(self) -> None:
        """禁止的是结构层字段：事件 content 与工具 parameters 的键名属于来源载荷。"""
        structural: set[str] = set()
        structural.update(self.batch)
        for item in self.batch["items"]:
            structural.update(item)
            trajectory = item["trajectory"]
            structural.update(trajectory)
            structural.update(trajectory["coverage"])
            for event in trajectory["events"]:
                structural.update(event)
            for definition in trajectory["tool_definitions"]:
                structural.update(definition)
                if isinstance(definition.get("function"), dict):
                    structural.update(definition["function"])
        for token in (
            "source_labels",
            "risk_source",
            "failure_mode",
            "is_risky",
            "requested_taxonomy",
            "detected",
            "reference",
            "raw",
        ):
            self.assertNotIn(token, structural)

    def test_verify_passes_without_dataset_cache(self) -> None:
        report = development.verify_development_corpus(
            root=COMMITTED_DEVELOPMENT,
            acceptance_manifest=read_json(COMMITTED_ACCEPTANCE),
        )
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["reproducible"], "skipped")

    def test_readme_documents_required_contracts(self) -> None:
        text = (COMMITTED_DEVELOPMENT / development.README_FILENAME).read_text(
            encoding="utf-8"
        )
        for token in (
            "topic4-audit run",
            "audit_input_sha256",
            "policy=null",
            "requested_taxonomy",
            "self-check",
            "不是项目真值",
        ):
            self.assertIn(token, text)


@unittest.skipUnless(
    (DATASET_ROOT / "atbench.json").is_file() and (DATASET_ROOT / "hintbench.json").is_file(),
    "需要固定数据缓存",
)
class FullRebuildTest(unittest.TestCase):
    def test_full_rebuild_matches_committed_artifacts(self) -> None:
        if not COMMITTED_DEVELOPMENT.is_dir():
            self.skipTest("尚未生成 topic4-provider-corpus-v2")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "corpus"
            shutil.copytree(COMMITTED_DEVELOPMENT, target)
            acceptance_manifest = read_json(COMMITTED_ACCEPTANCE)
            status = development.write_development_corpus(
                version=development.DEFAULT_CORPUS_VERSION,
                dataset_root=DATASET_ROOT,
                acceptance_manifest=acceptance_manifest,
                out_dir=target,
            )
            self.assertEqual(status["status"], "unchanged", status)
            for name in (
                development.INPUT_FILENAME,
                development.COVERAGE_FILENAME,
                development.REFERENCE_FILENAME,
                development.README_FILENAME,
                development.MANIFEST_FILENAME,
            ):
                self.assertEqual(
                    (COMMITTED_DEVELOPMENT / name).read_bytes(),
                    (target / name).read_bytes(),
                    name,
                )


class DevelopmentCliTest(ToyCorpusCase):
    def test_cli_reports_missing_arguments(self) -> None:
        build = invoke_cli("corpus", "development", "build")
        self.assertEqual(build.returncode, 2)
        self.assertEqual(json.loads(build.stderr)["code"], "missing_output")
        verify = invoke_cli("corpus", "development", "verify")
        self.assertEqual(verify.returncode, 2)
        self.assertEqual(json.loads(verify.stderr)["code"], "missing_root")

    def test_cli_rejects_unknown_version(self) -> None:
        completed = invoke_cli(
            "corpus",
            "development",
            "build",
            "--version",
            "nope",
            "--out",
            self.root / "out",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stderr)["code"], "unknown_corpus_version")


class CommittedCorpusCliTest(unittest.TestCase):
    def test_cli_verify_passes(self) -> None:
        if not COMMITTED_DEVELOPMENT.is_dir():
            self.skipTest("尚未生成 topic4-provider-corpus-v2")
        completed = invoke_cli(
            "corpus", "development", "verify", "--root", COMMITTED_DEVELOPMENT
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
