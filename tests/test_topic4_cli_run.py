# SPDX-License-Identifier: Apache-2.0
"""Operator-facing Topic 4 CLI integration, including a real offline delivery."""

import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOPIC4 = ROOT.parent / "Project_4-5_ContentToolRisk" / "课题五" / "finial"


class Topic4CliRunTests(unittest.TestCase):
    def invoke(
        self,
        out,
        config,
        *extra,
        entry_point="run",
        input_path="fixtures/smoke/openai_messages_smoke.json",
        extra_env=None,
    ):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        for key in ("TOPIC4_COMMAND", "TOPIC4_PROFILE", "TOPIC4_TIMEOUT"):
            env.pop(key, None)
        env["TOPIC4_FINIAL_ROOT"] = str(TOPIC4)
        env.update(extra_env or {})
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "trajectory_audit",
                entry_point,
                "--input",
                str(input_path),
                "--out",
                str(out),
                "--provider",
                "topic4-cli",
                "--topic4-config",
                str(config),
                *extra,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def test_provider_diagnostic_credentials_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "model.json"
            reference.write_text(
                '{"api_key":"SYNTHETIC-REFERENCE-KEY"}', encoding="utf-8"
            )
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "api_key": "SYNTHETIC-CONFIG-KEY",
                        "semantic": {"config_path": "model.json"},
                    }
                ),
                encoding="utf-8",
            )
            script = root / "provider.py"
            script.write_text(
                "import sys\nprint('SYNTHETIC-CONFIG-KEY SYNTHETIC-REFERENCE-KEY SYNTHETIC-ENV-KEY', file=sys.stderr)\nsys.exit(3)\n",
                encoding="utf-8",
            )
            completed = self.invoke(
                root / "runs",
                config,
                "--topic4-command",
                shlex.join([sys.executable, str(script)]),
                extra_env={"TOPIC4_MODEL_API_KEY": "SYNTHETIC-ENV-KEY"},
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            archive = Path(json.loads(completed.stderr)["details"]["archive_dir"])
            for artifact in archive.iterdir():
                text = artifact.read_text(encoding="utf-8")
                for credential in (
                    "SYNTHETIC-CONFIG-KEY",
                    "SYNTHETIC-REFERENCE-KEY",
                    "SYNTHETIC-ENV-KEY",
                ):
                    self.assertNotIn(credential, text)

    def test_invalid_utf8_output_is_retained_as_a_structured_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            script = root / "provider.py"
            script.write_text(
                "import pathlib, sys\npathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_bytes(b'\\xff')\n",
                encoding="utf-8",
            )
            completed = self.invoke(
                root / "runs",
                config,
                "--topic4-command",
                shlex.join([sys.executable, str(script)]),
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            error = json.loads(completed.stderr)
            self.assertEqual(
                error["details"]["original_failure"]["code"], "invalid_encoding"
            )
            self.assertEqual(
                (
                    Path(error["details"]["archive_dir"]) / "raw-output.json"
                ).read_bytes(),
                b"\xff",
            )

    def test_failed_provider_retains_archive_and_explicit_fallback_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text('{"scenario":"nonzero_exit"}', encoding="utf-8")
            command = shlex.join([sys.executable, "-m", "tools.fake_topic4_provider"])
            failed = self.invoke(
                root / "failed",
                config,
                "--topic4-command",
                command,
                "--topic4-profile",
                "strict-v1",
            )
            self.assertEqual(failed.returncode, 2, failed.stderr)
            error = json.loads(failed.stderr)
            archive = Path(error["details"]["archive_dir"])
            self.assertTrue((archive / "manifest.json").is_file())
            self.assertEqual(
                error["details"]["original_failure"]["code"], "nonzero_exit"
            )
            fallback = self.invoke(
                root / "fallback",
                config,
                "--topic4-command",
                command,
                "--topic4-profile",
                "strict-v1",
                "--allow-fallback",
            )
            self.assertEqual(fallback.returncode, 0, fallback.stderr)
            run = json.loads(
                (Path(json.loads(fallback.stdout)["run_dir"]) / "run.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(run["provider"]["id"], "deterministic-baseline")
            self.assertEqual(
                run["degraded"]["original_failure"]["code"], "nonzero_exit"
            )
            self.assertIn("topic4_archive", run["artifacts"])
            self.assertEqual(run["requested_provider"]["id"], "topic4-cli")
            self.assertNotEqual(
                run["audit_rules_sha256"], run["requested_audit_rules_sha256"]
            )
            retry = self.invoke(
                root / "fallback",
                config,
                "--topic4-command",
                command,
                "--topic4-profile",
                "strict-v1",
            )
            self.assertEqual(json.loads(retry.stderr)["code"], "topic4_provider_failed")

    @unittest.skipUnless(
        (TOPIC4 / "topic4-audit").is_file(), "Topic 4 delivery unavailable"
    )
    def test_bound_policy_is_sent_and_displayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": "audit-policy/1",
                        "kind": "audit-policy",
                        "policy_id": "topic4-policy-test",
                        "policy_version": "1",
                        "scope": "trajectory",
                        "sample_ids": ["openai-messages-smoke-001"],
                    }
                ),
                encoding="utf-8",
            )
            completed = self.invoke(
                root / "runs", TOPIC4 / "config/offline.json", "--policy", str(policy)
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            directory = Path(json.loads(completed.stdout)["run_dir"])
            audit = json.loads(
                (directory / "audit_result.json").read_text(encoding="utf-8")
            )
            exchanged = json.loads(
                (directory / "topic4/input-batch.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                exchanged["items"][0]["policy"]["policy_id"], "topic4-policy-test"
            )
            self.assertEqual(audit["policy"]["status"], "submitted_to_provider")
            self.assertIn(
                "topic4-policy-test",
                (directory / "index.html").read_text(encoding="utf-8"),
            )

    def test_batch_fallback_records_actual_provider_and_allows_strict_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text('{"scenario":"nonzero_exit"}', encoding="utf-8")
            source = root / "source.json"
            record = json.loads(
                (ROOT / "fixtures/smoke/openai_messages_smoke.json").read_text(
                    encoding="utf-8"
                )
            )
            source.write_text(
                json.dumps(
                    {
                        "schema_version": "source-batch/1",
                        "source": {"dataset_id": "smoke", "dataset_version": "1"},
                        "records": [record],
                    }
                ),
                encoding="utf-8",
            )
            args = (
                "--adapter",
                "openai-messages",
                "--topic4-command",
                shlex.join([sys.executable, "-m", "tools.fake_topic4_provider"]),
                "--topic4-profile",
                "strict-v1",
            )
            completed = self.invoke(
                root / "runs",
                config,
                *args,
                "--allow-fallback",
                entry_point="batch",
                input_path=source,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            batch = json.loads(
                (Path(summary["batch_dir"]) / "batch.json").read_text(encoding="utf-8")
            )
            self.assertEqual(batch["provider"]["id"], "deterministic-baseline")
            self.assertEqual(batch["requested_provider"]["id"], "topic4-cli")
            self.assertEqual(batch["provider_usage"], {"deterministic-baseline": 1})
            self.assertEqual(
                batch["degraded"]["samples"][0]["original_failure"]["code"],
                "nonzero_exit",
            )
            retry = self.invoke(
                root / "runs", config, *args, entry_point="batch", input_path=source
            )
            self.assertEqual(retry.returncode, 4, retry.stderr)
            self.assertEqual(
                json.loads(retry.stdout)["failures"][0]["code"],
                "topic4_provider_failed",
            )

    @unittest.skipUnless(
        (TOPIC4 / "topic4-audit").is_file(), "Topic 4 delivery unavailable"
    )
    def test_real_offline_batch_creates_sample_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = json.loads(
                (ROOT / "fixtures/smoke/openai_messages_smoke.json").read_text(
                    encoding="utf-8"
                )
            )
            source = root / "source-batch.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": "source-batch/1",
                        "source": {"dataset_id": "smoke", "dataset_version": "1"},
                        "records": [record, {**record, "sample_id": "second-smoke"}],
                    }
                ),
                encoding="utf-8",
            )
            completed = self.invoke(
                root / "runs",
                TOPIC4 / "config/offline.json",
                "--adapter",
                "openai-messages",
                entry_point="batch",
                input_path=source,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(len(summary["samples"]), 2)
            directory = Path(summary["batch_dir"])
            for sample in summary["samples"]:
                self.assertTrue(
                    (directory / sample["run_dir"] / "index.html").is_file()
                )

    @unittest.skipUnless(
        (TOPIC4 / "topic4-audit").is_file(), "Topic 4 delivery unavailable"
    )
    def test_native_evidence_cannot_point_to_a_different_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "provider.py"
            wrapper.write_text(
                "import json, pathlib, subprocess, sys\n"
                f"code = subprocess.call([sys.executable, {str(TOPIC4 / 'topic4-audit')!r}, *sys.argv[1:]])\n"
                "if code: sys.exit(code)\n"
                "path = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
                "result = json.loads(path.read_text(encoding='utf-8'))\n"
                "result['items'][0]['result']['findings'][0]['evidence'][0]['event_id'] = 'evt-0001'\n"
                "path.write_text(json.dumps(result), encoding='utf-8')\n",
                encoding="utf-8",
            )
            completed = self.invoke(
                root / "runs",
                TOPIC4 / "config/offline.json",
                "--topic4-command",
                shlex.join([sys.executable, str(wrapper)]),
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            error = json.loads(completed.stderr)
            self.assertEqual(
                error["details"]["original_failure"]["code"],
                "contract_validation_failed",
            )

    @unittest.skipUnless(
        (TOPIC4 / "topic4-audit").is_file(), "Topic 4 delivery unavailable"
    )
    def test_real_offline_provider_creates_review_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = self.invoke(tmp, TOPIC4 / "config/offline.json")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            directory = Path(summary["run_dir"])
            run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
            audit = json.loads(
                (directory / "audit_result.json").read_text(encoding="utf-8")
            )
            raw = json.loads(
                (directory / "topic4/raw-output.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run["provider"]["id"], "topic4-cli")
            self.assertEqual(
                audit["verdict"]["value"], raw["items"][0]["result"]["verdict"]
            )
            self.assertEqual(
                audit["provider_result"]["provider_run_id"], raw["provider_run_id"]
            )
            self.assertEqual(
                raw["provider"]["implementation"], "topic4-supplemental-offline"
            )
            self.assertEqual(audit["provider_result"]["profile"], "finial-1.0")
            self.assertTrue(audit["findings"])
            self.assertEqual(
                audit["findings"][0]["provider_native"],
                raw["items"][0]["result"]["findings"][0],
            )
            self.assertTrue(
                (directory / "topic4/strict-contract-issues.json").is_file()
            )
            self.assertIn(
                "topic4-cli", (directory / "index.html").read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
