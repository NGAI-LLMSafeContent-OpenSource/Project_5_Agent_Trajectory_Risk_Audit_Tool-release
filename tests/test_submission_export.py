# SPDX-License-Identifier: Apache-2.0
"""Fixed public projections test assembly, not an unconfirmed Provider contract."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from trajectory_audit.artifacts import read_json, write_json
from workbench.api.app import create_app
from workbench.api.messages_evidence import archive_messages_evidence


def record(identity):
    def call(i):
        return {"id": str(i), "type": "function", "function": {"name": "Read", "arguments": '{"path":"report"}'}}
    return {"trajectory_id": identity, "usage": {"input_tokens": 0},
            "metadata": {"record": {"is_session_completed": None, "is_truncated": True}}, "messages": [
        {"role": "system", "content": "Read report only"},
        {"role": "assistant", "content": None, "reasoning_content": "思考 😀\n完整正文", "tool_calls": [call(1), call(2)]},
        {"role": "tool", "tool_call_id": "1", "content": "[output truncated]"},
        {"role": "tool", "tool_call_id": "2", "content": "timeout"},
        {"role": "tool", "tool_call_id": "unknown", "content": "unmatched"},
        {"role": "assistant", "content": "done"},
    ]}


class SubmissionExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name)
        self.sid = "a" * 32
        self.session_dir = self.store / "sessions" / self.sid
        self.session_dir.mkdir(parents=True)
        self.batch = "batch-" + "b" * 16
        retained = archive_messages_evidence(self.session_dir, {"records": [record(f"fixed-{i}") for i in range(5)]}, 0)
        self.paths, entries = [], []
        for i, (sample_id, item) in enumerate(retained.items()):
            bundle = item["bundle"]
            bundle.pop("algorithm_status")
            bundle["verdict"]["value"] = "safe"
            bundle["race"].update(B2={"status": "available", "judgment": "无风险"},
                                    B3={"status": "available", "risks": []},
                                    C2={"status": "available", "markers": []},
                                    C3={"status": "available", "failures": []})
            run_id = "run-" + f"{i:016x}"
            path = self.session_dir / Path(item["trajectory_path"]).parent / "review-bundle.json"
            write_json(path, bundle)
            self.paths.append(path)
            run_dir = self.session_dir / "runs" / self.batch / "samples" / run_id
            run_dir.mkdir(parents=True)
            write_json(run_dir / "review-bundle.json", bundle)
            entries.append({"sample_id": sample_id, "ok": True, "run_id": run_id,
                            "evidence_bundle": str(path.relative_to(self.session_dir)),
                            "trajectory_path": item["trajectory_path"]})
        write_json(self.session_dir / "runs" / self.batch / "batch.json", {"status": "complete"})
        write_json(self.session_dir / "audit-session.json", {"batch_id": self.batch, "samples": entries,
                   "selection": {"total_records": 5, "selected": 5, "offset": 0}})
        self.client = TestClient(create_app(storage_root=self.store))
        self.url = f"/api/audit-sessions/{self.sid}/submission.jsonl"

    def edit(self, fn):
        bundle = read_json(self.paths[0])
        fn(bundle)
        write_json(self.paths[0], bundle)

    def test_five_rows_plus_aggregate_template_and_repeat_without_provider(self):
        with patch("workbench.api.app.execute_batch", side_effect=AssertionError("rerun")), \
             patch("workbench.api.app.execute_run", side_effect=AssertionError("rerun")):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["x-audit-session-id"], self.sid)
            self.assertIn('filename="submission.jsonl"', response.headers["content-disposition"])
            reopened = TestClient(create_app(storage_root=self.store))
            self.assertEqual(reopened.get(self.url).content, response.content)
            self.assertEqual(reopened.get(self.url).headers["etag"], response.headers["etag"])
            page = reopened.get(f"/api/audit-sessions/{self.sid}").json()
        rows = [json.loads(line) for line in response.content.decode("utf-8").splitlines()]
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["trajectory_id"] for row in rows}), 6)
        template = read_json(Path(__file__).resolve().parents[1] / "acceptance/saima-risk-visualization-sample-v1/raw/submission_template.json")
        for row, sample in zip(rows, page["samples"]):
            results, race = row["results"], sample["bundle"]["race"]
            self.assertEqual(set(results), set(template[0]["results"]))
            for key in results:
                self.assertEqual(results[key]["指标"], template[0]["results"][key]["指标"])
            for key in ("A1", "A2", "A3", "C4"):
                expected = {k: v for k, v in results[key].items() if k != "指标"}
                if key != "C4":
                    self.assertEqual(expected, race[key])
            self.assertEqual(results["A1"], {"指标": template[0]["results"]["A1"]["指标"], "input_tokens": 0, "output_tokens": "未提供"})
            self.assertEqual(results["B1"]["segments"][0]["text"], "思考 😀\n完整正文")
            self.assertEqual(results["B1"]["segments"][0]["step_ids"], [1, 2])
            self.assertEqual(results["B3"]["risks"], [])
            self.assertEqual(results["C4"]["completion"], None)
            self.assertEqual(results["C1"]["calls"][1]["returns"][0]["content"], "timeout")
            self.assertTrue(results["C1"]["unmatched"])
            self.assertNotIn("event_id", json.dumps(results))
        self.assertEqual(set(rows[-1]["results"]), {"A4"})
        self.assertEqual({k: v for k, v in rows[-1]["results"]["A4"].items() if k != "指标"}, page["aggregate"])
        self.assertEqual(set(page["aggregate"]), {"trajectory_ids", "tool_calls", "tool_counts"})

    def assert_blocked(self, contains):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn("content-disposition", response.headers)
        self.assertEqual(response.json()["error"]["code"], "submission_unavailable")
        self.assertIn(contains, response.text)
        self.assertIn("fixed-0", response.text)

    def test_pending_contract_never_becomes_empty_findings(self):
        self.edit(lambda b: b["race"].update(B3={"status": "unavailable", "reason": "等待正式字段"}, C2={"status": "failure", "reason": "未完成"}))
        self.assert_blocked("等待正式字段")
        self.assertIn("C2", self.client.get(self.url).text)

    def test_missing_field_undetermined_and_fabricated_stats(self):
        original = read_json(self.paths[0])
        for mutate in (lambda b: b["race"]["C3"].pop("failures"),
                       lambda b: b["verdict"].update(value="undetermined"),
                       lambda b: b["race"]["A1"].update(input_tokens=999),
                       lambda b: b["race"]["C5"].update(issues=[]),
                       lambda b: b["sample"].update(sample_id="other")):
            write_json(self.paths[0], original)
            self.edit(mutate)
            self.assert_blocked("fixed-0")

    def risk(self, bundle):
        race = bundle["race"]
        call = race["C1"]["calls"][1]
        bundle["verdict"]["value"] = "unsafe"
        race["B2"]["judgment"] = "有风险"
        race["B3"]["risks"] = [{"event_id": call["event_id"], "step_id": 2,
            "risk_quote": '"path":"report"', "evidence": [{"message_index": 0, "quote": "Read report only"}]}]

    def test_explicit_first_behavior_with_supporting_constraint_and_c2_c3(self):
        def findings(bundle):
            self.risk(bundle)
            race = bundle["race"]
            row = {"step_id": 2, "evidence": [{"step_id": 2, "message_index": 3, "quote": "timeout"}]}
            race["C2"]["markers"] = [{**row, "previous_step_id": 1}]
            race["C3"]["failures"] = [{**row, "error_type": "timeout"}]
            race["C5"]["issues"].extend({"source_subitem": key, **r} for key, field in (("C2", "markers"), ("C3", "failures")) for r in race[key][field])
        self.edit(findings)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        result = json.loads(response.text.splitlines()[0])["results"]
        self.assertEqual(result["B3"]["risks"][0]["step_id"], 2)
        self.assertEqual(result["C3"]["failures"][0]["error_type"], "timeout")

    def test_invalid_quote_event_or_step_blocks_download(self):
        original = read_json(self.paths[0])
        for field, value in (("risk_quote", "fabricated"), ("event_id", "foreign-event"), ("step_id", 1)):
            write_json(self.paths[0], original)
            self.edit(self.risk)
            self.edit(lambda b: b["race"]["B3"]["risks"][0].update({field: value}))
            self.assert_blocked("fixed-0")

    def test_failed_sample_and_missing_source_report_identity(self):
        manifest_path = self.session_dir / "audit-session.json"
        manifest = read_json(manifest_path)
        manifest["samples"][0].update(ok=False, error={"message": "模型超时"})
        write_json(manifest_path, manifest)
        self.assert_blocked("模型超时")
        (self.session_dir / manifest["samples"][0]["trajectory_path"]).unlink()
        manifest["samples"][0]["ok"] = True
        write_json(manifest_path, manifest)
        self.assert_blocked("原文或映射无法读取")

    def test_raw_argument_and_return_json_keys_are_not_stripped_as_internal_fields(self):
        source_record = record
        def structured(identity):
            raw = source_record(identity)
            raw["messages"][1]["tool_calls"][0]["function"]["arguments"] = {"event_id": "user-data", "status": "new", "start": 0, "end": None}
            raw["messages"][2]["content"] = {"event_id": "source-return", "status": "ok", "start": 0}
            return raw
        with patch(f"{__name__}.record", side_effect=structured):
            fixture = SubmissionExportTests()
            fixture.setUp()
        try:
            response = fixture.client.get(fixture.url)
            self.assertEqual(response.status_code, 200, response.text)
            call = json.loads(response.text.splitlines()[0])["results"]["C1"]["calls"][0]
            self.assertEqual(call["arguments"], structured("fixed-0")["messages"][1]["tool_calls"][0]["function"]["arguments"])
            self.assertEqual(call["returns"][0]["content"], structured("fixed-0")["messages"][2]["content"])
        finally:
            fixture.doCleanups()

    def test_duplicate_identity_and_foreign_evidence_are_rejected(self):
        manifest_path = self.session_dir / "audit-session.json"
        manifest = read_json(manifest_path)
        manifest["samples"].append(manifest["samples"][0])
        write_json(manifest_path, manifest)
        self.assert_blocked("trajectory_id 重复")
        manifest["samples"].pop()
        write_json(manifest_path, manifest)
        self.edit(self.risk)
        self.edit(lambda b: b["race"]["B3"]["risks"][0]["evidence"][0].update(message_index=3))
        self.assert_blocked("quote 不属于指定事件")

    def test_missing_session(self):
        self.assertEqual(self.client.get("/api/audit-sessions/invalid/submission.jsonl").status_code, 404)


if __name__ == "__main__":
    unittest.main()
