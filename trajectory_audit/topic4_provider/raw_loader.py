# SPDX-License-Identifier: Apache-2.0
"""原始 Topic4 JSON 交换文件的无损读写。

Adapter 必须先保存课题四原始输出，再做校验与映射；不得只保留映射后的
课题五结果。本模块不解释语义，只负责字节/文本层的读写。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RawLoadError(ValueError):
    """原始文件缺失、不可读或不是合法 JSON。"""

    def __init__(self, code: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


def save_raw_text(path: str | Path, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def save_raw_json(path: str | Path, payload: Any, *, indent: int | None = 2) -> Path:
    """以 UTF-8 JSON 无损写出；``ensure_ascii=False`` 保留原文。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if indent is None:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    else:
        text = json.dumps(
            payload, ensure_ascii=False, indent=indent, allow_nan=False
        )
        text += "\n"
    target.write_text(text, encoding="utf-8")
    return target


def load_raw_text(path: str | Path) -> str:
    target = Path(path)
    if not target.is_file():
        raise RawLoadError("missing_output", "输出文件不存在", path=str(target))
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RawLoadError("invalid_encoding", "输出不是合法 UTF-8", path=str(target)) from exc
    except OSError as exc:
        raise RawLoadError(
            "unreadable_output", f"无法读取输出文件：{exc}", path=str(target)
        ) from exc


def load_raw_json(path: str | Path) -> Any:
    """读取原始 JSON；失败时抛出 ``RawLoadError``，由 Adapter 记为 transport/contract。"""

    text = load_raw_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RawLoadError(
            "invalid_json",
            f"输出不是合法 JSON：{exc.msg} (line {exc.lineno})",
            path=str(path),
        ) from exc


def copy_file(src: str | Path, dst: str | Path) -> Path:
    """字节级复制，用于把用户源文件拷入隔离工作目录。"""

    source = Path(src)
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return target
