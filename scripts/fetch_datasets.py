#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""按固定清单获取并校验 ATBench / HINTBench 数据文件。

数据集文件不进入仓库：清单只固定 URL、字节数与 SHA-256，本脚本负责把文件
放到缓存目录并核对完整性。``--verify-only`` 不触网，仅核对本地缓存。

用法：
    python scripts/fetch_datasets.py                # 下载缺失文件并校验
    python scripts/fetch_datasets.py --verify-only  # 只校验本地缓存
    python scripts/fetch_datasets.py --dataset atbench --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trajectory_audit import pinned  # noqa: E402

DEFAULT_DATASET_ROOT = ROOT / ".cache" / "datasets"
USER_AGENT = "trajectory-audit-acceptance-v1/1.0"


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _download(entry: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=target.name, suffix=".part", delete=False
    ) as handle:
        staging = Path(handle.name)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                shutil.copyfileobj(response, handle, 1 << 20)
        except BaseException:
            handle.close()
            staging.unlink(missing_ok=True)
            raise
    digest, size = _sha256(staging)
    if digest != entry["sha256"] or size != entry["bytes"]:
        staging.unlink(missing_ok=True)
        raise SystemExit(
            json.dumps(
                {
                    "status": "failed",
                    "dataset_id": entry["dataset_id"],
                    "error": "downloaded_file_mismatch",
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": digest,
                    "expected_bytes": entry["bytes"],
                    "actual_bytes": size,
                },
                ensure_ascii=False,
            )
        )
    staging.replace(target)


def _entry_result(entry: dict, target: Path, state: str) -> dict:
    return {
        "dataset_id": entry["dataset_id"],
        "path": str(target),
        "state": state,
        "sha256": entry["sha256"],
        "bytes": entry["bytes"],
        "url": entry["url"],
        "license": entry["license"],
    }


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument(
        "--dataset",
        action="append",
        choices=sorted(pinned.BY_DATASET),
        help="只处理指定数据集，可重复",
    )
    parser.add_argument("--verify-only", action="store_true", help="不下载，只校验本地文件")
    parser.add_argument("--force", action="store_true", help="即使本地文件已通过校验也重新下载")
    args = parser.parse_args(argv)

    selected = args.dataset or sorted(pinned.BY_DATASET)
    results: list[dict] = []
    failures = 0

    for dataset_id in selected:
        entry = pinned.pinned(dataset_id)
        target = args.dataset_root / entry["cache_name"]
        if target.exists() and not args.force:
            digest, size = _sha256(target)
            if digest == entry["sha256"] and size == entry["bytes"]:
                results.append(_entry_result(entry, target, "cached"))
                continue
            if args.verify_only:
                failures += 1
                results.append(
                    {
                        **_entry_result(entry, target, "mismatch"),
                        "expected_sha256": entry["sha256"],
                        "actual_sha256": digest,
                        "actual_bytes": size,
                    }
                )
                continue
        if args.verify_only:
            failures += 1
            results.append(_entry_result(entry, target, "missing"))
            continue
        _download(entry, target)
        results.append(_entry_result(entry, target, "downloaded"))

    print(
        json.dumps(
            {"status": "ok" if failures == 0 else "failed", "datasets": results},
            ensure_ascii=False,
        )
    )
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
