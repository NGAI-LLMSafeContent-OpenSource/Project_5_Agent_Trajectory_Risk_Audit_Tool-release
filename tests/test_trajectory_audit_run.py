# SPDX-License-Identifier: Apache-2.0
"""Acceptance tests for the OpenAI messages audit slice.

These tests only drive the public run entry point (``python -m
trajectory_audit``) and assert observable artifacts, never internal helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMOKE_INPUT = PROJECT_ROOT / "fixtures" / "smoke" / "openai_messages_smoke.json"
PROVIDER_ID = "deterministic-baseline"
RUN_ARTIFACTS = (
    "run.json",
    "input_manifest.json",
    "trajectory.json",
    "audit_result.json",
    "metrics.json",
    "index.html",
)


def invoke_cli(*args: object) -> subprocess.CompletedProcess[str]:
    """Run the public CLI in a subprocess with the project importable."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "trajectory_audit", *(str(arg) for arg in args)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_input(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class PublicRunEntryPointTest(unittest.TestCase):
    def run_smoke(self, out_root: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = invoke_cli(
            "run", "--input", SMOKE_INPUT, "--out", out_root, "--provider", PROVIDER_ID
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr.strip(), "")
        return completed, json.loads(completed.stdout)

    def test_smoke_input_builds_complete_run_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed, result = self.run_smoke(Path(tmp) / "runs")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["check_status"], "findings")
            self.assertEqual(result["verdict"], "unsafe")

            run_dir = Path(result["run_dir"])
            self.assertTrue(run_dir.is_dir())
            self.assertEqual(run_dir.name, result["run_id"])
            for name in RUN_ARTIFACTS:
                self.assertTrue((run_dir / name).is_file(), f"missing {name}")

            run = read_json(run_dir / "run.json")
            self.assertEqual(run["run_id"], result["run_id"])
            self.assertEqual(run["status"], "complete")
            self.assertEqual(run["sample"]["sample_id"], "openai-messages-smoke-001")
            self.assertEqual(run["sample"]["source_format"], "openai_messages")
            self.assertEqual(run["provider"]["id"], PROVIDER_ID)
            self.assertEqual(run["provider"]["selection"], "explicit")

            for artifact_key, relative_path in run["artifacts"].items():
                self.assertTrue(
                    (run_dir / relative_path).is_file(),
                    f"run.json artifact {artifact_key} -> {relative_path} is missing",
                )

            input_manifest = read_json(run_dir / "input_manifest.json")
            digest = hashlib.sha256(SMOKE_INPUT.read_bytes()).hexdigest()
            self.assertEqual(len(input_manifest["files"]), 1)
            entry = input_manifest["files"][0]
            self.assertEqual(entry["sha256"], digest)
            self.assertEqual(entry["bytes"], SMOKE_INPUT.stat().st_size)
            self.assertEqual((run_dir / entry["stored_path"]).read_bytes(), SMOKE_INPUT.read_bytes())

            trajectory = read_json(run_dir / "trajectory.json")
            metrics = read_json(run_dir / "metrics.json")
            self.assertEqual(metrics["events"]["total"], len(trajectory["events"]))
            self.assertEqual(metrics["audit"]["check_status"], "findings")
            self.assertEqual(metrics["run_id"], result["run_id"])

            audit = read_json(run_dir / "audit_result.json")
            self.assertEqual(audit["run_id"], result["run_id"])
            self.assertEqual(audit["sample_id"], trajectory["sample"]["sample_id"])
            self.assertEqual(audit["provider"]["id"], PROVIDER_ID)
            self.assertEqual(audit["provider"]["version"], run["provider"]["version"])
            self.assertEqual(audit["verdict"]["value"], "unsafe")
            self.assertEqual(audit["verdict"]["basis"], "rule_violation")
            self.assertEqual(audit["checks"]["status"], "findings")
            self.assertGreaterEqual(len(audit["findings"]), 1)

    def test_findings_carry_scope_evidence_category_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, result = self.run_smoke(Path(tmp) / "runs")
            run_dir = Path(result["run_dir"])
            audit = read_json(run_dir / "audit_result.json")
            trajectory = read_json(run_dir / "trajectory.json")
            events_by_id = {event["event_id"]: event for event in trajectory["events"]}

            for finding in audit["findings"]:
                self.assertIn(finding["scope"], {"trajectory", "event", "span"})
                self.assertIn("tool_error", finding["categories"])
                self.assertTrue(finding["reason"])
                self.assertTrue(finding["origin_event_ids"])
                self.assertTrue(finding["evidence"])
                for event_id in finding["origin_event_ids"]:
                    self.assertIn(event_id, events_by_id)
                    self.assertEqual(events_by_id[event_id]["type"], "tool_call")

                evidence = finding["evidence"][0]
                self.assertIn(
                    evidence["kind"],
                    {"trace_excerpt", "tool_fact", "policy_clause", "source_rationale"},
                )
                self.assertEqual(evidence["event_id"], finding["origin_event_ids"][0])
                self.assertEqual(evidence["json_path"], events_by_id[evidence["event_id"]]["source"]["json_path"])
                self.assertTrue(evidence["field_path"])

    def test_every_normalized_event_keeps_identity_and_source_locator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, first = self.run_smoke(Path(tmp) / "runs-a")
            _, second = self.run_smoke(Path(tmp) / "runs-b")
            first_events = read_json(Path(first["run_dir"]) / "trajectory.json")["events"]
            second_events = read_json(Path(second["run_dir"]) / "trajectory.json")["events"]

            event_ids = [event["event_id"] for event in first_events]
            self.assertEqual(len(event_ids), len(set(event_ids)))
            self.assertEqual(
                [event["sequence"] for event in first_events], list(range(1, len(first_events) + 1))
            )

            for event in first_events:
                self.assertIn(
                    event["type"],
                    {"message", "thought", "tool_call", "tool_result", "state_change", "unsupported_content"},
                )
                self.assertTrue(event["event_id"].startswith("evt-"))
                self.assertIn("raw", event)
                source = event["source"]
                self.assertIsInstance(source["record_index"], int)
                self.assertRegex(source["json_path"], r"^messages\[\d+\]")
                self.assertRegex(source["record_sha256"], r"^[0-9a-f]{64}$")

            self.assertEqual(event_ids, [event["event_id"] for event in second_events])
            self.assertEqual(first_events, second_events)

            split_paths = [
                event["source"]["json_path"]
                for event in first_events
                if event["type"] == "tool_call"
            ]
            self.assertTrue(any(re.search(r"\.tool_calls\[\d+\]$", path) for path in split_paths))

    def test_unsupported_content_is_recorded_instead_of_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, result = self.run_smoke(Path(tmp) / "runs")
            trajectory = read_json(Path(result["run_dir"]) / "trajectory.json")
            unsupported = [
                event for event in trajectory["events"] if event["type"] == "unsupported_content"
            ]
            self.assertEqual(len(unsupported), 1)
            event = unsupported[0]
            self.assertEqual(event["content"]["part_type"], "image_url")
            self.assertTrue(event["content"]["reason"])
            self.assertEqual(event["raw"]["type"], "image_url")
            self.assertEqual(trajectory["unsupported"]["event_ids"], [event["event_id"]])

    def test_unknown_message_field_and_top_level_field_are_preserved(self) -> None:
        payload = {
            "sample_id": "vendor-extension-001",
            "vendor_metadata": {"collector": "vendor-x", "version": 3},
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi", "vendor_extension": {"span_id": "s-1"}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = write_input(Path(tmp) / "vendor.json", payload)
            out_root = Path(tmp) / "runs"
            completed = invoke_cli(
                "run", "--input", input_path, "--out", out_root, "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["run_dir"])
            trajectory = read_json(run_dir / "trajectory.json")
            preserved = [
                event
                for event in trajectory["events"]
                if event["type"] == "unsupported_content"
                and event["content"]["reason"] == "unmapped_message_field"
            ]
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0]["source"]["json_path"], "messages[1].vendor_extension")
            self.assertEqual(preserved[0]["raw"], {"span_id": "s-1"})

            manifest = read_json(run_dir / "input_manifest.json")
            self.assertEqual(manifest["unmapped_top_level_fields"], ["vendor_metadata"])
            self.assertEqual(
                trajectory["sample"]["unmapped_top_level_fields"], ["vendor_metadata"]
            )

    def test_unsupported_only_message_does_not_report_missing_content(self) -> None:
        payload = {
            "sample_id": "image-only-001",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "inline://only-image"}}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = write_input(Path(tmp) / "image_only.json", payload)
            completed = invoke_cli(
                "run", "--input", input_path, "--out", Path(tmp) / "runs", "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            trajectory = read_json(
                Path(json.loads(completed.stdout)["run_dir"]) / "trajectory.json"
            )
            reasons = [
                event["content"]["reason"]
                for event in trajectory["events"]
                if event["type"] == "unsupported_content"
            ]
            self.assertEqual(reasons, ["unsupported_content_part"])

    def test_tool_results_link_only_to_a_preceding_call(self) -> None:
        payload = {
            "sample_id": "link-order-001",
            "messages": [
                {"role": "user", "content": "run the tool"},
                {"role": "tool", "tool_call_id": "call_x", "content": "early result"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_x", "content": "late result"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = write_input(Path(tmp) / "link_order.json", payload)
            completed = invoke_cli(
                "run", "--input", input_path, "--out", Path(tmp) / "runs", "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["run_dir"])
            trajectory = read_json(run_dir / "trajectory.json")
            calls = [
                event for event in trajectory["events"] if event["type"] == "tool_call"
            ]
            results = [
                event for event in trajectory["events"] if event["type"] == "tool_result"
            ]
            self.assertEqual(len(calls), 2)
            self.assertEqual(results[0]["content"]["link_status"], "unknown_call_id")
            self.assertIsNone(results[0]["content"]["call_event_id"])
            # 最近的、位于结果之前的那次调用优先。
            self.assertEqual(results[1]["content"]["call_event_id"], calls[1]["event_id"])

            metrics = read_json(run_dir / "metrics.json")
            self.assertEqual(metrics["tools"]["unpaired_call_event_ids"], [calls[0]["event_id"]])

    def test_run_manifest_records_identity_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, result = self.run_smoke(Path(tmp) / "runs")
            run = read_json(Path(result["run_dir"]) / "run.json")
            self.assertRegex(run["audit_rules_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(run["adapter"]["id"], "openai-messages")
            self.assertEqual(run["adapter"]["version"], run["provider"]["version"])



    def test_completed_run_identity_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"
            _, first = self.run_smoke(out_root)
            run_dir = Path(first["run_dir"])
            before = {
                path.relative_to(run_dir): path.read_bytes()
                for path in run_dir.rglob("*")
                if path.is_file()
            }

            completed = invoke_cli(
                "run", "--input", SMOKE_INPUT, "--out", out_root, "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 3, completed.stderr)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "run_already_exists")
            self.assertEqual(error["details"]["run_id"], first["run_id"])

            after = {
                path.relative_to(run_dir): path.read_bytes()
                for path in run_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_clean_trajectory_is_undetermined_with_no_findings(self) -> None:
        payload = {
            "sample_id": "clean-sample-001",
            "messages": [
                {"role": "user", "content": "Summarize the meeting notes."},
                {"role": "assistant", "content": "Here is a summary."},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = write_input(Path(tmp) / "clean.json", payload)
            out_root = Path(tmp) / "runs"
            completed = invoke_cli(
                "run", "--input", input_path, "--out", out_root, "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["verdict"], "undetermined")
            self.assertEqual(result["check_status"], "no_findings")

            run_dir = Path(result["run_dir"])
            audit = read_json(run_dir / "audit_result.json")
            self.assertEqual(audit["verdict"]["value"], "undetermined")
            self.assertEqual(audit["verdict"]["basis"], "no_source_truth")
            self.assertEqual(audit["checks"]["status"], "no_findings")
            self.assertEqual(audit["findings"], [])

            page = (run_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn('data-verdict="undetermined"', page)
            self.assertIn('data-check-status="no_findings"', page)
            self.assertIn("no_findings", page)

    def test_invalid_input_fails_with_machine_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"

            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            completed = invoke_cli(
                "run", "--input", broken, "--out", out_root, "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout.strip(), "")
            self.assertEqual(json.loads(completed.stderr)["code"], "input_not_json")
            self.assertFalse(out_root.exists())

            wrong_shape = write_input(Path(tmp) / "wrong_shape.json", {"messages": "nope"})
            completed = invoke_cli(
                "run", "--input", wrong_shape, "--out", out_root, "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 2)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "input_invalid")
            self.assertEqual(error["details"]["issues"][0]["path"], "messages")

            missing_role = write_input(
                Path(tmp) / "missing_role.json", {"messages": [{"content": "hello"}]}
            )
            completed = invoke_cli(
                "run", "--input", missing_role, "--out", out_root, "--provider", PROVIDER_ID
            )
            self.assertEqual(completed.returncode, 2)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "input_invalid")
            self.assertEqual(error["details"]["issues"][0]["path"], "messages[0].role")
            self.assertFalse(out_root.exists())

    def test_provider_must_be_explicitly_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"
            completed = invoke_cli("run", "--input", SMOKE_INPUT, "--out", out_root)
            self.assertEqual(completed.returncode, 2)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "missing_provider")
            self.assertFalse(out_root.exists())

            completed = invoke_cli(
                "run", "--input", SMOKE_INPUT, "--out", out_root, "--provider", "topic4-remote"
            )
            self.assertEqual(completed.returncode, 2)
            error = json.loads(completed.stderr)
            self.assertEqual(error["code"], "unknown_provider")
            self.assertIn("deterministic-baseline", error["details"]["available"])
            self.assertFalse(out_root.exists())

    def test_review_page_is_self_contained_and_shows_run_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, result = self.run_smoke(Path(tmp) / "runs")
            run_dir = Path(result["run_dir"])
            page = (run_dir / "index.html").read_text(encoding="utf-8")
            trajectory = read_json(run_dir / "trajectory.json")
            audit = read_json(run_dir / "audit_result.json")

            self.assertIn(result["run_id"], page)
            self.assertIn("openai-messages-smoke-001", page)
            self.assertIn(PROVIDER_ID, page)
            self.assertIn("openai_messages", page)
            self.assertIn("read_file", page)
            self.assertIn("archive_file", page)
            self.assertIn("unsupported_content", page)
            self.assertIn('data-verdict="unsafe"', page)

            for event in trajectory["events"]:
                self.assertIn(f'id="{event["event_id"]}"', page)
            for finding in audit["findings"]:
                self.assertIn(finding["finding_id"], page)
                self.assertIn(finding["categories"][0], page)
                self.assertIn(f'href="#{finding["origin_event_ids"][0]}"', page)

            self.assertNotRegex(page, r'(?:src|href)\s*=\s*["\']https?://')
            self.assertNotIn("<script src", page)
            self.assertNotIn("<link", page)
            self.assertNotIn("@import", page)
            self.assertNotIn("fetch(", page)
            self.assertNotIn("XMLHttpRequest", page)

            self.assertIn('id="audit-result-data"', page)
            embedded = re.search(
                r'<script type="application/json" id="audit-result-data">(.*?)</script>',
                page,
                re.DOTALL,
            )
            self.assertIsNotNone(embedded)
            self.assertEqual(json.loads(embedded.group(1)), audit)


if __name__ == "__main__":
    unittest.main()
