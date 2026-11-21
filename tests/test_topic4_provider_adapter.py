# SPDX-License-Identifier: Apache-2.0
"""adapter 编排 seam 测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.adapter import run_topic4_adapter

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "fixtures/topic4_provider/single_input_batch.json"
MINIMAL = ROOT / "fixtures/topic4_provider/minimal_input_batch.json"
FAKE_CMD = [sys.executable, "-m", "tools.fake_topic4_provider"]


def _write_config(path: Path, scenario: str, **extra: object) -> Path:
    payload = {"scenario": scenario, "api_key": "SECRET-ADAPTER", **extra}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class AdapterTests(unittest.TestCase):
    def test_complete_run_archives_and_maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp) / "cfg.json", "unsafe_located")
            archive = Path(tmp) / "archive"
            outcome = run_topic4_adapter(
                input_path=SINGLE,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=archive,
                timeout=30,
            )
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.kind, "semantic")
            self.assertEqual(outcome.raw_result["status"], "complete")
            self.assertEqual(len(outcome.mapped["detected_findings"]), 1)
            self.assertTrue((archive / "manifest.json").is_file())
            sanitized = (archive / "config.sanitized.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("SECRET-ADAPTER", sanitized)

    def test_partial_preserves_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp) / "cfg.json", "partial_mixed")
            outcome = run_topic4_adapter(
                input_path=MINIMAL,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=Path(tmp) / "archive",
                timeout=30,
            )
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.raw_result["status"], "partial")
            self.assertEqual(len(outcome.mapped["success_items"]), 1)
            self.assertEqual(len(outcome.mapped["failure_items"]), 1)

    def test_transport_nonzero_not_semantic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp) / "cfg.json", "nonzero_exit")
            outcome = run_topic4_adapter(
                input_path=SINGLE,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=Path(tmp) / "archive",
                timeout=30,
            )
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.kind, "transport")
            self.assertEqual(outcome.failure["code"], "nonzero_exit")

    def test_contract_failure_on_bad_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp) / "cfg.json", "bad_schema_version")
            outcome = run_topic4_adapter(
                input_path=SINGLE,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=Path(tmp) / "archive",
                timeout=30,
            )
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.kind, "contract")
            self.assertTrue(outcome.validation_issues)

    def test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(
                Path(tmp) / "cfg.json", "timeout", sleep_seconds=5
            )
            outcome = run_topic4_adapter(
                input_path=SINGLE,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=Path(tmp) / "archive",
                timeout=0.5,
            )
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.failure["code"], "timeout")


if __name__ == "__main__":
    unittest.main()
