# SPDX-License-Identifier: Apache-2.0
"""Issue #17 验收测试：Blind Acceptance Batch 与整版 Provider Corpus。

玩具来源复用 #16 的 fixture 构造，不复制来源注册表与冻结策略；断言优先走
公开的构建/校验入口与落盘字节，而不是内部实现细节。
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_trajectory_audit_development_corpus import (  # noqa: E402
    COMMITTED_ACCEPTANCE,
    COMMITTED_CORPUS,
    PROJECT_ROOT,
    TOY_ACCEPTANCE_ID,
    TOY_VERSION,
    ToyCorpusCase,
    invoke_cli,
    read_json,
    toy_acceptance_manifest,
    write_json,
)

from trajectory_audit import blind_acceptance, development, edition  # noqa: E402
from trajectory_audit.adapters import get_adapter  # noqa: E402
from trajectory_audit.audit_input import validate_audit_input  # noqa: E402
from trajectory_audit.errors import RunError  # noqa: E402
from trajectory_audit.model import sha256_json  # noqa: E402

COMMITTED_DEVELOPMENT = COMMITTED_CORPUS / "development"
COMMITTED_BLIND = COMMITTED_CORPUS / blind_acceptance.SUBSET_DIR


def claim(self: Any, dataset_id: str, record_index: int, **overrides: Any) -> dict[str, Any]:
    """按固定数据构造一条验收样本声明（身份与记录摘要都与来源一致）。"""
    entry = self.registry[dataset_id]
    records = read_json(self.datasets / entry["cache_name"])
    record = records[record_index]
    adapter = get_adapter(entry["adapter_id"])
    sample_id, _ = adapter.sample_identity(record)
    payload = {
        "sample_id": sample_id or f"record-{record_index:04d}",
        "dataset_id": dataset_id,
        "record_index": record_index,
        "record_sha256": sha256_json(record),
        "stratum": "unsafe",
        "substratum": "toy",
        "source_label": {"kind": "source_label", "dataset_id": dataset_id},
    }
    payload.update(overrides)
    return payload


class BlindAcceptanceCase(ToyCorpusCase):
    """在玩具来源上先生成开发子集，再为盲测批次挑选未入选的记录。"""

    def setUp(self) -> None:
        super().setUp()
        self.development_manifest, _ = self.build()
        self.taken = {
            (sample["dataset_id"], sample["record_index"])
            for sample in self.development_manifest["samples"]
        }

    def available(self, dataset_id: str, limit: int) -> list[dict[str, Any]]:
        total = len(read_json(self.datasets / self.registry[dataset_id]["cache_name"]))
        picked = [
            claim(self, dataset_id, index)
            for index in range(total)
            if (dataset_id, index) not in self.taken
        ][:limit]
        self.assertTrue(picked, f"{dataset_id} 没有可用的验收记录")
        return picked

    def blind(self, claims: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        self.acceptance = toy_acceptance_manifest(claims)
        return blind_acceptance.build_blind_acceptance(
            version=TOY_VERSION,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
            development_manifest=self.development_manifest,
        )

    def write_blind(self, claims: list[dict[str, Any]], out: Path) -> dict[str, Any]:
        self.acceptance = toy_acceptance_manifest(claims)
        return blind_acceptance.write_blind_acceptance(
            version=TOY_VERSION,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
            development_manifest=self.development_manifest,
            out_dir=out,
        )


class BlindAcceptanceBuildTest(BlindAcceptanceCase):
    def test_build_matches_claimed_identities_and_schema(self) -> None:
        claims = self.available("toy_atbench", 2) + self.available("toy_hintbench", 2)
        manifest, artifacts = self.blind(claims)
        batch = artifacts[blind_acceptance.INPUT_FILENAME]

        self.assertEqual(validate_audit_input(batch), [])
        self.assertEqual(len(batch["items"]), len(claims))
        self.assertEqual(len(manifest["samples"]), len(claims))
        self.assertEqual(manifest["subset"], blind_acceptance.SUBSET_ID)
        self.assertTrue(manifest["frozen"])
        self.assertEqual(
            {sample["source_sample_id"] for sample in manifest["samples"]},
            {entry["sample_id"] for entry in claims},
        )
        self.assertEqual(
            {(sample["dataset_id"], sample["record_index"]) for sample in manifest["samples"]},
            {(entry["dataset_id"], entry["record_index"]) for entry in claims},
        )

    def test_provider_identity_is_opaque_and_mapping_stays_in_manifest(self) -> None:
        claims = self.available("toy_atbench", 2)
        manifest, artifacts = self.blind(claims)
        batch = artifacts[blind_acceptance.INPUT_FILENAME]
        for item in batch["items"]:
            self.assertRegex(item["sample_id"], r"^sample-[0-9a-f]{16}$")
            self.assertIsNone(item["policy"])
        raw = json.dumps(batch, ensure_ascii=False)
        for entry in claims:
            self.assertNotIn(entry["sample_id"], raw)
        for sample in manifest["samples"]:
            self.assertTrue(sample["source_sample_id"])

    def test_relations_are_zero_overlap_and_equal_to_acceptance(self) -> None:
        claims = self.available("toy_atbench", 2) + self.available("toy_hintbench", 1)
        manifest, _ = self.blind(claims)
        equality = manifest["relations"]["equals_acceptance"]
        self.assertTrue(equality["sample_identity_equal"])
        self.assertTrue(equality["record_identity_equal"])
        self.assertEqual(equality["samples"], len(claims))
        disjoint = manifest["relations"]["disjoint_from_development"]
        self.assertEqual(disjoint["sample_id_intersection"], [])
        self.assertEqual(disjoint["record_intersection"], [])
        self.assertTrue(disjoint["evidence_sha256"])

    def test_claim_overlapping_development_is_rejected(self) -> None:
        taken = self.development_manifest["samples"][0]
        overlapping = [claim(self, taken["dataset_id"], taken["record_index"])]
        self.acceptance = toy_acceptance_manifest(overlapping)
        with self.assertRaises(RunError) as context:
            blind_acceptance.build_blind_acceptance(
                version=TOY_VERSION,
                dataset_root=self.datasets,
                acceptance_manifest=self.acceptance,
                development_manifest=self.development_manifest,
            )
        self.assertEqual(context.exception.code, "corpus_overlap_detected")

    def test_record_digest_drift_is_rejected(self) -> None:
        claims = self.available("toy_atbench", 1)
        claims[0]["record_sha256"] = "0" * 64
        with self.assertRaises(RunError) as context:
            self.blind(claims)
        self.assertEqual(context.exception.code, "corpus_source_invalid")

    def test_source_identity_mismatch_is_rejected(self) -> None:
        claims = self.available("toy_atbench", 1)
        claims[0]["sample_id"] = "not-the-source-identity"
        with self.assertRaises(RunError) as context:
            self.blind(claims)
        self.assertEqual(context.exception.code, "corpus_source_invalid")

    def test_source_labels_stay_out_of_the_formal_input(self) -> None:
        canary = "CANARY-BLIND-REASON-9f3c"
        records = read_json(self.datasets / "toy_atbench.json")
        for record in records:
            record["reason"] = canary
        write_json(self.datasets / "toy_atbench.json", records)
        raw = (self.datasets / "toy_atbench.json").read_bytes()
        self.registry["toy_atbench"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.registry["toy_atbench"]["bytes"] = len(raw)

        claims = self.available("toy_atbench", 2)
        manifest, artifacts = self.blind(claims)
        formal = json.dumps(artifacts[blind_acceptance.INPUT_FILENAME], ensure_ascii=False)
        self.assertNotIn(canary, formal)
        self.assertNotIn("risk_source", formal)
        reference = artifacts[blind_acceptance.REFERENCE_FILENAME]
        self.assertFalse(reference["is_project_truth"])
        self.assertTrue(
            any(
                "risk_source" in sample["source_label"]
                for sample in reference["samples"]
            )
        )
        self.assertEqual(manifest["usage"]["prompt_tuning_allowed"], False)


class BlindAcceptanceFreezeTest(BlindAcceptanceCase):
    def test_second_write_reports_unchanged(self) -> None:
        claims = self.available("toy_atbench", 2)
        out = self.root / "blind"
        self.write_blind(claims, out)
        first = (out / blind_acceptance.MANIFEST_FILENAME).read_bytes()
        status = self.write_blind(claims, out)
        self.assertEqual(status["status"], "unchanged")
        self.assertEqual(first, (out / blind_acceptance.MANIFEST_FILENAME).read_bytes())

    def test_changed_claims_refuse_to_overwrite(self) -> None:
        out = self.root / "blind"
        self.write_blind(self.available("toy_atbench", 2), out)
        original = (out / blind_acceptance.MANIFEST_FILENAME).read_bytes()
        with self.assertRaises(RunError) as context:
            self.write_blind(self.available("toy_atbench", 1), out)
        self.assertEqual(context.exception.code, "corpus_version_conflict")
        self.assertEqual(context.exception.exit_code, 3)
        self.assertEqual(original, (out / blind_acceptance.MANIFEST_FILENAME).read_bytes())

    def test_tampered_input_fails_verification(self) -> None:
        claims = self.available("toy_atbench", 2)
        out = self.root / "blind"
        self.write_blind(claims, out)
        target = out / blind_acceptance.INPUT_FILENAME
        payload = read_json(target)
        payload["items"][0]["trajectory"]["events"][0]["content"]["text"] = "tampered"
        write_json(target, payload)
        report = blind_acceptance.verify_blind_acceptance(
            root=out,
            acceptance_manifest=self.acceptance,
            development_manifest=self.development_manifest,
        )
        self.assertEqual(report["status"], "fail")
        paths = {issue["path"] for issue in report["issues"]}
        self.assertTrue(any(path.startswith("artifacts[") for path in paths), paths)

    def test_verify_passes_without_dataset_cache(self) -> None:
        claims = self.available("toy_atbench", 2)
        out = self.root / "blind"
        self.write_blind(claims, out)
        report = blind_acceptance.verify_blind_acceptance(
            root=out,
            acceptance_manifest=self.acceptance,
            development_manifest=self.development_manifest,
        )
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["reproducible"], "skipped")


class EditionBuildTest(BlindAcceptanceCase):
    def setUp(self) -> None:
        super().setUp()
        self.claims = self.available("toy_atbench", 2) + self.available("toy_hintbench", 2)
        self.acceptance = toy_acceptance_manifest(self.claims)

    def build_edition(self, out: Path) -> dict[str, Any]:
        return edition.write_edition(
            version=TOY_VERSION,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
            out_dir=out,
        )

    def test_layout_contains_both_subsets(self) -> None:
        out = self.root / "edition"
        result = self.build_edition(out)
        self.assertEqual(result["subsets"]["development"], 12)
        self.assertEqual(
            result["subsets"][blind_acceptance.SUBSET_ID], len(self.claims)
        )
        self.assertTrue((out / "manifest.json").is_file())
        self.assertTrue((out / "README.md").is_file())
        for name in (
            development.INPUT_FILENAME,
            development.COVERAGE_FILENAME,
            development.REFERENCE_FILENAME,
            development.MANIFEST_FILENAME,
        ):
            self.assertTrue((out / edition.DEVELOPMENT_DIR / name).is_file(), name)
        for name in (
            blind_acceptance.INPUT_FILENAME,
            blind_acceptance.REFERENCE_FILENAME,
            blind_acceptance.MANIFEST_FILENAME,
        ):
            self.assertTrue((out / edition.BLIND_DIR / name).is_file(), name)

    def test_manifest_recomputes_subset_digests_and_relations(self) -> None:
        out = self.root / "edition"
        self.build_edition(out)
        manifest = read_json(out / "manifest.json")
        self.assertEqual(manifest["schema_version"], edition.EDITION_SCHEMA)
        self.assertTrue(manifest["frozen"])
        subsets = {entry["id"]: entry for entry in manifest["subsets"]}
        for subset_id, directory in (
            ("development", edition.DEVELOPMENT_DIR),
            (blind_acceptance.SUBSET_ID, edition.BLIND_DIR),
        ):
            entry = subsets[subset_id]
            subset_manifest = read_json(out / directory / "manifest.json")
            self.assertEqual(
                entry["content_sha256"], subset_manifest["integrity"]["content_sha256"]
            )
            self.assertEqual(entry["batch_id"], subset_manifest["batch_id"])
            self.assertEqual(
                entry["samples"], len(subset_manifest["samples"])
            )
            digest = hashlib.sha256(
                (out / directory / "input-batch.json").read_bytes()
            ).hexdigest()
            self.assertEqual(entry["input_sha256"], digest)

        relation = manifest["relations"]["subsets_isolated"]
        self.assertEqual(relation["sample_id_intersection"], [])
        self.assertEqual(relation["record_intersection"], [])
        self.assertTrue(relation["evidence_sha256"])
        usage = manifest["usage"]
        self.assertTrue(usage["development"]["tuning_allowed"])
        self.assertFalse(usage["blind_acceptance"]["tuning_allowed"])
        self.assertFalse(usage["run_policy"]["baseline_fallback_allowed"])
        self.assertEqual(
            usage["provider_contract_test_suite"]["status"], "not_in_corpus"
        )

    def test_write_is_idempotent_and_rejects_conflicts(self) -> None:
        out = self.root / "edition"
        self.build_edition(out)
        manifest_bytes = (out / "manifest.json").read_bytes()
        self.assertEqual(self.build_edition(out)["status"], "unchanged")
        self.assertEqual(manifest_bytes, (out / "manifest.json").read_bytes())

        self.claims = self.available("toy_atbench", 1)
        self.acceptance = toy_acceptance_manifest(self.claims)
        with self.assertRaises(RunError) as context:
            self.build_edition(out)
        self.assertEqual(context.exception.code, "corpus_version_conflict")
        self.assertEqual(manifest_bytes, (out / "manifest.json").read_bytes())

    def test_legacy_subset_only_directory_is_refused(self) -> None:
        out = self.root / "edition"
        development.write_development_corpus(
            version=TOY_VERSION,
            dataset_root=self.datasets,
            acceptance_manifest=toy_acceptance_manifest(),
            out_dir=out,
        )
        with self.assertRaises(RunError) as context:
            self.build_edition(out)
        self.assertEqual(context.exception.code, "corpus_directory_not_frozen")
        self.assertEqual(context.exception.exit_code, 3)

    def test_tampered_subset_file_fails_verification(self) -> None:
        out = self.root / "edition"
        self.build_edition(out)
        target = out / edition.BLIND_DIR / blind_acceptance.INPUT_FILENAME
        payload = read_json(target)
        payload["items"][0]["trajectory"]["events"][0]["content"]["text"] = "tampered"
        write_json(target, payload)
        report = edition.verify_edition(root=out, acceptance_manifest=self.acceptance)
        self.assertEqual(report["status"], "fail")
        paths = {issue["path"] for issue in report["issues"]}
        self.assertTrue(any(path.startswith("artifacts[") for path in paths), paths)

    def test_verify_passes_and_is_reproducible_with_cache(self) -> None:
        out = self.root / "edition"
        self.build_edition(out)
        report = edition.verify_edition(
            root=out,
            dataset_root=self.datasets,
            acceptance_manifest=self.acceptance,
        )
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["reproducible"], "verified")
        self.assertTrue(report["checks"]["identity_relations_ok"])

    def test_readme_documents_three_layers_and_run_rules(self) -> None:
        out = self.root / "edition"
        self.build_edition(out)
        text = (out / "README.md").read_text(encoding="utf-8")
        for token in edition.README_REQUIRED_TOKENS:
            self.assertIn(token, text)
        blind_readme = (out / edition.BLIND_DIR / "README.md").read_text(encoding="utf-8")
        for token in ("Blind Acceptance", "revision", "独立 run", "不用于"):
            self.assertIn(token, blind_readme)


class CommittedEditionTest(unittest.TestCase):
    """已提交的整版交付物在不读数据缓存的前提下也必须自洽。"""

    def setUp(self) -> None:
        if not (COMMITTED_CORPUS / "manifest.json").is_file():
            self.skipTest("尚未生成整版 topic4-provider-corpus-v2")
        self.edition = read_json(COMMITTED_CORPUS / "manifest.json")
        self.development_manifest = read_json(
            COMMITTED_DEVELOPMENT / development.MANIFEST_FILENAME
        )
        self.blind_manifest = read_json(
            COMMITTED_BLIND / blind_acceptance.MANIFEST_FILENAME
        )

    def test_subsets_have_expected_counts(self) -> None:
        self.assertEqual(self.edition["schema_version"], edition.EDITION_SCHEMA)
        subsets = {entry["id"]: entry["samples"] for entry in self.edition["subsets"]}
        self.assertEqual(subsets, {"development": 80, "blind_acceptance": 48})
        self.assertEqual(len(self.development_manifest["samples"]), 80)
        self.assertEqual(len(self.blind_manifest["samples"]), 48)

    def test_development_selection_unchanged_by_layout_migration(self) -> None:
        """已冻结的 v2 开发子集身份钉：样本集合与正式输入字节不得漂移。"""
        batch = (COMMITTED_DEVELOPMENT / development.INPUT_FILENAME).read_bytes()
        self.assertEqual(
            hashlib.sha256(batch).hexdigest(),
            "1abfd702b9d6499aeea8d592ff3bc5aa996569566188bf3ae447e729634b7fc5",
        )
        self.assertEqual(
            self.development_manifest["batch_id"], "audit-input-cc8f654f0939d0f2"
        )

    def test_blind_acceptance_preserves_acceptance_v1_identities(self) -> None:
        acceptance = read_json(COMMITTED_ACCEPTANCE)
        self.assertEqual(
            {sample["source_sample_id"] for sample in self.blind_manifest["samples"]},
            {sample["sample_id"] for sample in acceptance["samples"]},
        )
        self.assertEqual(
            {
                (sample["dataset_id"], sample["record_index"])
                for sample in self.blind_manifest["samples"]
            },
            {
                (sample["dataset_id"], sample["record_index"])
                for sample in acceptance["samples"]
            },
        )
        self.assertEqual(
            self.blind_manifest["source_manifest"]["manifest_id"], "acceptance-v1"
        )
        self.assertTrue(
            self.blind_manifest["relations"]["equals_acceptance"][
                "sample_identity_equal"
            ]
        )

    def test_subsets_stay_isolated(self) -> None:
        development_records = {
            (sample["dataset_id"], sample["record_index"])
            for sample in self.development_manifest["samples"]
        }
        blind_records = {
            (sample["dataset_id"], sample["record_index"])
            for sample in self.blind_manifest["samples"]
        }
        self.assertEqual(development_records & blind_records, set())
        relation = self.edition["relations"]["subsets_isolated"]
        self.assertEqual(relation["sample_id_intersection"], [])
        self.assertEqual(relation["record_intersection"], [])

    def test_blind_input_is_unlabeled_and_unlinked(self) -> None:
        batch = read_json(COMMITTED_BLIND / blind_acceptance.INPUT_FILENAME)
        self.assertEqual(len(batch["items"]), 48)
        self.assertEqual(validate_audit_input(batch), [])
        raw = json.dumps(batch, ensure_ascii=False)
        for item in batch["items"]:
            self.assertIsNone(item["policy"])
            self.assertRegex(item["sample_id"], r"^sample-[0-9a-f]{16}$")
        for token in ("atbench", "hintbench", "risk_source", "failure_mode", "is_risky"):
            self.assertNotIn(token, raw)
        for sample in self.blind_manifest["samples"]:
            self.assertNotIn(sample["source_sample_id"], raw)

    def test_edition_verify_cli_passes_without_dataset_cache(self) -> None:
        completed = invoke_cli("corpus", "edition", "verify", "--root", COMMITTED_CORPUS)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["reproducible"], "skipped")

    def test_subset_verify_cli_still_passes(self) -> None:
        completed = invoke_cli(
            "corpus", "development", "verify", "--root", COMMITTED_DEVELOPMENT
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class EditionCliTest(BlindAcceptanceCase):
    def test_missing_arguments_are_reported(self) -> None:
        build = invoke_cli("corpus", "edition", "build")
        self.assertEqual(build.returncode, 2)
        self.assertEqual(json.loads(build.stderr)["code"], "missing_output")
        verify = invoke_cli("corpus", "edition", "verify")
        self.assertEqual(verify.returncode, 2)
        self.assertEqual(json.loads(verify.stderr)["code"], "missing_root")

    def test_unknown_version_is_rejected(self) -> None:
        completed = invoke_cli(
            "corpus",
            "edition",
            "build",
            "--version",
            "does-not-exist",
            "--out",
            self.root / "out",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stderr)["code"], "unknown_corpus_version")


@unittest.skipUnless(
    (PROJECT_ROOT / ".cache" / "datasets" / "atbench.json").is_file()
    and (PROJECT_ROOT / ".cache" / "datasets" / "hintbench.json").is_file(),
    "需要固定数据缓存",
)
class CommittedEditionRebuildTest(unittest.TestCase):
    def test_rebuild_matches_committed_edition(self) -> None:
        if not (COMMITTED_CORPUS / "manifest.json").is_file():
            self.skipTest("尚未生成整版 topic4-provider-corpus-v2")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "corpus"
            shutil.copytree(COMMITTED_CORPUS, target)
            status = edition.write_edition(
                version=development.DEFAULT_CORPUS_VERSION,
                dataset_root=PROJECT_ROOT / ".cache" / "datasets",
                acceptance_manifest=read_json(COMMITTED_ACCEPTANCE),
                out_dir=target,
            )
            self.assertEqual(status["status"], "unchanged", status)
            for relative in (
                "manifest.json",
                "README.md",
                f"{edition.DEVELOPMENT_DIR}/{development.INPUT_FILENAME}",
                f"{edition.BLIND_DIR}/{blind_acceptance.INPUT_FILENAME}",
                f"{edition.BLIND_DIR}/{blind_acceptance.MANIFEST_FILENAME}",
            ):
                self.assertEqual(
                    (COMMITTED_CORPUS / relative).read_bytes(),
                    (target / relative).read_bytes(),
                    relative,
                )


if __name__ == "__main__":
    unittest.main()
