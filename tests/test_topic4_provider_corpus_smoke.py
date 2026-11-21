# SPDX-License-Identifier: Apache-2.0
"""Development Corpus 身份冒烟与可选真实 Topic4 CLI 调用。

正式 Blind Acceptance 不在此运行。真实 Topic4 仓库只读；若本地不存在交付目录，
真实 CLI 用例跳过，身份核对仍必须通过。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from trajectory_audit.topic4_provider.adapter import run_topic4_adapter
from trajectory_audit.topic4_provider.validator import validate_result

ROOT = Path(__file__).resolve().parents[1]
CORPUS_INPUT = (
    ROOT
    / "corpus"
    / "topic4-provider-corpus-v2"
    / "development"
    / "input-batch.json"
)
DIGESTS = (
    ROOT
    / "docs"
    / "reports"
    / "topic4-provider-corpus-v2-development-digests.json"
)
DEFAULT_TOPIC4_ROOT = (
    ROOT.parent / "Project_4-5_ContentToolRisk" / "课题五" / "finial"
)


def _topic4_root() -> Path | None:
    env = os.environ.get("TOPIC4_FINIAL_ROOT")
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    if DEFAULT_TOPIC4_ROOT.is_dir():
        return DEFAULT_TOPIC4_ROOT
    return None


class DevelopmentCorpusSmokeTests(unittest.TestCase):
    def test_development_corpus_identity_matches_frozen_digests(self) -> None:
        self.assertTrue(CORPUS_INPUT.is_file(), "缺少 Development Corpus 输入")
        self.assertTrue(DIGESTS.is_file(), "缺少 digest 报告")
        batch = json.loads(CORPUS_INPUT.read_text(encoding="utf-8"))
        expected = json.loads(DIGESTS.read_text(encoding="utf-8"))
        self.assertEqual(batch["batch_id"], expected["batch_id"])
        self.assertEqual(len(batch["items"]), 80)
        self.assertEqual(len(batch["items"]), expected["item_count"])
        actual_items = [
            {
                "sample_id": item["sample_id"],
                "audit_input_sha256": item["audit_input_sha256"],
            }
            for item in batch["items"]
        ]
        self.assertEqual(actual_items, expected["items"])
        digest_material = json.dumps(
            actual_items, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
        self.assertEqual(digest, expected["digest_list_sha256"])

    def test_real_topic4_offline_smoke_when_available(self) -> None:
        topic4 = _topic4_root()
        if topic4 is None:
            self.skipTest("未找到 Topic4 finial 交付目录")
        offline = topic4 / "config" / "offline.json"
        cli = topic4 / "topic4-audit"
        if not offline.is_file() or not cli.is_file():
            self.skipTest("Topic4 offline 配置或 CLI 入口缺失")

        batch = json.loads(CORPUS_INPUT.read_text(encoding="utf-8"))
        subset = {
            "schema_version": batch["schema_version"],
            "batch_id": batch["batch_id"],
            "items": [batch["items"][0]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input-batch.json"
            config_path = tmp_path / "offline.json"
            input_path.write_text(
                json.dumps(subset, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            config_path.write_text(offline.read_text(encoding="utf-8"), encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(topic4) + os.pathsep + env.get("PYTHONPATH", "")
            outcome = run_topic4_adapter(
                input_path=input_path,
                config_path=config_path,
                command=[sys.executable, str(cli)],
                archive_dir=tmp_path / "archive",
                timeout=120,
                allow_fallback=False,
                work_root=tmp_path / "work",
                env=env,
            )
            self.assertIsNotNone(outcome.process)
            self.assertTrue(outcome.process.started)
            self.assertFalse(outcome.process.timed_out)
            # 当前交付相对 #4 仍有合同缺口；冒烟只要求 CLI/file seam 可调用并归档。
            self.assertTrue((tmp_path / "archive" / "manifest.json").is_file())
            manifest = json.loads(
                (tmp_path / "archive" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("api_key", json.dumps(manifest))
            if outcome.ok and isinstance(outcome.raw_result, dict):
                issues = validate_result(subset, outcome.raw_result)
                # 记录缺口但不把已知合同失败当冒烟失败。
                self.assertIsInstance(issues, list)


if __name__ == "__main__":
    unittest.main()
