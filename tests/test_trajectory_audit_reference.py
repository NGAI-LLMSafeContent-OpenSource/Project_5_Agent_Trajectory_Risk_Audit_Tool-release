# SPDX-License-Identifier: Apache-2.0
"""接受测试：ATBench 项目参考标注审阅包。

真实的 24 条样本依赖 18 MB 固定版本数据集，因此集成测试把来源表替换成玩具
数据集、其余流程保持真实（真实 adapter、真实归一化、真实事件定位）。已提交的
审阅包另行静态校验，不需要任何数据集文件。
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

from trajectory_audit import acceptance, pinned, reference
from trajectory_audit.errors import RunError
from trajectory_audit.model import sha256_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ATBENCH_FIXTURE = PROJECT_ROOT / "fixtures" / "smoke" / "atbench_smoke.json"
COMMITTED_PACKAGE = PROJECT_ROOT / "acceptance" / "atbench_reference_v1"
COMMITTED_MANIFEST = PROJECT_ROOT / "acceptance" / "acceptance-v1.json"

TEST_MANIFEST_ID = "acceptance-reference-test"
TEST_PACKAGE_ID = "toy-atbench-reference"
TEST_DATASET_ID = "toy_atbench"
GENERATED_AT = "2026-10-23T07:10:00Z"

SAFE_MODES = (
    "benign",
    "benign",
    "procedural_deviation_or_inaction",
    "incorrect_tool_parameters",
    "failure_to_validate_tool_outputs",
    "benign",
)
UNSAFE_SOURCES = (
    "indirect_prompt_injection",
    "tool_description_injection",
    "indirect_prompt_injection",
    "tool_description_injection",
)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_policy() -> dict:
    layers = {
        "safe": {"field": "label", "true_value": 0},
        "unsafe": {"field": "label", "true_value": 1},
    }
    return {
        "seed": 11,
        "algorithm": "stratified-largest-remainder/1",
        "selection_basis": "source labels only; detected results never influence selection",
        "risk_layers": {TEST_DATASET_ID: layers},
        "stratification": {
            TEST_DATASET_ID: {
                "safe": {"layer": layers["safe"], "field": "failure_mode"},
                "unsafe": {"layer": layers["unsafe"], "field": "risk_source"},
            }
        },
        "quotas": {
            TEST_DATASET_ID: {"total": 6, "strata": {"safe": 2, "unsafe": 4}},
        },
    }


def toy_records() -> list[dict]:
    template = read_json(ATBENCH_FIXTURE)["records"][0]  # type: ignore[index]
    records: list[dict] = []
    for index, mode in enumerate(SAFE_MODES):
        record = copy.deepcopy(template)
        record["id"] = f"toy-safe-{index}"
        record["label"] = 0
        record["failure_mode"] = mode
        record["risk_source"] = "none"
        record["real_world_harm"] = "none"
        record["reason"] = f"benign toy record {index}"
        records.append(record)
    for index, source in enumerate(UNSAFE_SOURCES):
        record = copy.deepcopy(template)
        record["id"] = f"toy-unsafe-{index}"
        record["label"] = 1
        record["risk_source"] = source
        record["failure_mode"] = "choosing_malicious_tool"
        record["real_world_harm"] = "financial_harm"
        record["reason"] = f"unsafe toy record {index}"
        records.append(record)
    return records


def write_toy_dataset(root: Path) -> dict:
    records = toy_records()
    path = write_json(root / "toy_atbench.json", records)
    raw = path.read_bytes()
    return {
        "dataset_id": TEST_DATASET_ID,
        "adapter_id": "atbench",
        "dataset_version": "toy/atbench@v1",
        "upstream": "https://example.invalid/toy",
        "upstream_commit": "0" * 40,
        "url": "https://example.invalid/toy_atbench.json",
        "license": "Apache-2.0",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "cache_name": "toy_atbench.json",
        "layout": "json_array",
        "record_identity": "toy",
    }


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


class ToyPackageCase(unittest.TestCase):
    """玩具数据集 + 真实 adapter、真实验收清单构建。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dataset_root = self.root / "datasets"
        self.registry = write_toy_dataset(self.dataset_root)
        self.policy = test_policy()
        for patcher in (
            mock.patch.dict(pinned.BY_DATASET, {TEST_DATASET_ID: self.registry}, clear=True),
            mock.patch.dict(
                pinned.ACCEPTANCE_POLICIES, {TEST_MANIFEST_ID: self.policy}, clear=True
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.acceptance_manifest = acceptance.build_manifest(
            dataset_root=self.dataset_root, manifest_id=TEST_MANIFEST_ID
        )
        self.package = self.build()

    def build(self, **kwargs: object) -> dict:
        return reference.build_package(
            acceptance_manifest=self.acceptance_manifest,
            dataset_root=self.dataset_root,
            package_id=TEST_PACKAGE_ID,
            generated_at=GENERATED_AT,
            dataset_id=TEST_DATASET_ID,
            expected_manifest_id=TEST_MANIFEST_ID,
            **kwargs,  # type: ignore[arg-type]
        )

    def drafts(self) -> list[dict]:
        return self.package["drafts"]["drafts"]

    def draft(self, index: int = 0) -> dict:
        return self.drafts()[index]

    def transcript(self, draft: dict) -> dict:
        for entry in self.package["transcripts"]["transcripts"]:
            if entry["draft_id"] == draft["draft_id"]:
                return entry
        raise AssertionError("missing transcript")

    def review_item(self, draft: dict) -> dict:
        for item in self.package["review"]["items"]:
            if item["draft_id"] == draft["draft_id"]:
                return item
        raise AssertionError("missing review item")


class BuildTest(ToyPackageCase):
    def test_one_draft_per_acceptance_sample(self) -> None:
        samples = [
            sample
            for sample in self.acceptance_manifest["samples"]
            if sample["dataset_id"] == TEST_DATASET_ID
        ]
        drafts = self.drafts()
        self.assertEqual(len(drafts), len(samples))
        self.assertEqual(
            {draft["draft_id"] for draft in drafts},
            {f"ref-{sample['sample_id']}" for sample in samples},
        )
        for sample in samples:
            draft = next(
                item for item in drafts if item["sample_id"] == sample["sample_id"]
            )
            self.assertEqual(draft["input"]["record_index"], sample["record_index"])
            self.assertEqual(draft["input"]["record_sha256"], sample["record_sha256"])
            self.assertEqual(
                draft["input"]["acceptance_content_sha256"],
                self.acceptance_manifest["integrity"]["content_sha256"],
            )

    def test_drafts_are_trajectory_scoped_and_pending(self) -> None:
        for draft in self.drafts():
            self.assertEqual(draft["layer"], "reference")
            self.assertEqual(draft["status"], "pending")
            self.assertEqual(draft["scope"], "trajectory")
            self.assertIs(draft["located"], False)
            self.assertIsNone(draft["origin_event"])
            self.assertEqual(draft["categories"], [])
            self.assertEqual(draft["basis"], "source_label_projection")

    def test_generator_identity_is_explicit(self) -> None:
        generator = self.package["manifest"]["generator"]
        self.assertEqual(generator["kind"], "deterministic_projection")
        self.assertIn("model", generator)
        self.assertIn("prompt_template", generator)
        self.assertIsNone(generator["model"])
        self.assertIsNone(generator["prompt_template"])
        for draft in self.drafts():
            self.assertEqual(draft["generator"], generator)

    def test_source_facts_are_preserved_not_rewritten(self) -> None:
        records = read_json(self.dataset_root / "toy_atbench.json")
        for draft in self.drafts():
            record = records[draft["input"]["record_index"]]  # type: ignore[index]
            self.assertEqual(draft["source_label"]["label"], record["label"])
            self.assertEqual(
                draft["source_label"]["risk_source"], record["risk_source"]
            )
            self.assertEqual(
                draft["source_label"]["failure_mode"], record["failure_mode"]
            )
            self.assertEqual(draft["verdict"], "unsafe" if record["label"] == 1 else "safe")
            self.assertEqual(
                draft["categories"],
                [],
                "未确认的草稿不得凭空生成统一类别",
            )
            self.assertEqual(draft["mapping"]["status"], "not_applicable")

    def test_every_evidence_quote_reads_back(self) -> None:
        records = read_json(self.dataset_root / "toy_atbench.json")
        issues = reference.verify_against_dataset(self.package, self.dataset_root)
        self.assertEqual(issues, [])
        for draft in self.drafts():
            record = records[draft["input"]["record_index"]]  # type: ignore[index]
            self.assertTrue(draft["evidence"])
            for item in draft["evidence"]:
                self.assertIs(item["located"], True)
                self.assertIsNone(item["failure"])
                self.assertEqual(
                    reference.check_evidence(item, record),
                    None,
                    f"{draft['draft_id']} / {item['json_path']}",
                )
            self.assertEqual(draft["locator_failures"], [])

    def test_evidence_carries_full_locator_fields(self) -> None:
        draft = self.draft()
        for item in draft["evidence"]:
            for key in reference.EVIDENCE_REQUIRED_KEYS:
                self.assertIn(key, item)
            self.assertEqual(item["record_index"], draft["input"]["record_index"])
            self.assertEqual(item["record_sha256"], draft["input"]["record_sha256"])

    def test_transcript_exposes_event_identity_and_path(self) -> None:
        draft = self.draft()
        transcript = self.transcript(draft)
        self.assertGreater(len(transcript["events"]), 0)
        self.assertIsNone(transcript["problem"])
        identifiers = {event["event_id"] for event in transcript["events"]}
        self.assertEqual(len(identifiers), len(transcript["events"]))
        self.assertEqual(draft["transcript"]["event_count"], len(transcript["events"]))
        for event in transcript["events"]:
            self.assertTrue(event["json_path"])
            self.assertEqual(event["record_index"], draft["input"]["record_index"])
            self.assertEqual(event["record_sha256"], draft["input"]["record_sha256"])

    def test_review_table_records_original_draft_hash(self) -> None:
        for draft in self.drafts():
            item = self.review_item(draft)
            self.assertIsNone(item["decision"])
            self.assertIsNone(item["revised"])
            self.assertEqual(
                item["draft_sha256"], sha256_json(draft)
            )
            self.assertEqual(
                set(item["field_decisions"]), set(reference.REVISABLE_FIELDS)
            )

    def test_build_is_deterministic(self) -> None:
        again = self.build()
        self.assertEqual(
            json.dumps(again["drafts"], sort_keys=True, ensure_ascii=False),
            json.dumps(self.package["drafts"], sort_keys=True, ensure_ascii=False),
        )
        self.assertEqual(
            json.dumps(again["manifest"], sort_keys=True, ensure_ascii=False),
            json.dumps(self.package["manifest"], sort_keys=True, ensure_ascii=False),
        )

    def test_build_refuses_tampered_source_record(self) -> None:
        tampered = copy.deepcopy(self.acceptance_manifest)
        for sample in tampered["samples"]:
            if sample["dataset_id"] == TEST_DATASET_ID:
                sample["record_sha256"] = "0" * 64
                break
        tampered["integrity"] = {
            "digest_version": acceptance.INTEGRITY_VERSION,
            "content_sha256": acceptance.content_sha256(tampered),
        }
        with self.assertRaises(RunError) as caught:
            reference.build_package(
                acceptance_manifest=tampered,
                dataset_root=self.dataset_root,
                package_id=TEST_PACKAGE_ID,
                dataset_id=TEST_DATASET_ID,
                expected_manifest_id=TEST_MANIFEST_ID,
            )
        self.assertEqual(caught.exception.code, "record_sha256_changed")

    def test_build_refuses_wrong_dataset_hash(self) -> None:
        path = self.dataset_root / "toy_atbench.json"
        write_json(path, toy_records()[:3])
        with self.assertRaises(RunError) as caught:
            self.build()
        self.assertEqual(caught.exception.code, "dataset_hash_mismatch")


class PackageValidationTest(ToyPackageCase):
    def test_fresh_package_validates(self) -> None:
        self.assertEqual(
            reference.validate_package(
                self.package, acceptance_manifest=self.acceptance_manifest
            ),
            [],
        )

    def test_tampered_draft_breaks_original_hash(self) -> None:
        self.draft()["reason"] = "被改写过的理由"
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("原始草稿已被改写" in issue["message"] for issue in issues), issues
        )

    def test_unlocated_evidence_must_explain_failure(self) -> None:
        item = self.draft()["evidence"][0]
        item["located"] = False
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("未定位的证据必须说明失败原因" in issue["message"] for issue in issues),
            issues,
        )

    def test_detected_layer_cannot_be_included(self) -> None:
        for layer in self.package["manifest"]["layers"]:
            if layer["layer"] == "detected":
                layer["included"] = True
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("detected 结果不得并入" in issue["message"] for issue in issues), issues
        )

    def test_missing_transcript_is_rejected(self) -> None:
        self.package["transcripts"]["transcripts"].pop()
        issues = reference.validate_package(self.package)
        self.assertTrue(any("草稿缺少轨迹原文" in issue["message"] for issue in issues))

    def test_accept_requires_per_field_signoff(self) -> None:
        draft = self.draft()
        item = self.review_item(draft)
        item["decision"] = "accept"
        item["note"] = "同意"
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("必须逐字段签字" in issue["message"] for issue in issues), issues
        )

    def test_revise_requires_matching_field_decisions(self) -> None:
        draft = self.draft()
        item = self.review_item(draft)
        item["decision"] = "revise"
        item["note"] = "改范围"
        for field in reference.REVISABLE_FIELDS:
            item["field_decisions"][field] = "accept"
        item["field_decisions"]["scope"] = "revise"
        item["revised"] = {"reason": "未声明为 revised 的字段"}
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any(
                "只有字段决定为 revise 才能出现在修订内容中" in issue["message"]
                for issue in issues
            ),
            issues,
        )
        self.assertTrue(
            any("缺少修订内容" in issue["message"] for issue in issues), issues
        )

    def test_reject_must_leave_note(self) -> None:
        item = self.review_item(self.draft())
        item["decision"] = "reject"
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("人工决定必须留下说明" in issue["message"] for issue in issues)
        )

    def test_generator_must_record_model_and_prompt(self) -> None:
        del self.package["manifest"]["generator"]["model"]
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("manifest.generator.model" in issue["path"] for issue in issues), issues
        )

    def test_dataset_mismatch_is_reported(self) -> None:
        path = self.dataset_root / "toy_atbench.json"
        payload = read_json(path)
        payload[0]["reason"] = "mutated"  # type: ignore[index]
        write_json(path, payload)
        issues = reference.verify_against_dataset(self.package, self.dataset_root)
        self.assertTrue(any(issue["path"] == "manifest.input.dataset_sha256" for issue in issues), issues)

    def test_evidence_quote_mismatch_is_reported(self) -> None:
        self.draft()["evidence"][1]["quote"] = "不存在的引用"
        issues = reference.verify_against_dataset(self.package, self.dataset_root)
        self.assertTrue(any("定位失败原因与实况不一致" in issue["message"] for issue in issues), issues)

    def test_writes_all_package_files(self) -> None:
        out = self.root / "package"
        reference.write_package(self.package, out)
        for name in (
            "manifest.json",
            "drafts.json",
            "transcripts.json",
            "review.json",
            "index.html",
            "README.md",
        ):
            self.assertTrue((out / name).is_file(), name)
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("reference review package", page)
        self.assertNotIn("<script", page)
        self.assertNotIn('src="http', page)
        self.assertNotIn("<link", page)
        readme = (out / "README.md").read_text(encoding="utf-8")
        self.assertIn("不是 ATBench 来源数据集真值", readme)

    def test_rebuild_refuses_decided_review(self) -> None:
        out = self.root / "package"
        reference.write_package(self.package, out)
        review = json.loads((out / "review.json").read_text(encoding="utf-8"))
        review["items"][0]["decision"] = "reject"
        review["items"][0]["note"] = "理由不成立"
        write_json(out / "review.json", review)
        with self.assertRaises(RunError) as caught:
            reference.write_package(self.package, out)
        self.assertEqual(caught.exception.code, "package_review_in_progress")
        self.assertEqual(caught.exception.exit_code, 3)
        reference.write_package(self.package, out, force=True)

    def test_refresh_updates_review_outputs_without_rewriting_frozen_inputs(self) -> None:
        out = self.root / "package"
        reference.write_package(self.package, out)
        protected = {
            name: (out / name).read_bytes()
            for name in ("manifest.json", "drafts.json", "transcripts.json")
        }
        review = read_json(out / "review.json")
        review["items"][0]["decision"] = "reject"  # type: ignore[index]
        review["items"][0]["note"] = "人工认为 verdict 不成立"  # type: ignore[index]
        write_json(out / "review.json", review)
        loaded = reference.load_package(out)

        status = reference.refresh_review_artifacts(loaded, out)

        self.assertEqual(status, "in_progress")
        self.assertEqual(read_json(out / "review.json")["status"], "in_progress")  # type: ignore[index]
        for name, original in protected.items():
            self.assertEqual((out / name).read_bytes(), original, name)
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("in_progress · 审阅中", page)
        self.assertIn("人工认为 verdict 不成立", page)

    def test_ai_assistance_requires_model_and_protocol(self) -> None:
        review = self.package["review"]
        review["assistance"] = {
            "mode": "ai_assisted",
            "product": "OpenAI Codex",
            "model": None,
            "prompt_protocol": None,
            "final_authority": "human",
        }
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any(issue["path"] == "review.assistance.model" for issue in issues),
            issues,
        )
        self.assertTrue(
            any(
                issue["path"] == "review.assistance.prompt_protocol"
                for issue in issues
            ),
            issues,
        )


class EffectiveLabelsTest(ToyPackageCase):
    def _accept(self, draft: dict, **revised: object) -> None:
        if draft["verdict"] == "unsafe" and "categories" not in revised:
            category = draft["source_label"]["failure_mode"]
            if category not in reference.REFERENCE_CATEGORIES:
                category = reference.REFERENCE_CATEGORIES[0]
            revised["categories"] = [category]
        item = self.review_item(draft)
        item["decision"] = "accept" if not revised else "revise"
        item["note"] = "人工确认"
        item["field_decisions"] = {
            field: ("revise" if field in revised else "accept")
            for field in reference.REVISABLE_FIELDS
        }
        item["revised"] = dict(revised) or None
        self.package["review"]["status"] = reference.review_status(
            self.package["review"]
        )

    def _complete_review(self) -> None:
        for draft in self.drafts():
            self._accept(draft)
        review = self.package["review"]
        review["reviewer"] = "human-reviewer"
        review["reviewed_at"] = GENERATED_AT
        review["status"] = reference.review_status(review)

    def test_pending_package_yields_no_effective_labels(self) -> None:
        labels, problems = reference.effective_labels(
            self.drafts(), self.package["review"], self.package["transcripts"]
        )
        self.assertEqual(labels, [])
        self.assertEqual(problems, [])
        export = reference.export_effective(self.package)
        self.assertEqual(export["status"], "pending")
        self.assertEqual(export["effective_count"], 0)
        self.assertEqual(export["draft_count"], 6)

    def test_rejected_draft_stays_inactive(self) -> None:
        item = self.review_item(self.draft())
        item["decision"] = "reject"
        item["note"] = "证据不足"
        labels, problems = reference.effective_labels(self.drafts(), self.package["review"])
        self.assertEqual(labels, [])
        self.assertEqual(problems, [])

    def test_accepted_trajectory_draft_becomes_effective(self) -> None:
        draft = self.draft()
        self._accept(draft)
        labels, problems = reference.effective_labels(
            self.drafts(), self.package["review"], self.package["transcripts"]
        )
        self.assertEqual(problems, [])
        self.assertEqual(len(labels), 1)
        label = labels[0]
        self.assertEqual(label["label_id"], draft["draft_id"])
        self.assertEqual(label["layer"], "reference")
        self.assertEqual(label["status"], "confirmed")
        self.assertEqual(label["scope"], "trajectory")
        self.assertIs(label["located"], False)
        self.assertEqual(label["event_ids"], [])
        self.assertEqual(label["mapping"]["status"], "human_confirmed")
        self.assertEqual(label["original_draft"], draft)
        self.assertEqual(label["review"]["note"], "人工确认")

    def test_effective_labels_do_not_include_detected_layer(self) -> None:
        self._accept(self.draft())
        labels, _ = reference.effective_labels(self.drafts(), self.package["review"])
        self.assertTrue(labels)
        for label in labels:
            self.assertEqual(label["layer"], "reference")
            self.assertNotIn("detected", label)
        layers = {layer["layer"]: layer for layer in self.package["manifest"]["layers"]}
        self.assertIs(layers["detected"]["included"], False)

    def test_revision_to_event_scope_uses_transcript_event(self) -> None:
        draft = self.draft()
        transcript = self.transcript(draft)
        event = next(
            item for item in transcript["events"] if item["type"] == "message"
        )
        revised_evidence = [
            {
                "kind": "event",
                "event_id": event["event_id"],
                "source_step_id": event["source_step_id"],
                "json_path": event["json_path"],
                "record_index": event["record_index"],
                "record_sha256": event["record_sha256"],
                "quote": event["summary"][:12],
                "char_range": None,
                "located": True,
                "failure": None,
            }
        ]
        self._accept(
            draft,
            scope="event",
            origin_event=event["event_id"],
            evidence=revised_evidence,
            categories=[],
        )
        self.assertEqual(reference.validate_package(self.package), [])
        labels, problems = reference.effective_labels(
            self.drafts(), self.package["review"], self.package["transcripts"]
        )
        self.assertEqual(problems, [])
        self.assertEqual(len(labels), 1)
        label = labels[0]
        self.assertEqual(label["scope"], "event")
        self.assertIs(label["located"], True)
        self.assertEqual(label["event_ids"], [event["event_id"]])
        self.assertEqual(label["categories"], [])

    def test_event_scope_with_unknown_origin_is_refused(self) -> None:
        draft = self.draft()
        transcript = self.transcript(draft)
        event = next(
            item for item in transcript["events"] if item["type"] == "message"
        )
        self._accept(
            draft,
            scope="event",
            origin_event="evt-does-not-exist",
            evidence=[
                {
                    "kind": "event",
                    "event_id": event["event_id"],
                    "source_step_id": None,
                    "json_path": event["json_path"],
                    "record_index": event["record_index"],
                    "record_sha256": event["record_sha256"],
                    "quote": event["summary"][:12],
                    "char_range": None,
                    "located": True,
                    "failure": None,
                }
            ],
        )
        labels, problems = reference.effective_labels(
            self.drafts(), self.package["review"], self.package["transcripts"]
        )
        self.assertEqual(labels, [])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["code"], "origin_event_not_in_transcript")

    def test_event_scope_without_event_evidence_is_refused(self) -> None:
        draft = self.draft()
        event = self.transcript(draft)["events"][0]
        self._accept(draft, scope="event", origin_event=event["event_id"])
        labels, problems = reference.effective_labels(
            self.drafts(), self.package["review"], self.package["transcripts"]
        )
        self.assertEqual(labels, [])
        self.assertEqual(
            problems[0]["code"], "event_scope_without_locatable_evidence"
        )

    def test_revision_with_unlocated_evidence_is_refused(self) -> None:
        draft = self.draft()
        item = self.review_item(draft)
        item["decision"] = "revise"
        item["note"] = "改了理由"
        item["field_decisions"] = {
            field: ("revise" if field == "evidence" else "accept")
            for field in reference.REVISABLE_FIELDS
        }
        item["revised"] = {
            "evidence": [
                {
                    "kind": "source_field",
                    "event_id": None,
                    "source_step_id": None,
                    "json_path": "reason_missing",
                    "record_index": draft["input"]["record_index"],
                    "record_sha256": draft["input"]["record_sha256"],
                    "quote": "找不到的引用",
                    "char_range": None,
                    "located": False,
                    "failure": "字段路径无法在原始记录中回读",
                }
            ]
        }
        labels, problems = reference.effective_labels(self.drafts(), self.package["review"])
        self.assertEqual(labels, [])
        self.assertEqual(problems[0]["code"], "evidence_not_located")

    def test_unknown_revised_field_is_refused(self) -> None:
        draft = self.draft()
        item = self.review_item(draft)
        item["decision"] = "revise"
        item["note"] = "越权修订"
        item["field_decisions"] = {field: "accept" for field in reference.REVISABLE_FIELDS}
        item["revised"] = {"status": "confirmed"}
        labels, problems = reference.effective_labels(self.drafts(), self.package["review"])
        self.assertEqual(labels, [])
        self.assertEqual(problems[0]["code"], "unknown_revised_field")

    def test_export_reports_confirmed_after_review(self) -> None:
        self._complete_review()
        export = reference.export_effective(self.package, exported_at=GENERATED_AT)
        self.assertEqual(export["status"], "confirmed")
        self.assertEqual(export["effective_count"], len(self.drafts()))
        self.assertEqual(export["exported_at"], GENERATED_AT)
        self.assertEqual(export["labels"][0]["layer"], "reference")

    def test_partial_review_cannot_export_effective_labels(self) -> None:
        self._accept(self.draft())
        export = reference.export_effective(self.package, exported_at=GENERATED_AT)
        self.assertEqual(export["status"], "in_progress")
        self.assertEqual(export["effective_count"], 0)
        self.assertTrue(reference.review_is_pending(self.package["review"]))

    def test_all_decisions_without_human_metadata_are_not_complete(self) -> None:
        for draft in self.drafts():
            self._accept(draft)
        review = self.package["review"]
        self.assertEqual(reference.review_status(review), "in_progress")
        self.assertFalse(reference.review_is_complete(review))
        export = reference.export_effective(self.package, exported_at=GENERATED_AT)
        self.assertEqual(export["effective_count"], 0)

    def test_unsafe_label_requires_controlled_reference_category(self) -> None:
        draft = next(item for item in self.drafts() if item["verdict"] == "unsafe")
        item = self.review_item(draft)
        item["decision"] = "accept"
        item["note"] = "错误地接受空类别"
        item["field_decisions"] = {
            field: "accept" for field in reference.REVISABLE_FIELDS
        }
        self.package["review"]["status"] = "in_progress"
        issues = reference.validate_package(self.package)
        self.assertTrue(
            any("unsafe 生效标注必须" in issue["message"] for issue in issues),
            issues,
        )


class CommittedPackageTest(unittest.TestCase):
    """已提交的审阅包必须自洽，且未确认时零生效。"""

    def setUp(self) -> None:
        if not (COMMITTED_PACKAGE / "manifest.json").is_file():
            self.skipTest("尚未生成已提交的参考标注审阅包")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.package = reference.load_package(COMMITTED_PACKAGE)
        self.acceptance_manifest = read_json(COMMITTED_MANIFEST)

    def test_committed_package_is_structurally_valid(self) -> None:
        self.assertEqual(
            reference.validate_package(
                self.package, acceptance_manifest=self.acceptance_manifest
            ),
            [],
        )

    def test_committed_package_covers_24_atbench_samples(self) -> None:
        drafts = self.package["drafts"]["drafts"]
        self.assertEqual(len(drafts), reference.ACCEPTANCE_ATBENCH_SAMPLES)
        self.assertEqual(self.package["manifest"]["draft_count"], 24)
        self.assertEqual(self.package["manifest"]["locator_failure_count"], 0)
        for draft in drafts:
            self.assertEqual(draft["scope"], "trajectory")
            self.assertIs(draft["located"], False)
            self.assertIs(draft["origin_event"], None)
            self.assertEqual(draft["status"], "pending")

    def test_committed_package_declares_disclaimers_and_layers(self) -> None:
        manifest = self.package["manifest"]
        self.assertTrue(manifest["disclaimers"])
        self.assertTrue(
            any("不是 ATBench 来源数据集真值" in line for line in manifest["disclaimers"])
        )
        self.assertIsNone(manifest["generator"]["model"])
        self.assertIsNone(manifest["generator"]["prompt_template"])
        layers = {layer["layer"]: layer for layer in manifest["layers"]}
        self.assertEqual(set(layers), {"source", "reference", "detected"})
        self.assertIs(layers["detected"]["included"], False)

    def test_committed_review_is_pending_and_exports_nothing(self) -> None:
        export = reference.export_effective(self.package)
        self.assertEqual(export["status"], "pending")
        self.assertEqual(export["effective_count"], 0)
        self.assertTrue(reference.review_is_pending(self.package["review"]))

    def test_committed_package_is_reproducible(self) -> None:
        manifest = self.package["manifest"]
        self.assertEqual(manifest["annotation_version"], reference.DEFAULT_PACKAGE_ID)
        self.assertEqual(manifest["input"]["acceptance_manifest_id"], "acceptance-v1")
        self.assertEqual(
            manifest["input"]["acceptance_content_sha256"],
            self.acceptance_manifest["integrity"]["content_sha256"],
        )

    def test_cli_validates_committed_package(self) -> None:
        result = json_cli(
            "reference",
            "validate",
            "--package",
            COMMITTED_PACKAGE,
            "--manifest",
            COMMITTED_MANIFEST,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["draft_count"], 24)
        self.assertTrue(payload["review_pending"])
        self.assertEqual(payload["issues"], [])

    def test_cli_exports_zero_effective_labels(self) -> None:
        out = Path(self._tmp.name) / "effective-labels.json"
        result = json_cli(
            "reference", "export", "--package", COMMITTED_PACKAGE, "--out", out
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["effective_count"], 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["labels"], [])
        self.assertEqual(written["schema_version"], reference.EFFECTIVE_SCHEMA)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
