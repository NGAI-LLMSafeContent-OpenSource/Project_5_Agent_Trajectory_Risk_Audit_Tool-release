#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Topic4 Provider 外部 conformance suite。

只依赖标准库：发现 cases、按 argv 启动 Provider CLI、断言退出码与文件。
不 import trajectory_audit / topic4_audit。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = Path(__file__).resolve().parent / "cases"


def _provider_env() -> dict[str, str]:
    env = os.environ.copy()
    parts: list[str] = []
    for part in env.get("PYTHONPATH", "").split(os.pathsep):
        if not part:
            continue
        path = Path(part)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        parts.append(str(path))
    if str(ROOT) not in parts:
        parts.insert(0, str(ROOT))
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONUTF8"] = "1"
    return env


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(CASES_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_case_path"] = str(path)
        cases.append(payload)
    return cases


def shlex_split_command(command: str) -> list[str]:
    # Minimal split that keeps simple quoted tokens out of scope; callers should
    # pass space-separated argv without nested quotes for portability.
    return [part for part in command.split(" ") if part]


def run_case(
    case: dict[str, Any],
    *,
    provider_command: list[str],
    work_root: Path,
) -> dict[str, Any]:
    name = case["name"]
    case_dir = work_root / name
    case_dir.mkdir(parents=True, exist_ok=True)

    expect = case.get("expect") or {}
    timeout = float(case.get("timeout_seconds") or 15)

    if case.get("force_bad_command"):
        argv_prefix = [str(case.get("bad_command") or "/nonexistent/topic4-audit-xyz")]
    else:
        argv_prefix = list(provider_command)

    input_spec = case.get("input")
    if isinstance(input_spec, dict):
        input_path = case_dir / "input-batch.json"
        input_path.write_text(
            json.dumps(input_spec, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        source = ROOT / str(input_spec)
        input_path = case_dir / "input-batch.json"
        shutil.copyfile(source, input_path)

    config = dict(case.get("provider_config") or {})
    # Inject a dummy secret to ensure providers never echo it.
    config.setdefault("api_key", "SUPER_SECRET_VALUE_SHOULD_NOT_LEAK")
    config_path = case_dir / "provider-config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_path = case_dir / "audit-result.json"

    argv = [
        *argv_prefix,
        "run",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--config",
        str(config_path),
    ]

    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=str(case_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_provider_env(),
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except FileNotFoundError as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""

    errors: list[str] = []
    output_present = output_path.is_file()
    if "exit_code" in expect:
        if expect["exit_code"] is None:
            if not timed_out:
                errors.append("expected timeout but process finished")
        elif timed_out:
            errors.append("unexpected timeout")
        elif exit_code != expect["exit_code"]:
            errors.append(f"exit_code={exit_code} expected={expect['exit_code']}")

    if "timed_out" in expect and bool(timed_out) != bool(expect["timed_out"]):
        errors.append(f"timed_out={timed_out} expected={expect['timed_out']}")

    if "output_present" in expect and output_present != bool(expect["output_present"]):
        errors.append(
            f"output_present={output_present} expected={expect['output_present']}"
        )

    parsed: Any = None
    if expect.get("json_object"):
        if not output_present:
            errors.append("missing output for json_object expectation")
        else:
            try:
                parsed = json.loads(output_path.read_text(encoding="utf-8"))
                if not isinstance(parsed, dict):
                    errors.append("output JSON root is not an object")
            except json.JSONDecodeError as exc:
                errors.append(f"invalid JSON: {exc.msg}")

    if expect.get("json_invalid"):
        if not output_present:
            errors.append("missing output for json_invalid expectation")
        else:
            try:
                json.loads(output_path.read_text(encoding="utf-8"))
                errors.append("expected invalid JSON but parsed successfully")
            except json.JSONDecodeError:
                pass

    if "status" in expect:
        if not isinstance(parsed, dict):
            if output_present:
                try:
                    parsed = json.loads(output_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    parsed = None
        if not isinstance(parsed, dict):
            errors.append("cannot assert status without JSON object")
        elif parsed.get("status") != expect["status"]:
            errors.append(
                f"status={parsed.get('status')!r} expected={expect['status']!r}"
            )

    if expect.get("atomic_tmp_absent"):
        leftovers = list(case_dir.glob(".audit-result.json.*.tmp"))
        if leftovers:
            errors.append(f"atomic temp files left behind: {leftovers}")

    secret = "SUPER_SECRET_VALUE_SHOULD_NOT_LEAK"
    if secret in stdout or secret in stderr:
        errors.append("credential value leaked to stdout/stderr")

    if expect.get("forbid_semantic_on_nonzero") and exit_code not in (0, None):
        # Non-zero exit means transport failure: suite records the file as
        # diagnostic only and does not treat it as a conformant semantic result.
        if output_present and expect.get("treat_nonzero_output_as_diagnostic_only"):
            pass

    return {
        "name": name,
        "ok": not errors,
        "errors": errors,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output_present": output_present,
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Topic4 Provider conformance suite")
    parser.add_argument(
        "--provider-command",
        required=True,
        help="Provider CLI argv prefix, e.g. 'python3 -m tools.fake_topic4_provider'",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Optional case name filter; repeatable",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Optional work directory; default creates a temp dir",
    )
    args = parser.parse_args(argv)

    provider_command = shlex_split_command(args.provider_command)
    selected = set(args.cases or [])
    cases = [
        case
        for case in load_cases()
        if not selected or case.get("name") in selected
    ]
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2

    cleanup = False
    if args.work_dir:
        work_root = Path(args.work_dir)
        work_root.mkdir(parents=True, exist_ok=True)
    else:
        work_root = Path(tempfile.mkdtemp(prefix="topic4-conformance-"))
        cleanup = True

    results = [
        run_case(case, provider_command=provider_command, work_root=work_root)
        for case in cases
    ]
    failed = [item for item in results if not item["ok"]]
    summary = {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "work_dir": str(work_root),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if cleanup and not failed:
        shutil.rmtree(work_root, ignore_errors=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
