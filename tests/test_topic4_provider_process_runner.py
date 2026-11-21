# SPDX-License-Identifier: Apache-2.0
"""process_runner 公共 seam 测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.process_runner import run_command


class ProcessRunnerTests(unittest.TestCase):
    def test_captures_exit_stdout_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_command(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
                ],
                cwd=tmp,
                timeout=10,
            )
        self.assertTrue(result.started)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 3)
        self.assertIn("out", result.stdout)
        self.assertIn("err", result.stderr)

    def test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cwd=tmp,
                timeout=0.5,
            )
        self.assertTrue(result.started)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.error, "timeout")

    def test_command_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_command(
                ["/nonexistent/topic4-audit-xyz"],
                cwd=tmp,
                timeout=5,
            )
        self.assertFalse(result.started)
        self.assertIsNotNone(result.error)

    def test_isolated_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            result = run_command(
                [
                    sys.executable,
                    "-c",
                    "open('marker.txt','w',encoding='utf-8').write('ok')",
                ],
                cwd=tmp,
                timeout=10,
            )
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok")


if __name__ == "__main__":
    unittest.main()
