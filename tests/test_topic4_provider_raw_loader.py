# SPDX-License-Identifier: Apache-2.0
"""raw_loader 公共 seam 测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.raw_loader import (
    RawLoadError,
    load_raw_json,
    save_raw_json,
    save_raw_text,
)


class RawLoaderTests(unittest.TestCase):
    def test_roundtrip_preserves_unicode(self) -> None:
        payload = {"text": "备份已删除", "nested": {"ok": True}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            save_raw_json(path, payload)
            loaded = load_raw_json(path)
        self.assertEqual(loaded, payload)

    def test_missing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RawLoadError) as ctx:
                load_raw_json(Path(tmp) / "missing.json")
        self.assertEqual(ctx.exception.code, "missing_output")

    def test_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            save_raw_text(path, "{not-json")
            with self.assertRaises(RawLoadError) as ctx:
                load_raw_json(path)
        self.assertEqual(ctx.exception.code, "invalid_json")


if __name__ == "__main__":
    unittest.main()
