# SPDX-License-Identifier: Apache-2.0
"""Gate A: workbench API uploads ATBench/HINTBench and returns Review Bundle(s)."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ATBENCH_FIXTURE = ROOT / "fixtures/workbench/atbench_single_unsafe.json"
HINTBENCH_FIXTURE = ROOT / "fixtures/workbench/hintbench_single_risky.json"
ATBENCH_RAW_TINY = ROOT / "fixtures/workbench/atbench_raw_array_tiny.json"
HINTBENCH_RAW_TINY = ROOT / "fixtures/workbench/hintbench_raw_array_tiny.json"
UNLOCATED_CONFIG = ROOT / "fixtures/topic4_provider/fake_unsafe_unlocated_config.json"


def _ensure_repo_on_pythonpath() -> None:
    """Fake topic4 provider runs in an isolated cwd; it needs the repo root."""

    root = str(ROOT)
    current = os.environ.get("PYTHONPATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    if root not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])


class WorkbenchApiGateATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_repo_on_pythonpath()
        from workbench.api.app import create_app  # noqa: WPS433

        cls.create_app = staticmethod(create_app)

    def test_upload_atbench_returns_review_bundle(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            payload = ATBENCH_FIXTURE.read_bytes()
            response = client.post(
                "/api/audit-sessions",
                files={"trajectory": ("atbench_single_unsafe.json", payload, "application/json")},
                data={"adapter": "atbench"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["mode"], "single")
            self.assertEqual(len(body["samples"]), 1)
            bundle = body["bundle"]
            self.assertEqual(bundle["schema_version"], "review-bundle/1")
            self.assertEqual(bundle["verdict"]["value"], "unsafe")
            self.assertEqual(bundle["sample"]["source_format"], "atbench")
            self.assertTrue(bundle["detected_findings"])
            self.assertTrue(bundle["events"])
            for finding in bundle["detected_findings"]:
                self.assertNotIn(
                    "provider_native",
                    finding,
                    "Review Bundle must not leak topic4 provider_native shape",
                )
            self.assertIn("token_usage", bundle["availability"])
            self.assertEqual(bundle["availability"]["token_usage"]["status"], "unavailable")
            self.assertEqual(bundle["availability"]["key_tokens"]["status"], "unavailable")
            self.assertEqual(bundle["availability"]["thinking_patterns"]["status"], "unavailable")
            self.assertEqual(bundle["availability"]["provenance"]["status"], "unavailable")
            self.assertIsNone(bundle["annotations"]["key_tokens"])
            stored = list(Path(tmp).rglob("review-bundle.json"))
            self.assertGreaterEqual(len(stored), 1)
            disk = json.loads(stored[0].read_text(encoding="utf-8"))
            self.assertEqual(disk["schema_version"], "review-bundle/1")
            self.assert_reopens_from_archive(tmp, body)

    def test_upload_hintbench_returns_review_bundle(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            payload = HINTBENCH_FIXTURE.read_bytes()
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": ("hintbench_single_risky.json", payload, "application/json")
                },
                data={"adapter": "hintbench"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["mode"], "single")
            bundle = body["bundle"]
            self.assertEqual(bundle["schema_version"], "review-bundle/1")
            self.assertEqual(bundle["verdict"]["value"], "unsafe")
            self.assertEqual(bundle["sample"]["source_format"], "hintbench")
            self.assertEqual(bundle["sample"]["sample_id"], "slack_task_0008_risk_v7")
            self.assertTrue(bundle["detected_findings"])
            self.assertTrue(bundle["events"])
            for finding in bundle["detected_findings"]:
                self.assertNotIn(
                    "provider_native",
                    finding,
                    "Review Bundle must not leak topic4 provider_native shape",
                )
            self.assertEqual(bundle["availability"]["token_usage"]["status"], "unavailable")
            self.assertEqual(bundle["availability"]["key_tokens"]["status"], "unavailable")
            self.assertEqual(bundle["availability"]["thinking_patterns"]["status"], "unavailable")
            self.assertEqual(bundle["availability"]["provenance"]["status"], "unavailable")
            stored = list(Path(tmp).rglob("review-bundle.json"))
            self.assertGreaterEqual(len(stored), 1)
            disk = json.loads(stored[0].read_text(encoding="utf-8"))
            self.assertEqual(disk["sample"]["source_format"], "hintbench")

    def test_upload_raw_atbench_array_returns_batch_bundles(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            payload = ATBENCH_RAW_TINY.read_bytes()
            response = client.post(
                "/api/audit-sessions",
                files={"trajectory": ("atbench_raw_array_tiny.json", payload, "application/json")},
                data={"adapter": "atbench", "limit": "3", "offset": "0"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["mode"], "batch")
            self.assertEqual(
                body["selection"],
                {"offset": 0, "limit": 3, "total_records": 3, "selected": 3},
            )
            self.assertGreaterEqual(len(body["samples"]), 1)
            ok_samples = [sample for sample in body["samples"] if sample["ok"]]
            self.assertGreaterEqual(len(ok_samples), 1)
            for sample in ok_samples:
                bundle = sample["bundle"]
                self.assertEqual(bundle["schema_version"], "review-bundle/1")
                self.assertEqual(bundle["sample"]["source_format"], "atbench")
                for finding in bundle["detected_findings"]:
                    self.assertNotIn("provider_native", finding)
            self.assert_reopens_from_archive(tmp, body)

    def assert_reopens_from_archive(self, storage_root: str, body: dict) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(self.create_app(storage_root=Path(storage_root)))
        with patch("workbench.api.app.execute_run") as run, patch(
            "workbench.api.app.execute_batch"
        ) as batch:
            response = client.get(f"/api/audit-sessions/{body['session_id']}")
        run.assert_not_called()
        batch.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), body)

    def test_history_rejects_missing_or_invalid_session_ids(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(self.create_app(storage_root=Path(tmp)))
            for session_id in ("a" * 32, "not-a-session", "..%5Cother"):
                with self.subTest(session_id=session_id):
                    response = client.get(f"/api/audit-sessions/{session_id}")
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertEqual(response.json()["error"]["code"], "session_not_found")

    def test_history_preserves_batch_sample_failures(self) -> None:
        from fastapi.testclient import TestClient

        records = json.loads(ATBENCH_RAW_TINY.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(self.create_app(storage_root=Path(tmp)))
            response = client.post(
                "/api/audit-sessions",
                json={"adapter": "atbench", "trajectory": [records[0], None]},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual([sample["ok"] for sample in body["samples"]], [True, False])
            self.assert_reopens_from_archive(tmp, body)

    def test_upload_raw_array_respects_limit_and_offset(self) -> None:
        from fastapi.testclient import TestClient

        records = json.loads(ATBENCH_RAW_TINY.read_text(encoding="utf-8"))
        expected_id = records[1]["id"]
        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": (
                        "atbench_raw_array_tiny.json",
                        ATBENCH_RAW_TINY.read_bytes(),
                        "application/json",
                    )
                },
                data={"adapter": "atbench", "limit": "2", "offset": "1"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["mode"], "batch")
            self.assertEqual(
                body["selection"],
                {"offset": 1, "limit": 2, "total_records": 3, "selected": 2},
            )
            ok_samples = [sample for sample in body["samples"] if sample["ok"]]
            self.assertTrue(ok_samples)
            self.assertTrue(
                any(f"atbench-{expected_id}" == sample["sample_id"] for sample in ok_samples)
                or any(
                    sample["bundle"]["sample"]["sample_id"] == f"atbench-{expected_id}"
                    for sample in ok_samples
                )
            )

    def test_upload_hintbench_raw_array_uses_shared_batch_path(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": (
                        "hintbench_raw_array_tiny.json",
                        HINTBENCH_RAW_TINY.read_bytes(),
                        "application/json",
                    )
                },
                data={"adapter": "hintbench", "limit": "2", "offset": "0"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["mode"], "batch")
            self.assertEqual(body["selection"]["selected"], 2)
            ok_samples = [sample for sample in body["samples"] if sample["ok"]]
            self.assertTrue(ok_samples)
            self.assertEqual(ok_samples[0]["bundle"]["sample"]["source_format"], "hintbench")

    def test_limit_above_twenty_is_accepted(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": (
                        "atbench_raw_array_tiny.json",
                        json.dumps([
                            {**json.loads(ATBENCH_RAW_TINY.read_bytes())[0], "id": index}
                            for index in range(30)
                        ]).encode(),
                        "application/json",
                    )
                },
                data={"adapter": "atbench", "limit": "30"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["selection"]["limit"], 30)
            self.assertEqual(body["selection"]["selected"], 30)

    def test_located_finding_carries_link_fields_and_evidence(self) -> None:
        """关联字段存在时，审阅包必须带上可跳转的起源/证据定位信息。"""

        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(storage_root=Path(tmp))
            client = TestClient(app)
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": (
                        "atbench_single_unsafe.json",
                        ATBENCH_FIXTURE.read_bytes(),
                        "application/json",
                    )
                },
                data={"adapter": "atbench"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            findings = response.json()["bundle"]["detected_findings"]
            self.assertTrue(findings)
            finding = findings[0]
            self.assertTrue(finding["located"])
            self.assertTrue(finding["origin_event_ids"] or finding["affected_event_ids"])
            self.assertTrue(finding["evidence"])
            evidence = finding["evidence"][0]
            self.assertTrue(evidence["event_id"])
            self.assertTrue(evidence["excerpt"])
            self.assertIn("located", evidence)
            self.assertTrue(evidence["located"])
            event_ids = {event["event_id"] for event in response.json()["bundle"]["events"]}
            for event_id in finding["origin_event_ids"]:
                self.assertIn(event_id, event_ids)
            for event_id in finding["affected_event_ids"]:
                self.assertIn(event_id, event_ids)
            self.assertIn(evidence["event_id"], event_ids)

    def test_unlocated_finding_reports_empty_linkage(self) -> None:
        """定位失败时，审阅包必须如实给出 located=false 与空关联，而非静默缺字段。"""

        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(
                storage_root=Path(tmp),
                default_topic4_config=UNLOCATED_CONFIG,
            )
            client = TestClient(app)
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": (
                        "atbench_single_unsafe.json",
                        ATBENCH_FIXTURE.read_bytes(),
                        "application/json",
                    )
                },
                data={"adapter": "atbench"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["ok"])
            findings = body["bundle"]["detected_findings"]
            self.assertTrue(findings)
            finding = findings[0]
            self.assertFalse(finding["located"])
            self.assertEqual(finding["origin_event_ids"], [])
            self.assertEqual(finding["affected_event_ids"], [])
            self.assertEqual(finding["evidence"], [])

    def test_provider_failure_returns_structured_error_without_verdict(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            fail_cfg = Path(tmp) / "nonzero.json"
            fail_cfg.write_text(
                json.dumps({"scenario": "nonzero_exit", "api_key": "debug-only-not-a-real-secret"}),
                encoding="utf-8",
            )
            app = self.create_app(
                storage_root=Path(tmp) / "store",
                default_topic4_config=fail_cfg,
            )
            client = TestClient(app)
            payload = ATBENCH_FIXTURE.read_bytes()
            response = client.post(
                "/api/audit-sessions",
                files={"trajectory": ("atbench_single_unsafe.json", payload, "application/json")},
                data={"adapter": "atbench"},
            )
            self.assertIn(response.status_code, {400, 502, 503})
            body = response.json()
            self.assertFalse(body["ok"])
            self.assertIn("code", body["error"])
            self.assertIn("message", body["error"])
            self.assertNotIn("bundle", body)
            self.assertNotIn("verdict", body)

    def test_health(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(self.create_app(storage_root=Path(tmp)))
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["ok"], True)

    def test_deployment_environment_selects_real_provider_profile(self) -> None:
        """Deployment selects the real profile without changing local fake defaults."""

        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "TOPIC4_PROFILE": "finial-1.0",
                "WORKBENCH_ALLOW_PROVIDER_OVERRIDES": "false",
            },
        ):
            client = TestClient(self.create_app(storage_root=Path(tmp)))
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["provider"],
            {
                "id": "topic4-cli",
                "profile": "finial-1.0",
                "overrides_allowed": False,
            },
        )

    def test_deployment_rejects_request_provider_overrides(self) -> None:
        """Public uploads cannot replace the deployment-owned provider command."""

        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            app = self.create_app(
                storage_root=Path(tmp),
                allow_provider_overrides=False,
            )
            client = TestClient(app)
            response = client.post(
                "/api/audit-sessions",
                files={
                    "trajectory": (
                        "atbench_single_unsafe.json",
                        ATBENCH_FIXTURE.read_bytes(),
                        "application/json",
                    )
                },
                data={
                    "adapter": "atbench",
                    "topic4_command": "/tmp/caller-controlled-provider",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "provider_override_disabled")
        self.assertNotIn("bundle", response.json())


def _run_trajectories(storage_root: str) -> list[Path]:
    return [
        path
        for path in Path(storage_root).rglob("trajectory.json")
        if (path.parent / "review-bundle.json").is_file()
        and re.fullmatch(r"run-[0-9a-f]{16}", path.parent.name)
    ]


class WorkbenchSourceTrajectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_repo_on_pythonpath()
        from workbench.api.app import create_app

        cls.create_app = staticmethod(create_app)
    def test_hintbench_archive_projects_steps_and_shared_source_step(self) -> None:
        record = json.loads(HINTBENCH_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(tmp, HINTBENCH_FIXTURE, "hintbench_single_risky.json", "hintbench")
            sample_id = body["samples"][0]["sample_id"]
            projected = self._read_source_trajectory(tmp, body["session_id"], sample_id)

            self.assertEqual(projected["sample_id"], sample_id)
            self.assertEqual(projected["source_format"], "hintbench")
            self.assertEqual(projected["availability"], {"status": "available"})
            self.assertEqual([step["raw"] for step in projected["steps"]], record["trajectory"])
            self.assertEqual(projected["steps"][2]["json_path"], "trajectory[2]")
            self.assertEqual(projected["steps"][2]["event_ids"], ["evt-0003", "evt-0004"])
            self.assertEqual(projected["steps"][3]["json_path"], "trajectory[3]")
            self.assertEqual(projected["steps"][3]["event_ids"], ["evt-0005"])
            rendered = json.dumps(projected, ensure_ascii=False)
            self.assertNotIn("source_labels", rendered)
            self.assertNotIn("Goal Constraint", rendered)
            self.assertNotIn("state_tracking_failure", rendered)
            self.assertNotIn("is_risky", rendered)

    def test_atbench_archive_keeps_execution_turns_without_outer_metadata(self) -> None:
        record = json.loads(ATBENCH_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(tmp, ATBENCH_FIXTURE, "atbench_single_unsafe.json", "atbench")
            projected = self._read_source_trajectory(
                tmp, body["session_id"], body["samples"][0]["sample_id"]
            )

            self.assertEqual(projected["source_format"], "atbench")
            self.assertEqual(projected["availability"]["status"], "available")
            self.assertEqual(
                [step["json_path"] for step in projected["steps"]],
                ["contents[0][0]", "contents[0][1]", "contents[0][2]", "contents[0][3]"],
            )
            self.assertEqual([step["raw"] for step in projected["steps"]], record["contents"][0])
            self.assertEqual(
                [step["event_ids"] for step in projected["steps"]],
                [["evt-0001"], ["evt-0002"], ["evt-0003"], ["evt-0004"]],
            )
            rendered = json.dumps(projected, ensure_ascii=False)
            self.assertNotIn(record["reason"], rendered)
            self.assertNotIn(record["failure_mode"], rendered)
            self.assertNotIn(record["risk_source"], rendered)
            self.assertNotIn("Gets vault details by Name", rendered)

    def test_batch_reopen_returns_only_the_selected_sample(self) -> None:
        records = json.loads(HINTBENCH_RAW_TINY.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(
                tmp,
                HINTBENCH_RAW_TINY,
                "hintbench_raw_array_tiny.json",
                "hintbench",
                data={"adapter": "hintbench", "limit": "2", "offset": "0"},
            )
            self.assertEqual(body["mode"], "batch")
            ok_samples = [sample for sample in body["samples"] if sample["ok"]]
            self.assertEqual(len(ok_samples), 2)
            projections = [
                self._read_source_trajectory(tmp, body["session_id"], sample["sample_id"])
                for sample in ok_samples
            ]

            for record, projected in zip(records, projections, strict=True):
                self.assertEqual(projected["sample_id"], record["task_id"])
                self.assertEqual([step["raw"] for step in projected["steps"]], record["trajectory"])
            commerce = json.dumps(projections[0], ensure_ascii=False)
            crypto = json.dumps(projections[1], ensure_ascii=False)
            self.assertIn("Summer Collection", commerce)
            self.assertNotIn("Summer Collection", crypto)
            self.assertIn("emergencyWalletFreeze", crypto)
            self.assertNotIn("emergencyWalletFreeze", commerce)

    def test_missing_archived_original_reports_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(tmp, HINTBENCH_FIXTURE, "hintbench_single_risky.json", "hintbench")
            trajectories = _run_trajectories(tmp)
            self.assertEqual(len(trajectories), 1)
            trajectories[0].unlink()

            projected = self._read_source_trajectory(
                tmp, body["session_id"], body["samples"][0]["sample_id"]
            )
            session = self._reopen_session(tmp, body["session_id"])

            self.assertEqual(projected["availability"]["status"], "unavailable")
            self.assertEqual(projected["availability"]["reason"], "原始轨迹不可用")
            self.assertEqual(projected["steps"], [])
            self.assertNotIn("I'll start by inviting", json.dumps(projected, ensure_ascii=False))
            self.assertEqual(session["bundle"]["schema_version"], "review-bundle/1")

    def test_unreadable_archived_original_is_not_reported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(tmp, HINTBENCH_FIXTURE, "hintbench_single_risky.json", "hintbench")
            trajectories = _run_trajectories(tmp)
            self.assertEqual(len(trajectories), 1)
            trajectories[0].write_text("{", encoding="utf-8")

            projected = self._read_source_trajectory(
                tmp, body["session_id"], body["samples"][0]["sample_id"]
            )
            session = self._reopen_session(tmp, body["session_id"])

            self.assertEqual(projected["availability"]["status"], "unavailable")
            self.assertEqual(projected["availability"]["reason"], "原始轨迹读取失败")
            self.assertEqual(projected["steps"], [])
            self.assertEqual(session["bundle"]["schema_version"], "review-bundle/1")

    def test_event_without_reliable_source_path_stays_unlocated(self) -> None:
        record = json.loads(HINTBENCH_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(tmp, HINTBENCH_FIXTURE, "hintbench_single_risky.json", "hintbench")
            trajectory_path = _run_trajectories(tmp)[0]
            archived = json.loads(trajectory_path.read_text(encoding="utf-8"))
            archived["events"][0]["source"]["json_path"] = ""
            trajectory_path.write_text(json.dumps(archived), encoding="utf-8")

            projected = self._read_source_trajectory(
                tmp, body["session_id"], body["samples"][0]["sample_id"]
            )

            self.assertEqual(projected["availability"]["status"], "available")
            self.assertEqual([step["raw"] for step in projected["steps"]], record["trajectory"])
            self.assertEqual(projected["steps"][0]["event_ids"], [])
            self.assertEqual(projected["steps"][2]["event_ids"], ["evt-0003", "evt-0004"])
            located = [event_id for step in projected["steps"] for event_id in step["event_ids"]]
            self.assertNotIn("evt-0001", located)

    def test_unknown_sample_is_not_returned_from_another_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = self._upload(tmp, HINTBENCH_FIXTURE, "hintbench_single_risky.json", "hintbench")
            missing = self._read_source_trajectory(
                tmp, body["session_id"], "other-sample", expect_status=404
            )
            self.assertEqual(missing["error"]["code"], "sample_not_found")
            self.assertNotIn("steps", missing)

    def _upload(
        self,
        storage_root: str,
        fixture: Path,
        filename: str,
        adapter: str,
        data: dict[str, str] | None = None,
    ) -> dict:
        from fastapi.testclient import TestClient

        client = TestClient(self.create_app(storage_root=Path(storage_root)))
        response = client.post(
            "/api/audit-sessions",
            files={"trajectory": (filename, fixture.read_bytes(), "application/json")},
            data=data or {"adapter": adapter},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _read_source_trajectory(
        self,
        storage_root: str,
        session_id: str,
        sample_id: str,
        expect_status: int = 200,
    ) -> dict:
        from fastapi.testclient import TestClient

        client = TestClient(self.create_app(storage_root=Path(storage_root)))
        with patch("workbench.api.app.execute_run") as run, patch(
            "workbench.api.app.execute_batch"
        ) as batch:
            response = client.get(
                f"/api/audit-sessions/{session_id}/samples/{quote(sample_id, safe='')}/source-trajectory"
            )
        run.assert_not_called()
        batch.assert_not_called()
        self.assertEqual(response.status_code, expect_status, response.text)
        return response.json()

    def _reopen_session(self, storage_root: str, session_id: str) -> dict:
        from fastapi.testclient import TestClient

        client = TestClient(self.create_app(storage_root=Path(storage_root)))
        response = client.get(f"/api/audit-sessions/{session_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


if __name__ == "__main__":
    unittest.main()
