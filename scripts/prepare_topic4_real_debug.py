# SPDX-License-Identifier: Apache-2.0
"""准备真实 Topic4 CLI 调试用的 1 条子集输入与 offline 配置副本。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = (
    ROOT
    / "corpus"
    / "topic4-provider-corpus-v2"
    / "development"
    / "input-batch.json"
)
DEFAULT_TOPIC4 = (
    ROOT.parent / "Project_4-5_ContentToolRisk" / "课题五" / "finial"
)
DEFAULT_OUT = ROOT / "fixtures" / "topic4_provider"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--topic4-root", type=Path, default=DEFAULT_TOPIC4)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    topic4 = args.topic4_root
    offline = topic4 / "config" / "offline.json"
    cli = topic4 / "topic4-audit"
    if not offline.is_file():
        raise SystemExit(f"缺少 offline 配置：{offline}")
    if not cli.is_file():
        raise SystemExit(f"缺少 topic4-audit 入口：{cli}")

    batch = json.loads(args.corpus.read_text(encoding="utf-8"))
    subset = {
        "schema_version": batch["schema_version"],
        "batch_id": batch["batch_id"],
        "items": [batch["items"][0]],
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    input_path = args.out_dir / "real_offline_one_item_input.json"
    config_path = args.out_dir / "real_offline_config.json"
    input_path.write_text(
        json.dumps(subset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copyfile(offline, config_path)

    print(
        json.dumps(
            {
                "input": str(input_path),
                "config": str(config_path),
                "topic4_root": str(topic4),
                "topic4_cli": str(cli),
                "sample_id": subset["items"][0]["sample_id"],
                "batch_id": subset["batch_id"],
                "suggested_command": (
                    f'"{Path(cli).as_posix()}"'
                    if False
                    else str(cli)
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
