# SPDX-License-Identifier: Apache-2.0
"""JSONL upload and archived review behavior at the workbench HTTP boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from httpx import Response

from fastapi.testclient import TestClient

from workbench.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SOURCE = ROOT / "acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl"
# Organizer answers are a test oracle only; the workbench never reads them.
REFERENCE = {
    row["trajectory_id"]: row["results"]
    for row in map(json.loads, (ROOT / "acceptance/saima-risk-visualization-sample-v1/raw/reference_answers.jsonl")
                   .read_text(encoding="utf-8").splitlines())
}


def without_label(item: dict) -> dict:
    return {key: value for key, value in item.items() if key != "指标"}


def records(count: int) -> bytes:
    return "\n".join(json.dumps({"trajectory_id": f"case-{i}", "messages": [
        {"role": "system", "content": "Stay in workspace"},
        {"role": "user", "content": "Read the report"},
        {"role": "assistant", "content": "Done"},
    ]}) for i in range(count)).encode()


class MessagesUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name)
        self.client = TestClient(create_app(storage_root=self.store))

    def upload(self, raw: bytes, **selection: str) -> Response:
        return self.client.post("/api/audit-sessions", files={"trajectory": ("samples.jsonl", raw)},
                                data={"adapter": "openai-messages", **selection})

    def test_public_five_reopen_source_and_competition_mapping(self) -> None:
        raw = PUBLIC_SOURCE.read_bytes()
        response = self.upload(raw)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([s["sample_id"] for s in body["samples"]],
                         ["s01", "p14", "s04", "science_agent_bench_66", "science_agent_bench_48"])
        self.assertEqual([len(s["bundle"]["source_mapping"]["steps"]) for s in body["samples"]], [7, 43, 22, 6, 24])
        for sample, line in zip(body["samples"], raw.splitlines()):
            self.assertTrue(sample["ok"])
            source = self.client.get(f"/api/audit-sessions/{body['session_id']}/samples/{sample['sample_id']}/source-trajectory").json()
            self.assertEqual(source["availability"]["status"], "available")
            self.assertEqual([step["raw"] for step in source["steps"]], json.loads(line)["messages"])
        self.assertEqual(self.client.get(f"/api/audit-sessions/{body['session_id']}").json(), body)
        self.assertEqual((self.store / "sessions" / body["session_id"] / "upload.jsonl").read_bytes(), raw)

    def test_public_five_statistics_match_reference(self) -> None:
        body = self.upload(PUBLIC_SOURCE.read_bytes()).json()
        for sample in body["samples"]:
            with self.subTest(sample=sample["sample_id"]):
                race = sample["bundle"]["race"]
                expected = REFERENCE[sample["sample_id"]]
                for item in ("A1", "A2", "A3"):
                    self.assertEqual(race[item], without_label(expected[item]), item)
                self.assertEqual(race["B1"]["absent_body_step_ids"], expected["B1"]["absent_body_step_ids"])
                self.assertEqual(
                    [{"step_id": s["step_id"], "text": s["text"],
                      **({"step_ids": s["step_ids"]} if len(s["step_ids"]) > 1 else {})}
                     for s in race["B1"]["segments"]],
                    expected["B1"]["segments"])
                self.assertEqual(
                    [{"step_id": c["step_id"], "tool_name": c["tool_name"], "arguments": c["arguments"],
                      "returns": [{"content": r["content"]} for r in c["returns"]]} for c in race["C1"]["calls"]],
                    expected["C1"]["calls"])
                self.assertEqual(race["C1"]["unmatched"], expected["C1"]["unmatched"])
                c4 = race["C4"]
                self.assertEqual(
                    {"missing_fields": c4["missing_fields"], "unmatched": c4["unmatched"],
                     "truncations": [{k: t[k] for k in ("step_id", "message_index", "quote")} for t in c4["truncations"]],
                     "completion": c4["completion"]},
                    without_label(expected["C4"]))

    def bundles_of(self, *rows: dict) -> list[dict]:
        response = self.upload("\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode())
        self.assertEqual(response.status_code, 200, response.text)
        return [sample["bundle"] for sample in response.json()["samples"]]

    def race_of(self, *rows: dict) -> list[dict]:
        return [bundle["race"] for bundle in self.bundles_of(*rows)]

    def test_tokens_keep_real_zero_and_thinking_counts_code_points_per_reply(self) -> None:
        call = lambda i: {"id": f"c{i}", "type": "function", "function": {"name": "Read", "arguments": "{}"}}
        thought = "a 😀\r\n中"
        zero_bundle, missing_bundle = self.bundles_of(
            {"trajectory_id": "zero", "usage": {"input_tokens": 0, "output_tokens": 0}, "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "reasoning_content": thought, "tool_calls": [call(1), call(2)]},
                {"role": "tool", "tool_call_id": "c1", "content": "x"},
                {"role": "tool", "tool_call_id": "c2", "content": "y"},
                {"role": "assistant", "content": "", "reasoning_content": thought, "tool_calls": [call(3)]},
                {"role": "tool", "tool_call_id": "c3", "content": "z"},
                {"role": "assistant", "content": "done", "reasoning_content": ""},
            ]},
            {"trajectory_id": "missing", "usage": {"input_tokens": None}, "messages": [
                {"role": "user", "content": "go"}, {"role": "assistant", "content": "done"}]},
        )
        zero, missing = zero_bundle["race"], missing_bundle["race"]
        thoughts = {e["event_id"]: e["source"]["json_path"] for e in zero_bundle["events"] if e["type"] == "thought"}
        self.assertEqual([thoughts[s["event_id"]] for s in zero["B1"]["segments"]],
                         ["messages[1].reasoning_content", "messages[4].reasoning_content"])
        self.assertEqual(zero["A1"], {"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(missing["A1"], {"input_tokens": "未提供", "output_tokens": "未提供"})
        self.assertEqual(zero["A2"], {"known_characters": 12, "absent_body_step_ids": [4]})
        self.assertEqual([(s["step_ids"], s["text"]) for s in zero["B1"]["segments"]],
                         [([1, 2], thought), ([3], thought)])
        self.assertEqual(zero["A3"], {"tool_calls": 3, "tool_kinds": 1, "tool_counts": {"Read": 3}})
        self.assertEqual(missing["A2"], {"known_characters": 0, "absent_body_step_ids": [1]})

    def test_returns_pair_by_reply_scope_without_overwriting_or_guessing(self) -> None:
        call = lambda i, name="bash": {"id": i, "type": "function", "function": {"name": name, "arguments": f'{{"n": "{i}"}}'}}
        (bundle,) = self.bundles_of({"trajectory_id": "pairs", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [call("a"), call("dup"), call("dup", "Bash")]},
            {"role": "tool", "tool_call_id": "a", "content": "first"},
            {"role": "tool", "tool_call_id": "a", "content": "second"},
            {"role": "tool", "tool_call_id": "dup", "content": "which?"},
            {"role": "tool", "content": "no id"},
            {"role": "tool", "tool_call_id": "ghost", "content": "orphan"},
            {"role": "assistant", "content": "", "tool_calls": [call("a"), call("silent")]},
            {"role": "tool", "tool_call_id": "a", "content": "again"},
        ]})
        race = bundle["race"]
        self.assertEqual([[r["output"] for r in item["results"]] for item in bundle["tool_interactions"]],
                         [["first", "second"], [], [], ["again"], []])
        calls = race["C1"]["calls"]
        self.assertEqual([(c["step_id"], c["tool_name"], [r["content"] for r in c["returns"]]) for c in calls],
                         [(1, "bash", ["first", "second"]), (2, "bash", []), (3, "Bash", []),
                          (4, "bash", ["again"]), (5, "bash", [])])
        self.assertEqual(calls[0]["arguments"], '{"n": "a"}')
        self.assertEqual(race["A3"]["tool_counts"], {"bash": 4, "Bash": 1})
        self.assertEqual([(u["message_index"], u.get("tool_call_id", "<absent>"), u["content"]) for u in race["C1"]["unmatched"]],
                         [(4, "dup", "which?"), (5, "<absent>", "no id"), (6, "ghost", "orphan")])
        self.assertNotIn("tool_call_id", race["C4"]["unmatched"][-2])
        self.assertEqual(len({u["issue"] for u in race["C1"]["unmatched"]}), 3)

    def test_integrity_separates_missing_null_and_legal_empty_values(self) -> None:
        def call(**fields):
            return {"type": "function", **fields}
        rows = [
            {"trajectory_id": "fields", "messages": [
                {"role": "system", "content": ""},
                {"role": "user"},
                {"role": "assistant", "content": None, "tool_calls": [
                    call(id="a", function={"name": "bash", "arguments": ""}),
                    call(function={"name": "", "arguments": None}),
                ]},
                {"role": "tool", "tool_call_id": "a", "content": None},
                {"role": "tool", "content": "id missing"},
                {"role": "assistant", "content": "head -n 5 out.log | sed -n 1,3p; tail shows ...[truncated 20 chars]",
                 "reasoning_content": "the output was truncated by head, as planned"},
            ]},
            {"trajectory_id": "done-false", "metadata": {"record": {"is_session_completed": False, "is_truncated": True},
                                                       "request": {"is_session_completed": True}},
             "messages": [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]},
            {"trajectory_id": "done-null", "metadata": {"record": {"is_session_completed": None}},
             "messages": [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]},
            {"trajectory_id": "request-only", "tools": [], "usage": {}, "metadata": {"request": {"is_session_completed": True}},
             "messages": [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]},
            {"trajectory_id": "call-only-reply", "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "tool_calls": [call(id="b", function={"name": "bash", "arguments": "{}"})]},
                {"role": "tool", "tool_call_id": "b", "content": "ok"}]},
        ]
        races = self.race_of(*rows)
        issues = races[0]["C5"]["issues"]
        self.assertEqual([(i["kind"], i["evidence"][0]["quote"]) for i in issues if i["message_index"] in {1, 4}], [
            ("missing_fields", '{"role": "user"}'),
            ("missing_fields", '{"role": "tool", "content": "id missing"}'),
            ("unmatched", "id missing"),
        ])
        self.assertIn(("unmatched", '{"type": "function", "function": {"name": "", "arguments": null}}'),
                      [(i["kind"], i["evidence"][0]["quote"]) for i in issues])
        self.assertEqual(races[1]["C5"]["issues"], [{"source_subitem": "C4", "kind": "truncations", "event_id": None,
                                                     "field": "metadata.record.is_truncated",
                                                     "evidence": [{"field": "metadata.record.is_truncated", "quote": "true"}]}])
        self.assertNotIn('"step_id": 0', json.dumps(races))
        fields, done_false, done_null, request_only, call_only = (race["C4"] for race in races)
        self.assertEqual(call_only["missing_fields"], [])
        self.assertEqual([(m.get("step_id"), m["message_index"], m["field"], m["issue"]) for m in fields["missing_fields"]], [
            (None, 1, "content", "缺失"),
            (2, 2, "tool_calls[1].id", "缺失"),
            (2, 2, "tool_calls[1].function.name", "为空字符串"),
            (2, 2, "tool_calls[1].function.arguments", "为 null"),
            (1, 3, "content", "为 null"),
            (None, 4, "tool_call_id", "缺失"),
        ])
        self.assertTrue(all(m["event_id"] for m in fields["missing_fields"]))
        self.assertEqual([(t["step_id"], t["message_index"], t["field"], t["quote"]) for t in fields["truncations"]],
                         [(3, 5, "content", "...[truncated 20 chars]")])
        self.assertEqual([(u["message_index"], u.get("step_id"), u["issue"]) for u in fields["unmatched"]],
                         [(2, 2, "未找到可确定的对应返回"), (4, None, "缺少 tool_call_id")])
        self.assertEqual(fields["completion"], "未提供")
        self.assertEqual(done_false["completion"], False)
        self.assertEqual(done_false["truncations"], [{"field": "metadata.record.is_truncated", "value": True}])
        self.assertIsNone(done_null["completion"])
        self.assertEqual(request_only, {"missing_fields": [], "unmatched": [], "truncations": [], "completion": "未提供"})

    def test_public_five_aggregate_and_issue_index_match_reference(self) -> None:
        body = self.upload(PUBLIC_SOURCE.read_bytes()).json()
        self.assertEqual(body["aggregate"], without_label(REFERENCE["aggregate"]["A4"]))
        for sample in body["samples"]:
            with self.subTest(sample=sample["sample_id"]):
                c5 = sample["bundle"]["race"]["C5"]
                expected = [issue for issue in REFERENCE[sample["sample_id"]]["C5"]["issues"]
                            if issue["source_subitem"] == "C4"]
                self.assertEqual([{k: issue[k] for k in ("source_subitem", "step_id", "message_index", "evidence")}
                                  for issue in c5["issues"]], expected)
                self.assertTrue(all(issue["event_id"] for issue in c5["issues"]))
                self.assertEqual([(p["source_subitem"], p["status"]) for p in c5["pending"]],
                                 [("C2", "unavailable"), ("C3", "unavailable")])
        self.assertEqual(self.client.get(f"/api/audit-sessions/{body['session_id']}").json(), body)

    def test_algorithm_items_map_fixed_provider_results_without_guessing(self) -> None:
        # Fixed fake-Provider contract results: verifies mapping only, not algorithm quality.
        row = lambda i: {"trajectory_id": f"t{i}", "messages": [
            {"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]}
        raw = "\n".join(json.dumps(row(i)) for i in range(2)).encode()
        races = {}
        for scenario in ("safe", "unsafe_located", "failed_all"):
            config = self.store / f"{scenario}.json"
            config.write_text(json.dumps({"scenario": scenario}), encoding="utf-8")
            self.client = TestClient(create_app(storage_root=self.store, default_topic4_config=config))
            races[scenario] = [s["bundle"]["race"] for s in self.upload(raw).json()["samples"]]
        self.assertEqual(races["safe"][0]["B2"], {"status": "available", "judgment": "无风险"})
        self.assertEqual(races["unsafe_located"][0]["B2"], {"status": "available", "judgment": "有风险"})
        for item in ("B3", "C2", "C3"):
            self.assertEqual(races["unsafe_located"][0][item]["status"], "unavailable")
            self.assertIn("算法尚未输出该项结果", races["unsafe_located"][0][item]["reason"])
        for race in races["failed_all"]:
            for item in ("B2", "B3", "C2", "C3"):
                self.assertEqual(race[item]["status"], "failure")
                self.assertNotIn("judgment", race[item])
            self.assertEqual([p["status"] for p in race["C5"]["pending"]], ["failure", "failure"])

    def test_thirty_records_default_full_and_explicit_subset(self) -> None:
        body = self.upload(records(30)).json()
        self.assertEqual(body["selection"], {"offset": 0, "limit": 30, "total_records": 30, "selected": 30})
        self.assertEqual(len(body["samples"]), 30)
        subset = self.upload(records(30), limit="3", offset="24").json()
        self.assertEqual(subset["selection"], {"offset": 24, "limit": 3, "total_records": 30, "selected": 3})
        self.assertEqual([s["sample_id"] for s in subset["samples"]], ["case-24", "case-25", "case-26"])
        self.assertEqual(subset["samples"][0]["bundle"]["source_mapping"]["events"][0]["line_number"], 25)

    def test_invalid_lines_encoding_and_duplicate_ids_reject_entire_file(self) -> None:
        for raw, code in [(b'\xff', "invalid_source"), (records(1) + b'\n{', "invalid_source_line"),
                          (records(1) + b'\n' + records(1), "duplicate_trajectory_id"),
                          (b'{}', "invalid_trajectory_id"), (b'', "empty_trajectory"),
                          (b'{"trajectory_id":"bad","messages":[]}', "invalid_source_record")]:
            with self.subTest(code=code):
                response = self.upload(raw, limit="1")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], code)
        self.assertFalse((self.store / "sessions").exists())

    def test_algorithm_failure_preserves_source_and_never_becomes_safe(self) -> None:
        self.client = TestClient(create_app(storage_root=self.store, topic4_command="/nonexistent/provider"))
        body = self.upload(records(2)).json()
        self.assertTrue(body["ok"])
        for sample in body["samples"]:
            self.assertFalse(sample["ok"])
            self.assertEqual(sample["bundle"]["verdict"]["value"], "undetermined")
            self.assertEqual(sample["bundle"]["algorithm_status"], "failure")
            self.assertIsNone(sample["bundle"]["metrics"]["audit"])
            source = self.client.get(f"/api/audit-sessions/{body['session_id']}/samples/{sample['sample_id']}/source-trajectory").json()
            self.assertEqual(len(source["steps"]), 3)
        self.assertEqual(self.client.get(f"/api/audit-sessions/{body['session_id']}").json(), body)

    def test_non_json_constants_rejected_even_outside_selection(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                raw = records(1) + b'\n' + ('{"trajectory_id":"bad","metadata":' + constant +
                    ',"messages":[{"role":"user","content":"hello"}]}').encode()
                response = self.upload(raw, limit="1")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_source_line")


if __name__ == "__main__":
    unittest.main()
