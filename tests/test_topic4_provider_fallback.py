# SPDX-License-Identifier: Apache-2.0
"""显式 fallback 边界测试：正式 Topic4 失败不得静默降级。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.adapter import run_topic4_adapter
from trajectory_audit.topic4_provider.provider_module import (
    PROVIDER_ID,
    provider_identity,
    register_provider_helpers,
)

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "fixtures/topic4_provider/single_input_batch.json"
FAKE_CMD = [sys.executable, "-m", "tools.fake_topic4_provider"]


class FallbackTests(unittest.TestCase):
    def test_no_silent_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps({"scenario": "nonzero_exit", "api_key": "x"}),
                encoding="utf-8",
            )
            outcome = run_topic4_adapter(
                input_path=SINGLE,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=Path(tmp) / "archive",
                timeout=30,
                allow_fallback=False,
                fallback_runner=lambda batch: {"should_not": "run"},
            )
            self.assertFalse(outcome.ok)
            self.assertIsNone(outcome.degraded)
            self.assertEqual(outcome.failure["code"], "nonzero_exit")

    def test_explicit_fallback_records_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps({"scenario": "bad_schema_version"}),
                encoding="utf-8",
            )

            def fallback(batch: dict) -> dict:
                return {
                    "provider": "deterministic-baseline",
                    "batch_id": batch.get("batch_id"),
                    "items": [{"sample_id": "sample-alpha", "status": "ok"}],
                }

            archive = Path(tmp) / "archive"
            outcome = run_topic4_adapter(
                input_path=SINGLE,
                config_path=cfg,
                command=FAKE_CMD,
                archive_dir=archive,
                timeout=30,
                allow_fallback=True,
                fallback_runner=fallback,
            )
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.kind, "fallback")
            self.assertIsNotNone(outcome.degraded)
            self.assertTrue(outcome.degraded["retained"])
            self.assertEqual(
                outcome.degraded["original_failure"]["code"],
                "contract_validation_failed",
            )
            self.assertEqual(
                outcome.mapped["degraded"]["from_provider"], "topic4-cli"
            )
            manifest = json.loads(
                (archive / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIsNotNone(manifest["degraded"])
            self.assertIsNotNone(manifest["failure"])

    def test_baseline_batch_fallback_runs_deterministic_rules(self) -> None:
        from trajectory_audit.topic4_provider.baseline_fallback import (
            run_baseline_batch_fallback,
        )

        batch = json.loads(SINGLE.read_text(encoding="utf-8"))
        result = run_baseline_batch_fallback(
            batch,
            degraded={"mode": "explicit_fallback", "retained": True},
        )
        self.assertEqual(result["provider"]["id"], "deterministic-baseline")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["status"], "success")
        self.assertIn(result["items"][0]["result"]["verdict"], {"safe", "unsafe", "undetermined"})

    def test_provider_registration_helper(self) -> None:
        registry: dict = {}
        import trajectory_audit.topic4_provider as mod

        register_provider_helpers(registry, mod)
        self.assertIn(PROVIDER_ID, registry)
        self.assertEqual(provider_identity()["id"], PROVIDER_ID)


if __name__ == "__main__":
    unittest.main()
