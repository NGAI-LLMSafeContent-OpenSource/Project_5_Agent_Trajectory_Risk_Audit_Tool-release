# SPDX-License-Identifier: Apache-2.0
"""result mapper 公共 seam 测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.mapper import map_topic4_result
from tools.fake_topic4_provider.scenarios import build_result

ROOT = Path(__file__).resolve().parents[1]
MINIMAL = json.loads(
    (ROOT / "fixtures/topic4_provider/minimal_input_batch.json").read_text(
        encoding="utf-8"
    )
)
SINGLE = json.loads(
    (ROOT / "fixtures/topic4_provider/single_input_batch.json").read_text(
        encoding="utf-8"
    )
)


class MapperTests(unittest.TestCase):
    def test_preserves_partial_successes(self) -> None:
        result = build_result(MINIMAL, {"scenario": "partial_mixed"})
        mapped = map_topic4_result(result)
        self.assertEqual(mapped["batch"]["status"], "partial")
        self.assertEqual(len(mapped["success_items"]), 1)
        self.assertEqual(len(mapped["failure_items"]), 1)
        finding_trace = mapped["success_items"][0]
        self.assertEqual(
            finding_trace["provider_run_id"], result["provider_run_id"]
        )
        self.assertIn("sample_id", finding_trace)
        self.assertIn("audit_input_sha256", finding_trace)

    def test_maps_provider_native_fields(self) -> None:
        result = build_result(SINGLE, {"scenario": "unsafe_located"})
        mapped = map_topic4_result(result)
        finding = mapped["detected_findings"][0]
        self.assertEqual(finding["layer"], "detected")
        self.assertTrue(finding["provider_native"])
        self.assertEqual(finding["category"], "unauthorized_action")
        self.assertIn("evidence", finding)
        self.assertIn("confidence", finding)
        self.assertIn("severity", finding)
        self.assertNotIn("source_labels", finding)
        self.assertNotIn("reference", finding)

    def test_rejected_mapping(self) -> None:
        result = build_result(SINGLE, {"scenario": "rejected_batch"})
        mapped = map_topic4_result(result)
        self.assertIsNotNone(mapped["rejection"])
        self.assertEqual(mapped["success_items"], [])
        self.assertEqual(mapped["detected_findings"], [])


if __name__ == "__main__":
    unittest.main()
