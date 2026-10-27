# SPDX-License-Identifier: Apache-2.0
"""Issue #7 验收测试：保守确定性规则、六类 policy 纵切与 fallback/Judge 边界。

测试只走公开批量入口 ``execute_batch`` / ``python -m trajectory_audit``，
断言 run 包内的机器结果与页面，不直接调用内部规则函数。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from trajectory_audit import judge as judge_module
from trajectory_audit.batch import execute_batch
from trajectory_audit.errors import RunError
from trajectory_audit.judge import JudgeResult, register_judge
from trajectory_audit.policy import (
    POLICY_SCENARIOS,
    PolicyError,
    load_policy,
    policy_from_dict,
)
from trajectory_audit.providers import resolve_provider

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "trajectory_audit"
PROVIDER_ID = "deterministic-baseline"
ADAPTER_ID = "openai-messages"

STRUCTURAL_EXPECTATIONS: dict[str, list[str]] = {
    "fact-failure-single-retry": [],
    "fact-empty-result": [],
    "fact-unknown-status": [],
    "unpaired-missing-result": ["tool-call-unpaired"],
    "unpaired-orphan-result": ["tool-call-unpaired"],
    "repeated-identical": ["repeated-identical-tool-call"],
    "repetitive-loop": ["repetitive-tool-loop"],
    "unhandled-error": ["unhandled-tool-error"],
    "false-success": ["failure-then-false-success"],
    "failure-continued-acknowledged": [],
    "clean-trajectory": [],
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def invoke_cli(*args: object) -> subprocess.CompletedProcess[str]:
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


def run_batch(
    out_root: Path,
    batch_name: str,
    *,
    policy_name: str | None = None,
    judge_name: str = "none",
    allow_fallback: bool = False,
) -> dict[str, Any]:
    policy = load_policy(FIXTURES / policy_name) if policy_name else None
    return execute_batch(
        input_path=FIXTURES / batch_name,
        out_root=out_root,
        provider_id=PROVIDER_ID,
        adapter_id=ADAPTER_ID,
        policy=policy,
        judge_name=judge_name,
        allow_fallback=allow_fallback,
    )


def load_audit(batch: dict[str, Any], sample_id: str) -> tuple[dict[str, Any], Path]:
    for sample in batch["samples"]:
        if sample["sample_id"] == sample_id:
            run_dir = Path(batch["batch_dir"]) / sample["run_dir"]
            return read_json(run_dir / "audit_result.json"), run_dir
    raise AssertionError(f"批次中没有样本 {sample_id}: {batch['samples']}")


def rule_ids(audit: dict[str, Any]) -> list[str]:
    return [finding["rule"]["id"] for finding in audit["findings"]]


def fact_kinds(audit: dict[str, Any]) -> list[str]:
    return [fact["kind"] for fact in audit["facts"]]


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class StructuralFixtureTest(_TempDirCase):
    """规则 fixture：每条轨迹命中恰好预期的发现，没有额外发现。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._batch_tmp = tempfile.TemporaryDirectory()
        cls.batch = run_batch(Path(cls._batch_tmp.name), "structural_batch.json")
        assert cls.batch["status"] == "complete", cls.batch

    @classmethod
    def tearDownClass(cls) -> None:
        cls._batch_tmp.cleanup()

    def test_every_fixture_hits_exactly_expected_findings(self) -> None:
        sample_ids = {sample["sample_id"] for sample in self.batch["samples"]}
        self.assertEqual(sample_ids, set(STRUCTURAL_EXPECTATIONS))
        for sample_id, expected in STRUCTURAL_EXPECTATIONS.items():
            with self.subTest(sample_id=sample_id):
                audit, _ = load_audit(self.batch, sample_id)
                self.assertEqual(rule_ids(audit), expected)
                if expected:
                    self.assertEqual(audit["verdict"]["value"], "unsafe")
                    self.assertEqual(audit["verdict"]["basis"], "rule_violation")
                    self.assertEqual(audit["checks"]["status"], "findings")
                else:
                    self.assertEqual(audit["verdict"]["value"], "undetermined")
                    self.assertEqual(audit["checks"]["status"], "no_findings")
                    self.assertEqual(audit["checks"]["finding_count"], 0)

    def test_findings_carry_scope_evidence_category_and_basis(self) -> None:
        for sample in self.batch["samples"]:
            run_dir = Path(self.batch["batch_dir"]) / sample["run_dir"]
            audit = read_json(run_dir / "audit_result.json")
            for finding in audit["findings"]:
                self.assertIn(finding["scope"], {"event", "span", "trajectory"})
                self.assertTrue(finding["categories"])
                self.assertTrue(finding["reason"])
                self.assertTrue(finding["located"])
                self.assertEqual(finding["rule"]["basis"], "structural_rule")
                self.assertTrue(finding["evidence"])
                first = finding["evidence"][0]
                self.assertEqual(first["event_id"], finding["origin_event_ids"][0])
                self.assertTrue(first["json_path"])
                self.assertTrue(first["field_path"])
                self.assertTrue(first["located"])

    def test_failure_alone_is_a_fact_not_a_finding(self) -> None:
        audit, _ = load_audit(self.batch, "fact-failure-single-retry")
        self.assertIn("tool_failure", fact_kinds(audit))
        self.assertIn("tool_call_retry", fact_kinds(audit))
        failure_fact = next(
            fact for fact in audit["facts"] if fact["kind"] == "tool_failure"
        )
        self.assertIn("不是风险发现", failure_fact["detail"])
        self.assertEqual(audit["findings"], [])

    def test_empty_and_unknown_results_are_facts_only(self) -> None:
        empty, _ = load_audit(self.batch, "fact-empty-result")
        self.assertIn("tool_result_empty", fact_kinds(empty))
        self.assertEqual(empty["findings"], [])

        unknown, _ = load_audit(self.batch, "fact-unknown-status")
        self.assertIn("tool_result_status_unknown", fact_kinds(unknown))
        self.assertEqual(unknown["findings"], [])

    def test_acknowledged_failure_after_continuing_is_not_a_finding(self) -> None:
        audit, _ = load_audit(self.batch, "failure-continued-acknowledged")
        continuation = next(
            fact for fact in audit["facts"] if fact["kind"] == "failure_continued"
        )
        self.assertTrue(continuation["data"]["acknowledged"])
        self.assertEqual(audit["findings"], [])

    def test_volume_fact_never_becomes_an_inefficiency_finding(self) -> None:
        messages: list[dict[str, Any]] = [{"role": "user", "content": "批量读取"}]
        for index in range(80):
            call_id = f"c{index}"
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": f"file-{index}.txt"}),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": "ok", "is_error": False}
            )
        messages.append({"role": "assistant", "content": "全部读取完毕"})
        batch_path = write_json(
            self.tmp / "volume_batch.json",
            {
                "schema_version": "source-batch/1",
                "source": {
                    "dataset_id": "fixture-volume",
                    "dataset_version": "fixture-1",
                    "adapter_id": ADAPTER_ID,
                },
                "records": [{"sample_id": "volume-80", "messages": messages}],
            },
        )
        result = execute_batch(
            input_path=batch_path,
            out_root=self.tmp / "runs",
            provider_id=PROVIDER_ID,
            adapter_id=ADAPTER_ID,
        )
        self.assertEqual(result["status"], "complete")
        audit, _ = load_audit(result, "volume-80")
        self.assertEqual(audit["findings"], [])
        volume = next(
            fact for fact in audit["facts"] if fact["kind"] == "trajectory_volume"
        )
        self.assertGreaterEqual(volume["data"]["tool_calls"], 80)
        self.assertEqual(volume["data"]["wall_clock_seconds"], None)
        self.assertIn("不参与低效判定", volume["detail"])


class PolicyFixtureTest(_TempDirCase):
    """六类 policy 纵切：合法、未授权、参数越界、缺确认、越界任务、无 policy。"""

    def test_allowed_tools_and_arguments_pass_without_safe_claim(self) -> None:
        batch = run_batch(
            self.tmp, "policy_allowed_batch.json", policy_name="policy_allowed.json"
        )
        audit, _ = load_audit(batch, "policy-allowed")
        self.assertEqual(audit["findings"], [])
        self.assertEqual(audit["verdict"]["value"], "undetermined")
        self.assertEqual(audit["policy"]["status"], "evaluated")
        checks = {item["check"]: item["status"] for item in audit["policy"]["checks"]}
        self.assertEqual(checks["authorized_tools"], "ok")
        self.assertEqual(checks["argument_bounds"], "ok")
        self.assertEqual(checks["human_confirmation"], "no_basis")
        self.assertEqual(checks["user_scope"], "no_basis")
        self.assertEqual(checks["policy_availability"], "ok")
        self.assertIn("policy-argument-out-of-bounds", audit["checks"]["rules_evaluated"])

    def test_unauthorized_tool_produces_exactly_one_finding(self) -> None:
        batch = run_batch(
            self.tmp,
            "policy_unauthorized_batch.json",
            policy_name="policy_unauthorized.json",
        )
        audit, _ = load_audit(batch, "policy-unauthorized")
        self.assertEqual(rule_ids(audit), ["policy-unauthorized-tool"])
        self.assertEqual(audit["verdict"]["basis"], "policy_violation")
        finding = audit["findings"][0]
        self.assertEqual(finding["categories"], ["unauthorized_tool"])
        self.assertEqual(finding["rule"]["basis"], "policy_rule")
        kinds = {item["kind"] for item in finding["evidence"]}
        self.assertIn("policy_clause", kinds)

    def test_argument_out_of_bounds_produces_exactly_one_finding(self) -> None:
        batch = run_batch(
            self.tmp,
            "policy_argument_bounds_batch.json",
            policy_name="policy_argument_bounds.json",
        )
        audit, _ = load_audit(batch, "policy-arg-bounds")
        self.assertEqual(rule_ids(audit), ["policy-argument-out-of-bounds"])
        self.assertEqual(audit["findings"][0]["categories"], ["argument_out_of_bounds"])

    def test_missing_human_confirmation_is_detected_and_recorded(self) -> None:
        batch = run_batch(
            self.tmp,
            "policy_human_confirmation_batch.json",
            policy_name="policy_human_confirmation.json",
        )
        missing, _ = load_audit(batch, "policy-confirm-missing")
        self.assertEqual(rule_ids(missing), ["policy-missing-human-confirmation"])
        self.assertEqual(
            missing["findings"][0]["categories"], ["missing_human_confirmation"]
        )

        confirmed, _ = load_audit(batch, "policy-confirm-ok")
        self.assertEqual(confirmed["findings"], [])
        checks = {
            item["check"]: item["status"] for item in confirmed["policy"]["checks"]
        }
        self.assertEqual(checks["human_confirmation"], "ok")

    def test_out_of_user_scope_uses_explicit_policy_terms(self) -> None:
        batch = run_batch(
            self.tmp, "policy_user_scope_batch.json", policy_name="policy_user_scope.json"
        )
        audit, _ = load_audit(batch, "policy-scope")
        self.assertEqual(rule_ids(audit), ["policy-out-of-user-scope"])
        self.assertEqual(audit["findings"][0]["categories"], ["out_of_user_scope"])

    def test_no_policy_keeps_every_check_without_basis(self) -> None:
        batch = run_batch(self.tmp, "policy_allowed_batch.json")
        audit, _ = load_audit(batch, "policy-allowed")
        self.assertEqual(audit["findings"], [])
        self.assertEqual(audit["verdict"]["value"], "undetermined")
        self.assertEqual(audit["policy"]["status"], "absent")
        statuses = {item["check"]: item["status"] for item in audit["policy"]["checks"]}
        self.assertEqual(statuses["policy_availability"], "missing")
        for check in statuses:
            if check != "policy_availability":
                self.assertEqual(statuses[check], "no_basis")

    def test_policy_not_bound_to_sample_stays_undetermined(self) -> None:
        payload = json.loads(
            (FIXTURES / "policy_allowed.json").read_text(encoding="utf-8")
        )
        payload["sample_ids"] = ["some-other-sample"]
        policy_path = write_json(self.tmp / "policy-not-bound.json", payload)
        result = execute_batch(
            input_path=FIXTURES / "policy_allowed_batch.json",
            out_root=self.tmp / "runs",
            provider_id=PROVIDER_ID,
            adapter_id=ADAPTER_ID,
            policy=load_policy(policy_path),
        )
        audit, _ = load_audit(result, "policy-allowed")
        self.assertEqual(audit["findings"], [])
        self.assertEqual(audit["policy"]["status"], "not_bound")
        self.assertFalse(audit["policy"]["binding"]["bound"])
        self.assertEqual(audit["verdict"]["value"], "undetermined")

    def test_policy_check_names_match_documented_scenarios(self) -> None:
        batch = run_batch(
            self.tmp, "policy_allowed_batch.json", policy_name="policy_allowed.json"
        )
        audit, _ = load_audit(batch, "policy-allowed")
        scenarios = {item["scenario"] for item in audit["policy"]["checks"]}
        self.assertEqual(scenarios, set(POLICY_SCENARIOS))

    def test_policy_document_is_written_into_run_bundle(self) -> None:
        batch = run_batch(
            self.tmp, "policy_allowed_batch.json", policy_name="policy_allowed.json"
        )
        _, run_dir = load_audit(batch, "policy-allowed")
        stored = read_json(run_dir / "policy.json")
        self.assertEqual(stored["policy_id"], "policy-allowed-v1")
        run = read_json(run_dir / "run.json")
        self.assertIn("policy", run["artifacts"])
        self.assertEqual(run["policy"]["policy_id"], "policy-allowed-v1")
        self.assertEqual(run["judge"]["name"], "none")
        self.assertIsNone(run["degraded"])


class PolicySchemaTest(_TempDirCase):
    def _base(self) -> dict[str, Any]:
        return {
            "kind": "audit-policy",
            "schema_version": "audit-policy/1",
            "policy_id": "policy-x",
            "policy_version": "1",
            "scope": "trajectory",
            "tools": [{"tool_name": "read_file"}],
        }

    def test_missing_kind_is_rejected(self) -> None:
        payload = self._base()
        payload.pop("kind")
        with self.assertRaises(PolicyError):
            policy_from_dict(payload)

    def test_unknown_comparator_is_rejected(self) -> None:
        payload = self._base()
        payload["tools"][0]["constraints"] = [
            {"argument": "path", "comparator": "fuzzy", "value": "x"}
        ]
        with self.assertRaises(PolicyError):
            policy_from_dict(payload)

    def test_duplicate_tool_names_are_rejected(self) -> None:
        payload = self._base()
        payload["tools"].append({"tool_name": "read_file"})
        with self.assertRaises(PolicyError):
            policy_from_dict(payload)

    def test_missing_policy_id_is_rejected(self) -> None:
        payload = self._base()
        payload.pop("policy_id")
        with self.assertRaises(PolicyError):
            policy_from_dict(payload)


class JudgeTest(_TempDirCase):
    def tearDown(self) -> None:
        judge_module._REGISTRY.pop("test-revise", None)
        judge_module._REGISTRY.pop("test-error", None)
        super().tearDown()

    def test_default_judge_is_disabled_and_does_not_influence_verdict(self) -> None:
        batch = run_batch(self.tmp, "structural_batch.json")
        audit, run_dir = load_audit(batch, "false-success")
        self.assertEqual(audit["judge"]["status"], "disabled")
        self.assertFalse(audit["judge"]["influences_verdict"])
        self.assertEqual(rule_ids(audit), ["failure-then-false-success"])
        page = (run_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-judge-status="disabled"', page)
        self.assertIn("确定性事实", page)

    def test_plugged_judge_is_recorded_but_never_a_success_gate(self) -> None:
        class ReviseJudge:
            def evaluate(self, *, trajectory, policy, findings):  # noqa: ANN001
                return JudgeResult(
                    status="revise",
                    reason="建议人工复核",
                    provider={"id": "test-revise", "version": "1", "kind": "stub"},
                )

        register_judge("test-revise", ReviseJudge)
        batch = run_batch(self.tmp, "structural_batch.json", judge_name="test-revise")
        audit, _ = load_audit(batch, "clean-trajectory")
        self.assertEqual(audit["judge"]["status"], "revise")
        self.assertFalse(audit["judge"]["influences_verdict"])
        self.assertEqual(audit["verdict"]["value"], "undetermined")
        self.assertEqual(audit["findings"], [])

    def test_judge_failure_is_isolated_and_reported(self) -> None:
        class ExplodingJudge:
            def evaluate(self, *, trajectory, policy, findings):  # noqa: ANN001
                raise RuntimeError("judge backend unavailable")

        register_judge("test-error", ExplodingJudge)
        batch = run_batch(self.tmp, "structural_batch.json", judge_name="test-error")
        audit, _ = load_audit(batch, "false-success")
        self.assertEqual(audit["judge"]["status"], "error")
        self.assertIn("judge backend unavailable", audit["judge"]["reason"])
        self.assertEqual(rule_ids(audit), ["failure-then-false-success"])

    def test_unknown_judge_is_rejected_by_cli(self) -> None:
        proc = invoke_cli(
            "run",
            "--input",
            FIXTURES / "policy_allowed_batch.json",
            "--out",
            self.tmp / "runs",
            "--provider",
            PROVIDER_ID,
            "--judge",
            "does-not-exist",
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["code"], "unknown_judge")


class FallbackTest(_TempDirCase):
    def test_unknown_provider_fails_closed_by_default(self) -> None:
        with self.assertRaises(RunError) as context:
            resolve_provider("formal-provider")
        self.assertEqual(context.exception.code, "unknown_provider")
        self.assertFalse(context.exception.details["allow_fallback"])

    def test_cli_run_without_fallback_keeps_original_failure(self) -> None:
        proc = invoke_cli(
            "run",
            "--input",
            FIXTURES / "single_clean_trajectory.json",
            "--out",
            self.tmp / "runs",
            "--provider",
            "formal-provider",
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["code"], "unknown_provider")

    def test_explicit_fallback_marks_degraded_and_retains_failure(self) -> None:
        result = execute_batch(
            input_path=FIXTURES / "policy_allowed_batch.json",
            out_root=self.tmp / "runs",
            provider_id="formal-provider",
            adapter_id=ADAPTER_ID,
            allow_fallback=True,
        )
        self.assertEqual(result["status"], "complete")
        audit, run_dir = load_audit(result, "policy-allowed")
        self.assertEqual(audit["degraded"]["mode"], "explicit_fallback")
        self.assertEqual(audit["degraded"]["from_provider"], "formal-provider")
        self.assertEqual(
            audit["degraded"]["original_failure"]["code"], "unknown_provider"
        )
        self.assertTrue(audit["degraded"]["retained"])
        run = read_json(run_dir / "run.json")
        self.assertEqual(run["degraded"], audit["degraded"])
        page = (run_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-degraded="true"', page)

    def test_cli_run_with_explicit_fallback_completes(self) -> None:
        proc = invoke_cli(
            "run",
            "--input",
            FIXTURES / "single_clean_trajectory.json",
            "--out",
            self.tmp / "runs",
            "--provider",
            "formal-provider",
            "--allow-fallback",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        audit = read_json(Path(payload["run_dir"]) / "audit_result.json")
        self.assertEqual(audit["degraded"]["mode"], "explicit_fallback")
        self.assertEqual(
            audit["degraded"]["original_failure"]["code"], "unknown_provider"
        )

    def test_batch_without_fallback_still_fails(self) -> None:
        with self.assertRaises(RunError) as context:
            execute_batch(
                input_path=FIXTURES / "policy_allowed_batch.json",
                out_root=self.tmp / "runs",
                provider_id="formal-provider",
                adapter_id=ADAPTER_ID,
            )
        self.assertEqual(context.exception.code, "unknown_provider")


class DeterminismAndCliTest(_TempDirCase):
    def test_offline_replay_is_deterministic(self) -> None:
        first = run_batch(
            self.tmp / "a", "policy_allowed_batch.json", policy_name="policy_allowed.json"
        )
        second = run_batch(
            self.tmp / "b", "policy_allowed_batch.json", policy_name="policy_allowed.json"
        )
        self.assertEqual(first["batch_id"], second["batch_id"])
        audit_a, _ = load_audit(first, "policy-allowed")
        audit_b, _ = load_audit(second, "policy-allowed")
        audit_a.pop("evaluated_at")
        audit_b.pop("evaluated_at")
        self.assertEqual(audit_a, audit_b)

    def test_cli_batch_with_policy_writes_run_bundle(self) -> None:
        proc = invoke_cli(
            "batch",
            "--input",
            FIXTURES / "policy_argument_bounds_batch.json",
            "--out",
            self.tmp / "runs",
            "--provider",
            PROVIDER_ID,
            "--adapter",
            ADAPTER_ID,
            "--policy",
            FIXTURES / "policy_argument_bounds.json",
            "--judge",
            "none",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "complete")
        audit, _ = load_audit(payload, "policy-arg-bounds")
        self.assertEqual(rule_ids(audit), ["policy-argument-out-of-bounds"])

    def test_cli_rejects_invalid_policy(self) -> None:
        bad = write_json(self.tmp / "bad_policy.json", {"kind": "audit-policy"})
        proc = invoke_cli(
            "batch",
            "--input",
            FIXTURES / "policy_allowed_batch.json",
            "--out",
            self.tmp / "runs",
            "--provider",
            PROVIDER_ID,
            "--adapter",
            ADAPTER_ID,
            "--policy",
            bad,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["code"], "policy_invalid")


if __name__ == "__main__":
    unittest.main()
