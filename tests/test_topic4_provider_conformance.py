# SPDX-License-Identifier: Apache-2.0
"""通过子进程调用 conformance/run_suite（不 import 内部包）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConformanceSuiteTests(unittest.TestCase):
    def test_fake_provider_conformance_suite(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUTF8"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "conformance" / "run_suite.py"),
                "--provider-command",
                f"{sys.executable} -m tools.fake_topic4_provider",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(
                "conformance suite failed\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        summary = json.loads(completed.stdout)
        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["passed"], 20)


if __name__ == "__main__":
    unittest.main()
