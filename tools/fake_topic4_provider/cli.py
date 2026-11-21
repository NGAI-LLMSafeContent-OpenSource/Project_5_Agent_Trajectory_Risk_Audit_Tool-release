# SPDX-License-Identifier: Apache-2.0
"""Fake Topic4 Provider CLI：``run --input --output --config``。

与真实 ``topic4-audit`` 外部 seam 对齐。合同合法结果退出 0 并原子发布；
transport 失败退出非零。stdout/stderr 不打印凭据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .scenarios import (
    _TransportScenario,
    atomic_write_json,
    build_result,
    sanitize_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fake-topic4-provider")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--input", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--config", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command != "run":
        parser.error("only run is supported")

    input_path = Path(args.input)
    output_path = Path(args.output)
    config_path = Path(args.config)

    try:
        batch = json.loads(input_path.read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input_error: {exc}", file=sys.stderr)
        return 2

    # Never print credential values.
    safe_cfg = sanitize_config(config if isinstance(config, dict) else {})
    print(
        json.dumps(
            {
                "event": "fake_provider_start",
                "scenario": safe_cfg.get("scenario"),
                "config_keys": sorted(safe_cfg),
            },
            ensure_ascii=False,
        )
    )

    if not isinstance(config, dict):
        print("config must be object", file=sys.stderr)
        return 2

    scenario = str(config.get("scenario") or "complete_multi")
    try:
        if scenario == "invalid_json":
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("{not-json", encoding="utf-8")
            return 0
        if scenario == "missing_output":
            return 0
        if scenario == "nonzero_exit":
            # May leave a partial diagnostic file; must not be treated as formal result.
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text('{"partial": true\n', encoding="utf-8")
            return 1
        result = build_result(batch, config)
    except _TransportScenario as exc:
        if exc.kind == "missing_output":
            return 0
        if exc.kind == "invalid_json":
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("{not-json", encoding="utf-8")
            return 0
        if exc.kind == "nonzero_exit":
            return 1
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"provider_error: {exc}", file=sys.stderr)
        return 2

    try:
        atomic_write_json(output_path, result)
    except OSError as exc:
        print(f"write_error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "event": "fake_provider_done",
                "status": result.get("status"),
                "samples": len(result.get("items") or []),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
