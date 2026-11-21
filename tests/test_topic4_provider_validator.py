# SPDX-License-Identifier: Apache-2.0
"""contract validator 公共 seam 测试。"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.sanitize import config_sha256
from trajectory_audit.topic4_provider.validator import (
    is_valid,
    unicode_slice,
    validate_result,
)
from tools.fake_topic4_provider.scenarios import build_result

ROOT = Path(__file__).resolve().parents[1]
SINGLE = json.loads(
    (ROOT / "fixtures/topic4_provider/single_input_batch.json").read_text(
        encoding="utf-8"
    )
)
MINIMAL = json.loads(
    (ROOT / "fixtures/topic4_provider/minimal_input_batch.json").read_text(
        encoding="utf-8"
    )
)


class ValidatorTests(unittest.TestCase):
    def test_unicode_code_point_slice(self) -> None:
        text = "备份AB"
        self.assertEqual(unicode_slice(text, 0, 2), "备份")
        self.assertEqual(unicode_slice(text, 2, 4), "AB")

    def test_complete_safe_valid(self) -> None:
        result = build_result(SINGLE, {"scenario": "safe"})
        issues = validate_result(SINGLE, result)
        self.assertEqual(issues, [])

    def test_unsafe_located_valid(self) -> None:
        result = build_result(SINGLE, {"scenario": "unsafe_located"})
        self.assertTrue(is_valid(SINGLE, result))

    def test_unsafe_unlocated_valid(self) -> None:
        result = build_result(SINGLE, {"scenario": "unsafe_unlocated"})
        self.assertTrue(is_valid(SINGLE, result))

    def test_partial_and_failed_and_rejected(self) -> None:
        partial = build_result(MINIMAL, {"scenario": "partial_mixed"})
        failed = build_result(MINIMAL, {"scenario": "failed_all"})
        rejected = build_result(SINGLE, {"scenario": "rejected_batch"})
        self.assertTrue(is_valid(MINIMAL, partial))
        self.assertTrue(is_valid(MINIMAL, failed))
        self.assertTrue(is_valid(SINGLE, rejected))

    def test_reject_unknown_major(self) -> None:
        result = build_result(SINGLE, {"scenario": "bad_schema_version"})
        codes = {issue.code for issue in validate_result(SINGLE, result)}
        self.assertIn("unsupported_major", codes)

    def test_identity_errors(self) -> None:
        for scenario, code in [
            ("bad_batch_id", "batch_id_mismatch"),
            ("missing_sample", "missing_sample"),
            ("duplicate_sample", "duplicate_sample"),
            ("unknown_sample", "unknown_sample"),
            ("sha_mismatch", "sha_mismatch"),
        ]:
            batch = MINIMAL if scenario == "missing_sample" else SINGLE
            result = build_result(batch, {"scenario": scenario})
            codes = {issue.code for issue in validate_result(batch, result)}
            self.assertIn(code, codes, scenario)

    def test_evidence_errors(self) -> None:
        for scenario, code in [
            ("bad_evidence_pointer", "bad_evidence_pointer"),
            ("bad_excerpt", "bad_excerpt"),
            ("bad_span", "bad_span"),
        ]:
            result = build_result(SINGLE, {"scenario": scenario})
            codes = {issue.code for issue in validate_result(SINGLE, result)}
            self.assertIn(code, codes, scenario)

    def test_unknown_core_field_and_extensions(self) -> None:
        bad = build_result(SINGLE, {"scenario": "unknown_core_field"})
        good = build_result(SINGLE, {"scenario": "legal_extensions"})
        self.assertIn(
            "unknown_core_field",
            {issue.code for issue in validate_result(SINGLE, bad)},
        )
        self.assertEqual(validate_result(SINGLE, good), [])

    def test_missing_provider_identity(self) -> None:
        result = build_result(SINGLE, {"scenario": "missing_provider_identity"})
        codes = {issue.code for issue in validate_result(SINGLE, result)}
        self.assertTrue(
            {"missing_provider_identity", "missing_components"} & codes
        )

    def test_item_xor_and_verdict_invariants(self) -> None:
        result = build_result(SINGLE, {"scenario": "safe"})
        item = result["items"][0]
        broken = copy.deepcopy(result)
        broken["items"][0]["failure"] = {
            "stage": "x",
            "code": "y",
            "reason": "z",
            "retryable": False,
        }
        codes = {issue.code for issue in validate_result(SINGLE, broken)}
        self.assertIn("item_xor", codes)

        unsafe_empty = copy.deepcopy(result)
        unsafe_empty["items"][0]["result"]["verdict"] = "unsafe"
        codes = {issue.code for issue in validate_result(SINGLE, unsafe_empty)}
        self.assertIn("unsafe_without_findings", codes)

        safe_with = build_result(SINGLE, {"scenario": "unsafe_located"})
        safe_with["items"][0]["result"]["verdict"] = "safe"
        codes = {issue.code for issue in validate_result(SINGLE, safe_with)}
        self.assertIn("safe_with_findings", codes)

        self.assertEqual(item["status"], "success")

    def test_config_sha_is_sanitized(self) -> None:
        digest = config_sha256({"api_key": "SECRET", "scenario": "safe"})
        digest2 = config_sha256({"api_key": "[REDACTED]", "scenario": "safe"})
        self.assertEqual(digest, digest2)


if __name__ == "__main__":
    unittest.main()
