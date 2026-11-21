# SPDX-License-Identifier: Apache-2.0
"""Deployment contract for the Review Workbench systemd service."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts/server/deploy-review-workbench.sh"


class WorkbenchDeployTests(unittest.TestCase):
    def test_dry_run_pins_real_provider_in_systemd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            topic4_root = Path(tmp) / "topic4-finial"
            (topic4_root / "config").mkdir(parents=True)
            (topic4_root / "deployment/176").mkdir(parents=True)
            (topic4_root / "config/toolchain.json").write_text("{}\n", encoding="utf-8")
            (topic4_root / "deployment/176/activate.sh").write_text(
                "#!/usr/bin/env bash\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "TOPIC4_FINIAL_ROOT": str(topic4_root),
                "WORKBENCH_STORAGE_ROOT": str(Path(tmp) / "sessions"),
            }
            completed = subprocess.run(
                ["bash", str(DEPLOY_SCRIPT), "--dry-run", "--no-pull"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('Environment="TOPIC4_PROFILE=finial-1.0"', completed.stdout)
        self.assertIn(
            'Environment="WORKBENCH_ALLOW_PROVIDER_OVERRIDES=false"',
            completed.stdout,
        )
        self.assertIn('Environment="CUDA_VISIBLE_DEVICES="', completed.stdout)
        self.assertIn(
            f'Environment="TOPIC4_CONFIG={topic4_root / "config/toolchain.json"}"',
            completed.stdout,
        )
        self.assertIn(f"WorkingDirectory={ROOT}", completed.stdout)
        self.assertNotIn('WorkingDirectory="', completed.stdout)
        self.assertIn("scripts/server/run-topic4-cli.sh", completed.stdout)


if __name__ == "__main__":
    unittest.main()
