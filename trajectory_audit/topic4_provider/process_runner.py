# SPDX-License-Identifier: Apache-2.0
"""外部进程启动器。

以参数数组启动命令，在隔离工作目录中运行，捕获退出码、stdout/stderr、
总超时与启动失败。stdout/stderr 只作诊断，不承载语义结果。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

from .types import ProcessRunResult


def _env_with_absolute_pythonpath(
    env: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """把 ``PYTHONPATH`` 中的相对路径解析为绝对路径。

    Adapter 会在隔离 workdir 中启动子进程；若继承的 ``PYTHONPATH=.`` 仍是
    相对路径，子进程会在 workdir 下找不到项目包。
    """

    base = dict(os.environ) if env is None else dict(env)
    raw = base.get("PYTHONPATH")
    if not raw:
        return base if env is not None else None
    parts: list[str] = []
    for part in raw.split(os.pathsep):
        if not part:
            continue
        path = Path(part)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        parts.append(str(path))
    base["PYTHONPATH"] = os.pathsep.join(parts)
    return base


def run_command(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessRunResult:
    """启动 ``argv`` 命令并返回可观察进程事实。

    - ``started=False``：命令根本未能启动（例如可执行文件不存在）；
    - ``timed_out=True``：超过 ``timeout`` 秒被终止；
    - 其余情况 ``exit_code`` 为进程退出码。
    """

    command = [str(part) for part in argv]
    workdir = str(Path(cwd))
    started_at = time.monotonic()
    child_env = _env_with_absolute_pythonpath(env)
    if not command:
        return ProcessRunResult(
            argv=command,
            cwd=workdir,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            started=False,
            error="empty_argv",
        )
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
            check=False,
        )
    except FileNotFoundError as exc:
        return ProcessRunResult(
            argv=command,
            cwd=workdir,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started_at,
            started=False,
            error=f"command_not_found: {exc}",
        )
    except OSError as exc:
        return ProcessRunResult(
            argv=command,
            cwd=workdir,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started_at,
            started=False,
            error=f"os_error: {exc}",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return ProcessRunResult(
            argv=command,
            cwd=workdir,
            exit_code=None,
            timed_out=True,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started_at,
            started=True,
            error="timeout",
        )
    return ProcessRunResult(
        argv=command,
        cwd=workdir,
        exit_code=completed.returncode,
        timed_out=False,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        duration_seconds=time.monotonic() - started_at,
        started=True,
        error=None,
    )
