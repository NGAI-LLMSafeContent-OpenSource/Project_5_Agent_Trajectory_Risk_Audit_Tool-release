# SPDX-License-Identifier: Apache-2.0
"""run archiver 公共 seam 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.archiver import (
    archive_provider_run,
    load_archive_manifest,
)
from trajectory_audit.topic4_provider.types import ProcessRunResult

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "fixtures/topic4_provider/single_input_batch.json"


class ArchiverTests(unittest.TestCase):
    def test_archives_without_credential_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp) / "run"
            process = ProcessRunResult(
                argv=["python3", "-m", "tools.fake_topic4_provider"],
                cwd=tmp,
                exit_code=0,
                timed_out=False,
                stdout="ok",
                stderr="",
                duration_seconds=0.1,
                started=True,
            )
            manifest = archive_provider_run(
                archive_dir,
                input_path=SINGLE,
                config={
                    "scenario": "safe",
                    "api_key": "SECRET-VALUE-123",
                    "token": "tok",
                },
                config_path="provider-config.json",
                raw_output={
                    "schema_version": "topic4-audit-result/1",
                    "status": "complete",
                },
                process=process,
                provider_identity={
                    "implementation": "fake",
                    "revision": "1",
                },
                mapped={"detected_findings": []},
                failure=None,
            )
            self.assertTrue((archive_dir / "input-batch.json").is_file())
            self.assertTrue((archive_dir / "raw-output.json").is_file())
            self.assertTrue((archive_dir / "mapped.json").is_file())
            sanitized = json.loads(
                (archive_dir / "config.sanitized.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sanitized["api_key"], "[REDACTED]")
            self.assertEqual(sanitized["token"], "[REDACTED]")
            blob = (archive_dir / "config.sanitized.json").read_text(encoding="utf-8")
            self.assertNotIn("SECRET-VALUE-123", blob)
            loaded = load_archive_manifest(archive_dir)
            self.assertEqual(loaded["config_sha256"], manifest["config_sha256"])
            self.assertIsNotNone(loaded["command"])


if __name__ == "__main__":
    unittest.main()
