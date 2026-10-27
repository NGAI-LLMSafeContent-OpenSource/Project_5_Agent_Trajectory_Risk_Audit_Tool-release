# SPDX-License-Identifier: Apache-2.0
"""不可变目录与结构化产物的共享写入工具。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import RunError


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_input(path: Path) -> tuple[bytes, Any]:
    """读取 UTF-8 JSON 输入，失败时返回机器可读错误。"""
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        raise RunError(
            "input_not_found",
            f"输入文件不存在：{path}",
            details={"path": str(path)},
        ) from None
    except OSError as exc:
        raise RunError(
            "input_unreadable",
            f"无法读取输入文件：{exc}",
            details={"path": str(path)},
        ) from None

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RunError(
            "input_not_utf8",
            "输入文件必须是 UTF-8 编码",
            details={"path": str(path), "error": str(exc)},
        ) from None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunError(
            "input_not_json",
            "输入文件不是合法 JSON",
            details={
                "path": str(path),
                "line": exc.lineno,
                "column": exc.colno,
                "error": exc.msg,
            },
        ) from None
    return raw_bytes, payload


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def claim_directory(
    directory: Path,
    *,
    completion_marker: str,
    already_exists_code: str,
    incomplete_code: str,
    conflict_details: dict[str, Any] | None = None,
) -> None:
    """新建目录作为不可变运行单位；已存在时不覆盖。"""
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RunError(
            "output_not_writable",
            f"无法创建输出目录：{exc}",
            details={"path": str(directory.parent)},
        ) from None

    try:
        directory.mkdir()
    except FileExistsError:
        details = {"directory": str(directory), **(conflict_details or {})}
        if (directory / completion_marker).is_file():
            raise RunError(
                already_exists_code,
                "同一身份已存在且已完成，拒绝覆盖",
                details=details,
                exit_code=3,
            ) from None
        raise RunError(
            incomplete_code,
            "目标目录已存在但没有完成标记，请人工检查后移除",
            details=details,
            exit_code=3,
        ) from None
    except OSError as exc:
        raise RunError(
            "output_not_writable",
            f"无法创建输出目录：{exc}",
            details={"path": str(directory)},
        ) from None


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
