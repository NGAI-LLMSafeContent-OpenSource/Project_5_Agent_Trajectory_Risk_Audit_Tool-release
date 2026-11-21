# SPDX-License-Identifier: Apache-2.0
"""命令行入口。

成功时向 stdout 输出单行 JSON；失败时向 stderr 输出单行结构化 JSON。
退出码：0 成功，1 内部错误，2 参数或输入错误，3 run/批次身份冲突，4 批次
或 H1 验证完成但存在逐项失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import acceptance, corpus, reference
from . import development as development_module
from . import edition as edition_module
from . import pinned
from .adapters import ADAPTERS, OPENAI_MESSAGES_ADAPTER
from .artifacts import read_json, write_json
from .batch import execute_batch
from .errors import RunError
from .baseline_report import build_baseline_report
from .source_label_report import build_source_label_report
from .verdict_score import score_archived_verdicts
from .judge import JudgeError, available_judges, resolve_judge
from .policy import Policy, PolicyError, load_policy
from .providers import PROVIDERS
from .runner import execute_run

DEFAULT_DATASET_ROOT = ".cache/datasets"
DEFAULT_ACCEPTANCE_MANIFEST = Path("acceptance") / "acceptance-v1.json"
_SUBCOMMAND_HINT = (
    "缺少子命令：run、batch、score、baseline-report、source-label-report、topic4-run、acceptance、reference 或 corpus"
)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise RunError("invalid_arguments", message)


def _add_audit_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--topic4-config", metavar="PATH", help="课题四配置；也可设置 TOPIC4_CONFIG，默认 finial/config/toolchain.json")
    parser.add_argument("--topic4-command", metavar="COMMAND", help="课题四启动命令前缀；也可设置 TOPIC4_COMMAND")
    parser.add_argument("--topic4-timeout", type=float, metavar="SECONDS", help="课题四总超时，默认 1800 秒")
    parser.add_argument("--topic4-profile", choices=("finial-1.0", "strict-v1"), help="输出兼容版本，默认 finial-1.0；topic4-run 仍默认严格校验")
    parser.add_argument(
        "--policy",
        metavar="PATH",
        help="审计 policy 文件（audit-policy/1）；不提供时 policy 检查保持无依据",
    )
    parser.add_argument(
        "--judge",
        metavar="NAME",
        default="none",
        help=(
            "可插拔语义 Judge（可用："
            f"{', '.join(available_judges())}；默认 none，完全离线）"
        ),
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="请求的 provider 不可用时显式允许 baseline fallback，并在结果中标记 degraded",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m trajectory_audit",
        description="Agent 轨迹风险审计：输入校验、来源适配、归一化、显式 baseline 审计与不可变 run 包。",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    pujiang_parser = subparsers.add_parser('pujiang', help='构建或重放验证公开五例 v3 开发交付')
    pujiang_actions = pujiang_parser.add_subparsers(dest='pujiang_action', required=True)
    for action in ('build', 'verify'):
        action_parser = pujiang_actions.add_parser(action)
        action_parser.add_argument('--source', type=Path, required=True)
        action_parser.add_argument('--out' if action == 'build' else '--root', type=Path, required=True)
    run_parser = subparsers.add_parser(
        "run",
        help="运行一条轨迹并生成 run 包",
        description="校验一条来源记录，归一化为事件，执行显式选择的 provider，写出 run 包与离线审阅页面。",
    )
    run_parser.add_argument("--input", metavar="PATH", help="单条来源轨迹 JSON 输入文件")
    run_parser.add_argument("--out", metavar="DIR", help="run 包输出根目录")
    run_parser.add_argument(
        "--provider",
        metavar="ID",
        help=f"显式选择的审计 provider（可用：{', '.join(sorted(PROVIDERS))}）",
    )
    run_parser.add_argument(
        "--adapter",
        metavar="ID",
        default=OPENAI_MESSAGES_ADAPTER,
        help=(
            "来源 adapter（可用："
            f"{', '.join(sorted(ADAPTERS))}；默认 {OPENAI_MESSAGES_ADAPTER}）"
        ),
    )
    _add_audit_options(run_parser)

    batch_parser = subparsers.add_parser(
        "batch",
        help="运行一个来源批次并为每条轨迹生成独立 run 包",
        description=(
            "读取 source-batch/1 批次输入，用同一个 adapter 归一每条记录，"
            "逐样本生成 run 包、失败清单与批次索引页。"
        ),
    )
    batch_parser.add_argument("--input", metavar="PATH", help="source-batch/1 JSON 批次输入文件")
    batch_parser.add_argument("--out", metavar="DIR", help="批次输出根目录")
    batch_parser.add_argument(
        "--provider",
        metavar="ID",
        help=f"显式选择的审计 provider（可用：{', '.join(sorted(PROVIDERS))}）",
    )
    batch_parser.add_argument(
        "--adapter",
        metavar="ID",
        help=f"显式选择的来源 adapter（可用：{', '.join(sorted(ADAPTERS))}）",
    )
    _add_audit_options(batch_parser)

    acceptance_parser = subparsers.add_parser(
        "acceptance",
        help="冻结验收集清单并对固定版本做 H1 数据契约验证",
        description=(
            "build 冻结 acceptance-v1 的 48 条样本身份；verify 对固定版本的全部"
            "记录执行转换、Schema 与可追溯性验证，输出机读与人读 H1 报告。"
        ),
    )
    acceptance_actions = acceptance_parser.add_subparsers(
        dest="acceptance_action", metavar="<action>"
    )
    build_parser = acceptance_actions.add_parser(
        "build", help="构建并冻结验收集清单"
    )
    build_parser.add_argument("--out", metavar="PATH", help="清单输出文件")
    build_parser.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=DEFAULT_DATASET_ROOT,
        help=f"固定版本数据集缓存目录（默认 {DEFAULT_DATASET_ROOT}）",
    )
    build_parser.add_argument(
        "--manifest-id",
        metavar="ID",
        default=acceptance.DEFAULT_MANIFEST_ID,
        help=f"清单身份（默认 {acceptance.DEFAULT_MANIFEST_ID}）",
    )
    build_parser.add_argument(
        "--previous", metavar="PATH", help="上一版清单路径，用于记录样本替换与原因"
    )
    build_parser.add_argument("--reason", metavar="TEXT", help="样本替换原因")
    build_parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖内容不同的已冻结清单，必须同时提供 --previous 与 --reason",
    )

    verify_parser = acceptance_actions.add_parser(
        "verify", help="对固定版本执行 H1 数据契约验证"
    )
    verify_parser.add_argument("--manifest", metavar="PATH", help="冻结清单路径")
    verify_parser.add_argument("--out", metavar="PATH", help="机读 H1 报告输出文件")
    verify_parser.add_argument("--report", metavar="PATH", help="人读 H1 报告输出文件")
    verify_parser.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=DEFAULT_DATASET_ROOT,
        help=f"固定版本数据集缓存目录（默认 {DEFAULT_DATASET_ROOT}）",
    )

    reference_parser = subparsers.add_parser(
        "reference",
        help="生成、校验与导出 ATBench 项目参考标注审阅包",
        description=(
            "build 为 acceptance-v1 的 24 条 ATBench 样本生成待确认的参考标注草稿；"
            "validate 校验审阅包自洽并回读数据集证据；export 只导出人工确认后生效的参考标注。"
        ),
    )
    reference_actions = reference_parser.add_subparsers(
        dest="reference_action", metavar="<action>"
    )
    reference_build = reference_actions.add_parser(
        "build", help="生成参考标注草稿、轨迹原文与审阅页面"
    )
    reference_build.add_argument(
        "--manifest",
        metavar="PATH",
        default=str(Path("acceptance") / "acceptance-v1.json"),
        help="验收清单路径（默认 acceptance/acceptance-v1.json）",
    )
    reference_build.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=DEFAULT_DATASET_ROOT,
        help=f"固定版本数据集缓存目录（默认 {DEFAULT_DATASET_ROOT}）",
    )
    reference_build.add_argument("--out", metavar="DIR", help="审阅包输出目录")
    reference_build.add_argument(
        "--package-id",
        metavar="ID",
        default=reference.DEFAULT_PACKAGE_ID,
        help=f"审阅包身份（默认 {reference.DEFAULT_PACKAGE_ID}）",
    )
    reference_build.add_argument(
        "--annotation-version", metavar="ID", help="标注版本，默认与包身份一致"
    )
    reference_build.add_argument(
        "--generated-at", metavar="TIMESTAMP", help="覆盖生成时间，便于可复现构建"
    )
    reference_build.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已有确认记录的审阅包（会丢失人工结论）",
    )

    reference_validate = reference_actions.add_parser(
        "validate", help="校验审阅包自洽，并按需回读数据集证据"
    )
    reference_validate.add_argument("--package", metavar="DIR", help="审阅包目录")
    reference_validate.add_argument(
        "--manifest", metavar="PATH", help="验收清单路径，用于核对草稿集合"
    )
    reference_validate.add_argument(
        "--dataset-root",
        metavar="DIR",
        help=f"固定版本数据集缓存目录；提供时回读记录与证据（如 {DEFAULT_DATASET_ROOT}）",
    )

    reference_refresh = reference_actions.add_parser(
        "refresh", help="校验审阅记录并安全刷新状态、离线页面与 README"
    )
    reference_refresh.add_argument("--package", metavar="DIR", help="审阅包目录")

    reference_export = reference_actions.add_parser(
        "export", help="导出人工确认后生效的参考标注"
    )
    reference_export.add_argument("--package", metavar="DIR", help="审阅包目录")
    reference_export.add_argument("--out", metavar="PATH", help="生效标注输出文件")

    corpus_parser = subparsers.add_parser(
        "corpus",
        help="构建与校验 topic4-audit-input/1 审计输入",
        description=(
            "build 把单条来源记录或 source-batch/1 批次通过白名单投影为"
            " topic4-audit-input/1；validate 校验已有输入文件的 Schema 与摘要。"
        ),
    )
    corpus_actions = corpus_parser.add_subparsers(
        dest="corpus_action", metavar="<action>"
    )
    corpus_build = corpus_actions.add_parser(
        "build", help="生成单条或多条 topic4-audit-input/1 批次"
    )
    corpus_build.add_argument(
        "--input", metavar="PATH", help="单条来源记录或 source-batch/1 批次文件"
    )
    corpus_build.add_argument("--out", metavar="PATH", help="审计输入输出文件")
    corpus_build.add_argument(
        "--adapter",
        metavar="ID",
        default=OPENAI_MESSAGES_ADAPTER,
        help=(
            "来源 adapter（可用："
            f"{', '.join(sorted(ADAPTERS))}；默认 {OPENAI_MESSAGES_ADAPTER}）"
        ),
    )
    corpus_build.add_argument(
        "--policy",
        metavar="PATH",
        help="可选授权 policy（audit-policy/1）；仅绑定到其声明的样本",
    )

    corpus_validate = corpus_actions.add_parser(
        "validate", help="校验 topic4-audit-input/1 文件"
    )
    corpus_validate.add_argument("--input", metavar="PATH", help="审计输入文件")

    corpus_development = corpus_actions.add_parser(
        "development",
        help="构建或校验 Development Corpus（80 条冻结开发用例集）",
        description=(
            "build 按冻结策略从固定版本来源抽样并生成 corpus 目录；"
            "verify 校验已交付目录的产物摘要、Schema、隔离与可重放性。"
        ),
    )
    development_actions = corpus_development.add_subparsers(
        dest="development_action", metavar="<action>"
    )
    development_build = development_actions.add_parser(
        "build", help="生成并冻结一个 Development Corpus"
    )
    development_build.add_argument(
        "--version",
        metavar="ID",
        default=development_module.DEFAULT_CORPUS_VERSION,
        help=(
            "corpus 版本（可用："
            f"{', '.join(sorted(pinned.DEVELOPMENT_CORPUS_POLICIES))}；"
            f"默认 {development_module.DEFAULT_CORPUS_VERSION}）"
        ),
    )
    development_build.add_argument("--out", metavar="DIR", help="corpus 输出目录")
    development_build.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=DEFAULT_DATASET_ROOT,
        help=f"固定版本数据集缓存目录（默认 {DEFAULT_DATASET_ROOT}）",
    )
    development_build.add_argument(
        "--acceptance",
        metavar="PATH",
        default=str(DEFAULT_ACCEPTANCE_MANIFEST),
        help=f"用于证明零重叠的 frozen acceptance 清单（默认 {DEFAULT_ACCEPTANCE_MANIFEST}）",
    )
    development_verify = development_actions.add_parser(
        "verify", help="校验已交付的 Development Corpus 目录"
    )
    development_verify.add_argument("--root", metavar="DIR", help="corpus 目录")
    development_verify.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=None,
        help="固定数据缓存目录；给出时额外执行全量重建比对",
    )
    development_verify.add_argument(
        "--acceptance",
        metavar="PATH",
        default=str(DEFAULT_ACCEPTANCE_MANIFEST),
        help=f"用于重算零重叠证据的 frozen acceptance 清单（默认 {DEFAULT_ACCEPTANCE_MANIFEST}）",
    )

    corpus_edition = corpus_actions.add_parser(
        "edition",
        help="构建或校验整版 Provider Corpus（Development + Blind Acceptance）",
        description=(
            "build 生成同时包含 development/ 与 blind-acceptance/ 的整版冻结目录；"
            "verify 校验整版产物摘要、两个子集、交叉零重叠证据与可重放性。"
        ),
    )
    edition_actions = corpus_edition.add_subparsers(
        dest="edition_action", metavar="<action>"
    )
    edition_build = edition_actions.add_parser(
        "build", help="生成并冻结一个整版 Provider Corpus"
    )
    edition_build.add_argument(
        "--version",
        metavar="ID",
        default=development_module.DEFAULT_CORPUS_VERSION,
        help=(
            "corpus 版本（可用："
            f"{', '.join(sorted(pinned.DEVELOPMENT_CORPUS_POLICIES))}；"
            f"默认 {development_module.DEFAULT_CORPUS_VERSION}）"
        ),
    )
    edition_build.add_argument("--out", metavar="DIR", help="整版 corpus 输出目录")
    edition_build.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=DEFAULT_DATASET_ROOT,
        help=f"固定版本数据集缓存目录（默认 {DEFAULT_DATASET_ROOT}）",
    )
    edition_build.add_argument(
        "--acceptance",
        metavar="PATH",
        default=str(DEFAULT_ACCEPTANCE_MANIFEST),
        help=f"生成验收子集并证明零重叠的 frozen acceptance 清单（默认 {DEFAULT_ACCEPTANCE_MANIFEST}）",
    )
    edition_verify = edition_actions.add_parser(
        "verify", help="校验已交付的整版 Provider Corpus 目录"
    )
    edition_verify.add_argument("--root", metavar="DIR", help="整版 corpus 目录")
    edition_verify.add_argument(
        "--dataset-root",
        metavar="DIR",
        default=None,
        help="固定数据缓存目录；给出时额外执行全量重建比对",
    )
    edition_verify.add_argument(
        "--acceptance",
        metavar="PATH",
        default=str(DEFAULT_ACCEPTANCE_MANIFEST),
    )

    score_parser = subparsers.add_parser(
        "score",
        help="从已归档批量运行重放轨迹判定评分",
        description=(
            "只读批量运行归档、冻结样本清单和独立项目参考标注，"
            "按稳定样本身份重放轨迹级判定，写出逐条记录和可读汇总。不调用模型。"
        ),
    )
    score_parser.add_argument("--batch", metavar="DIR", help="已完成的 batch-run 目录，或其中的 batch.json")
    score_parser.add_argument("--manifest", metavar="PATH", help="冻结样本清单")
    score_parser.add_argument("--reference", metavar="PATH", help="独立项目参考标注，不从归档轨迹里读取")
    score_parser.add_argument("--out", metavar="DIR", help="评分输出目录")

    baseline_report_parser = subparsers.add_parser(
        "baseline-report",
        help="归档 12 条人工参考基线并重放评分",
        description=(
            "按冻结的 12 条轨迹抽出来源记录，归档课题五确定性 baseline。"
            "课题四离线模式只在提供根目录时运行，完整模式未确认可用时标记未运行。"
            "参考标注和来源标签不进入 Provider 输入，不启用 fallback。"
        ),
    )
    baseline_report_parser.add_argument(
        "--dataset-root", metavar="DIR", help="固定版本数据集缓存目录"
    )
    baseline_report_parser.add_argument("--out", metavar="DIR", help="基线报告输出目录")
    baseline_report_parser.add_argument(
        "--topic4-root",
        metavar="DIR",
        help="课题四 finial 根目录；提供后才尝试离线模式",
    )

    source_label_parser = subparsers.add_parser(
        "source-label-report",
        help="对照 Development Corpus 的来源标签",
        description=(
            "读取冻结开发集的 Provider 输入和来源标签，"
            "用确定性 baseline 统计一致、不一致、未判定和运行失败。"
            "来源标签不是人工裁决，不读取 12 条项目参考标注，也不启用 fallback。"
        ),
    )
    source_label_parser.add_argument("--corpus", metavar="DIR", help="Development Corpus 目录")
    source_label_parser.add_argument("--out", metavar="DIR", help="对照报告输出目录")
    source_label_parser.add_argument(
        "--gloss",
        metavar="PATH",
        help="可选的样本身份到中文对照，不参与判定",
    )

    topic4_parser = subparsers.add_parser(
        "topic4-run",
        help="通过外部 CLI/文件 seam 运行 Topic 4 Provider 并归档",
        description=(
            "复制输入与配置到隔离工作目录，按 argv 启动 topic4-audit 兼容 CLI，"
            "校验 topic4-audit-result/1，映射 Detected Finding，并保存运行归档。"
            "正式 Provider 失败默认不 fallback；仅 --allow-fallback 时记录 degraded。"
        ),
    )
    topic4_parser.add_argument(
        "--input", metavar="PATH", help="topic4-audit-input/1 批次输入"
    )
    topic4_parser.add_argument(
        "--config", metavar="PATH", help="课题四 Provider 配置 JSON"
    )
    topic4_parser.add_argument(
        "--command",
        dest="provider_command",
        metavar="ARGV",
        help=(
            "Provider CLI argv 前缀（空格分隔），例如 "
            "'python3 -m tools.fake_topic4_provider' 或 topic4-audit 可执行路径"
        ),
    )
    topic4_parser.add_argument(
        "--out", metavar="DIR", help="运行归档输出目录"
    )
    topic4_parser.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=float,
        default=600.0,
        help="Provider 总超时秒数（默认 600）",
    )
    topic4_parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="正式 Topic4 失败时显式允许 baseline fallback，并记录 degraded",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command is None:
            raise RunError(
                "invalid_arguments",
                _SUBCOMMAND_HINT,
            )
        if args.command == 'pujiang':
            from .pujiang import build_delivery, verify_delivery
            result = (build_delivery(args.source, args.out) if args.pujiang_action == 'build'
                      else verify_delivery(args.source, args.root))
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "acceptance":
            return _command_acceptance(args)
        if args.command == "reference":
            return _command_reference(args)
        if args.command == "corpus":
            return _command_corpus(args)
        if args.command == "topic4-run":
            return _command_topic4_run(args)
        if args.command == "score":
            return _command_score(args)
        if args.command == "baseline-report":
            return _command_baseline_report(args)
        if args.command == "source-label-report":
            return _command_source_label_report(args)
        if args.command not in {"run", "batch"}:
            raise RunError(
                "invalid_arguments",
                _SUBCOMMAND_HINT,
            )
        if not args.input:
            raise RunError("missing_input", "缺少必需参数：--input")
        if not args.out:
            raise RunError("missing_output", "缺少必需参数：--out")
        if not args.provider:
            raise RunError("missing_provider", "缺少必需参数：--provider")
        provider_options = {
            "config": args.topic4_config, "command": args.topic4_command,
            "timeout": args.topic4_timeout, "profile": args.topic4_profile,
        }

        if args.command == "run":
            policy = _load_audit_policy(args.policy)
            _validate_judge(args.judge)
            result = execute_run(
                input_path=Path(args.input),
                out_root=Path(args.out),
                provider_id=args.provider,
                adapter_id=args.adapter,
                policy=policy,
                judge_name=args.judge,
                allow_fallback=args.allow_fallback,
                provider_options=provider_options,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0

        if not args.adapter:
            raise RunError("missing_adapter", "batch 缺少必需参数：--adapter")
        policy = _load_audit_policy(args.policy)
        _validate_judge(args.judge)
        result = execute_batch(
            input_path=Path(args.input),
            out_root=Path(args.out),
            provider_id=args.provider,
            adapter_id=args.adapter,
            policy=policy,
            judge_name=args.judge,
            allow_fallback=args.allow_fallback,
            provider_options=provider_options,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "complete" else 4
    except RunError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，输出机器可读错误
        error = RunError("internal_error", f"运行失败：{exc}", exit_code=1)
        print(json.dumps(error.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 1


def _load_audit_policy(path: str | None) -> Policy | None:
    if not path:
        return None
    try:
        return load_policy(path)
    except PolicyError as exc:
        raise RunError(
            "policy_invalid",
            f"policy 文件不合法：{exc}",
            details={"path": path},
        ) from None


def _validate_judge(name: str) -> None:
    try:
        resolve_judge(name)
    except JudgeError as exc:
        raise RunError(
            "unknown_judge",
            str(exc),
            details={"requested": name, "available": available_judges()},
        ) from None


def _command_baseline_report(args: Any) -> int:
    if not args.dataset_root:
        raise RunError("missing_dataset_root", "baseline-report 缺少必需参数：--dataset-root")
    if not args.out:
        raise RunError("missing_output", "baseline-report 缺少必需参数：--out")
    report = build_baseline_report(
        dataset_root=Path(args.dataset_root),
        out_dir=Path(args.out),
        topic4_root=Path(args.topic4_root) if args.topic4_root else None,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _command_source_label_report(args: Any) -> int:
    if not args.corpus:
        raise RunError("missing_corpus", "source-label-report 缺少必需参数：--corpus")
    if not args.out:
        raise RunError("missing_output", "source-label-report 缺少必需参数：--out")
    report = build_source_label_report(
        corpus_dir=Path(args.corpus),
        out_dir=Path(args.out),
        gloss_path=Path(args.gloss) if args.gloss else None,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _command_score(args: Any) -> int:
    if not args.batch:
        raise RunError("missing_batch", "score 缺少必需参数：--batch")
    if not args.manifest:
        raise RunError("missing_manifest", "score 缺少必需参数：--manifest")
    if not args.reference:
        raise RunError("missing_reference", "score 缺少必需参数：--reference")
    if not args.out:
        raise RunError("missing_output", "score 缺少必需参数：--out")
    out_dir = Path(args.out)
    report = score_archived_verdicts(
        batch_dir=Path(args.batch),
        manifest_path=Path(args.manifest),
        reference_path=Path(args.reference),
        out_dir=out_dir,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "rules_version": report["rules_version"],
                "batch_id": report["run"]["batch_id"],
                "manifest_id": report["manifest"]["manifest_id"],
                "annotation_version": report["reference"]["annotation_version"],
                "sample_count": report["sample_count"],
                "counts": report["counts"],
                "confirmed_risk_detection": report["confirmed_risk_detection"]["display"],
                "records": str(out_dir / "records.jsonl"),
                "summary": str(out_dir / "summary.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _command_acceptance(args: Any) -> int:
    action = args.acceptance_action
    if action == "build":
        return _command_acceptance_build(args)
    if action == "verify":
        return _command_acceptance_verify(args)
    raise RunError("invalid_arguments", "acceptance 缺少动作：build 或 verify")


def _command_acceptance_build(args: Any) -> int:
    if not args.out:
        raise RunError("missing_output", "acceptance build 缺少必需参数：--out")
    previous = None
    if args.previous:
        previous = read_json(Path(args.previous))
        if not isinstance(previous, dict):
            raise RunError(
                "invalid_previous_manifest",
                "--previous 必须指向一个清单 JSON 对象",
                details={"path": str(args.previous)},
            )
        if previous.get("manifest_id") != args.manifest_id:
            raise RunError(
                "manifest_id_mismatch",
                "--previous 的清单身份与 --manifest-id 不一致",
                details={
                    "previous": previous.get("manifest_id"),
                    "requested": args.manifest_id,
                },
            )
    manifest = acceptance.build_manifest(
        dataset_root=Path(args.dataset_root),
        manifest_id=args.manifest_id,
        previous=previous,
        replacement_reason=args.reason,
    )
    out_path = Path(args.out)
    state = "frozen"
    if out_path.exists():
        existing = read_json(out_path)
        existing_digest = (existing.get("integrity") or {}).get("content_sha256") if isinstance(
            existing, dict
        ) else None
        if existing_digest == manifest["integrity"]["content_sha256"]:
            state = "unchanged"
        elif not args.force:
            raise RunError(
                "manifest_frozen",
                "目标清单已冻结且内容不同；冻结清单不可被覆盖",
                details={
                    "path": str(out_path),
                    "existing_manifest_id": (existing or {}).get("manifest_id")
                    if isinstance(existing, dict)
                    else None,
                    "existing_content_sha256": existing_digest,
                    "new_content_sha256": manifest["integrity"]["content_sha256"],
                    "hint": "新建 acceptance-v2，或显式传入 --force --previous --reason 记录替换",
                },
                exit_code=3,
            )
        elif not (args.previous and args.reason):
            raise RunError(
                "replacement_reason_required",
                "--force 必须与 --previous 和 --reason 一起使用",
                details={"path": str(out_path)},
            )
        else:
            state = "replaced"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, manifest)
    print(        json.dumps(
            {
                "status": state,
                "manifest_id": manifest["manifest_id"],
                "path": str(out_path),
                "content_sha256": manifest["integrity"]["content_sha256"],
                "selected": len(manifest["samples"]),
                "excluded": len(manifest["excluded_records"]),
                "replacements": len(manifest["replacements"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _command_acceptance_verify(args: Any) -> int:
    if not args.manifest:
        raise RunError("missing_manifest", "acceptance verify 缺少必需参数：--manifest")
    if not args.out:
        raise RunError("missing_output", "acceptance verify 缺少必需参数：--out")
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        raise RunError(
            "manifest_not_found",
            "冻结清单文件不存在",
            details={"path": str(manifest_path)},
        )
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RunError(
            "invalid_manifest",
            "冻结清单必须是 JSON 对象",
            details={"path": str(manifest_path)},
        )
    report = acceptance.write_report(
        manifest=manifest,
        dataset_root=Path(args.dataset_root),
        out_json=Path(args.out),
        out_markdown=Path(args.report) if args.report else None,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "manifest_id": report["manifest_id"],
                "report": str(args.out),
                "checks": {check["id"]: check["status"] for check in report["checks"]},
                "records": {
                    source["dataset_id"]: source["records_total"]
                    for source in report["sources"]
                },
                "failures": len(report["failures"]),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 4


def _command_corpus(args: Any) -> int:
    action = args.corpus_action
    if action == "build":
        return _command_corpus_build(args)
    if action == "validate":
        return _command_corpus_validate(args)
    if action == "development":
        return _command_corpus_development(args)
    if action == "edition":
        return _command_corpus_edition(args)
    raise RunError(
        "invalid_arguments", "corpus 缺少动作：build、validate、development 或 edition"
    )


def _command_corpus_edition(args: Any) -> int:
    action = args.edition_action
    if action == "build":
        return _command_edition_build(args)
    if action == "verify":
        return _command_edition_verify(args)
    raise RunError("invalid_arguments", "corpus edition 缺少动作：build 或 verify")


def _command_edition_build(args: Any) -> int:
    if not args.out:
        raise RunError("missing_output", "corpus edition build 缺少必需参数：--out")
    acceptance_manifest = _load_acceptance_manifest(Path(args.acceptance))
    result = edition_module.write_edition(
        version=args.version,
        dataset_root=Path(args.dataset_root),
        acceptance_manifest=acceptance_manifest,
        out_dir=Path(args.out),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _command_edition_verify(args: Any) -> int:
    if not args.root:
        raise RunError("missing_root", "corpus edition verify 缺少必需参数：--root")
    acceptance_manifest = _load_acceptance_manifest(Path(args.acceptance))
    report = edition_module.verify_edition(
        root=Path(args.root),
        dataset_root=Path(args.dataset_root) if args.dataset_root else None,
        acceptance_manifest=acceptance_manifest,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 4


def _command_corpus_development(args: Any) -> int:
    action = args.development_action
    if action == "build":
        return _command_development_build(args)
    if action == "verify":
        return _command_development_verify(args)
    raise RunError(
        "invalid_arguments", "corpus development 缺少动作：build 或 verify"
    )


def _command_development_build(args: Any) -> int:
    if not args.out:
        raise RunError(
            "missing_output", "corpus development build 缺少必需参数：--out"
        )
    acceptance_manifest = _load_acceptance_manifest(Path(args.acceptance))
    result = development_module.write_development_corpus(
        version=args.version,
        dataset_root=Path(args.dataset_root),
        acceptance_manifest=acceptance_manifest,
        out_dir=Path(args.out),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _command_development_verify(args: Any) -> int:
    if not args.root:
        raise RunError(
            "missing_root", "corpus development verify 缺少必需参数：--root"
        )
    acceptance_manifest = _load_acceptance_manifest(Path(args.acceptance))
    report = development_module.verify_development_corpus(
        root=Path(args.root),
        dataset_root=Path(args.dataset_root) if args.dataset_root else None,
        acceptance_manifest=acceptance_manifest,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 4


def _load_acceptance_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RunError(
            "manifest_not_found",
            "frozen acceptance 清单不存在",
            details={"path": str(path)},
        )
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise RunError(
            "invalid_manifest",
            "frozen acceptance 清单必须是 JSON 对象",
            details={"path": str(path)},
        )
    return manifest


def _command_topic4_run(args: Any) -> int:
    if not args.input:
        raise RunError("missing_input", "topic4-run 缺少必需参数：--input")
    if not args.config:
        raise RunError("missing_config", "topic4-run 缺少必需参数：--config")
    if not args.provider_command:
        raise RunError("missing_command", "topic4-run 缺少必需参数：--command")
    if not args.out:
        raise RunError("missing_output", "topic4-run 缺少必需参数：--out")

    from .topic4_provider.adapter import run_topic4_adapter

    command = [part for part in str(args.provider_command).split(" ") if part]
    fallback_runner = None
    if args.allow_fallback:
        from .topic4_provider.baseline_fallback import run_baseline_batch_fallback

        def fallback_runner(input_batch: dict[str, Any]) -> dict[str, Any]:
            return run_baseline_batch_fallback(input_batch)

    outcome = run_topic4_adapter(
        input_path=Path(args.input),
        config_path=Path(args.config),
        command=command,
        archive_dir=Path(args.out),
        timeout=args.timeout,
        allow_fallback=bool(args.allow_fallback),
        fallback_runner=fallback_runner,
    )
    payload = {
        "ok": outcome.ok,
        "kind": outcome.kind,
        "archive_dir": outcome.archive_dir,
        "failure": outcome.failure,
        "degraded": outcome.degraded,
        "validation_issue_count": len(outcome.validation_issues),
        "batch_status": (outcome.raw_result or {}).get("status")
        if isinstance(outcome.raw_result, dict)
        else None,
        "mapped_success_count": len((outcome.mapped or {}).get("success_items") or [])
        if outcome.mapped
        else 0,
        "mapped_failure_count": len((outcome.mapped or {}).get("failure_items") or [])
        if outcome.mapped
        else 0,
    }
    print(json.dumps(payload, ensure_ascii=False))
    if outcome.ok:
        return 0
    if outcome.kind in {"transport", "contract"}:
        return 1
    return 1


def _command_corpus_build(args: Any) -> int:
    if not args.input:
        raise RunError("missing_input", "corpus build 缺少必需参数：--input")
    if not args.out:
        raise RunError("missing_output", "corpus build 缺少必需参数：--out")
    policy = _load_audit_policy(args.policy)
    result = corpus.write_corpus_batch(
        source_path=Path(args.input),
        out_path=Path(args.out),
        adapter_id=args.adapter,
        policy=policy,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _command_corpus_validate(args: Any) -> int:
    if not args.input:
        raise RunError("missing_input", "corpus validate 缺少必需参数：--input")
    result = corpus.validate_corpus_file(Path(args.input))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 4


def _command_reference(args: Any) -> int:
    action = args.reference_action
    if action == "build":
        return _command_reference_build(args)
    if action == "validate":
        return _command_reference_validate(args)
    if action == "refresh":
        return _command_reference_refresh(args)
    if action == "export":
        return _command_reference_export(args)
    raise RunError(
        "invalid_arguments", "reference 缺少动作：build、validate、refresh 或 export"
    )


def _command_reference_build(args: Any) -> int:
    if not args.out:
        raise RunError("missing_output", "reference build 缺少必需参数：--out")
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        raise RunError(
            "manifest_not_found",
            "验收清单文件不存在",
            details={"path": str(manifest_path)},
        )
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RunError(
            "invalid_manifest",
            "验收清单必须是 JSON 对象",
            details={"path": str(manifest_path)},
        )
    package = reference.build_package(
        acceptance_manifest=manifest,
        dataset_root=Path(args.dataset_root),
        package_id=args.package_id,
        annotation_version=args.annotation_version,
        generated_at=args.generated_at,
    )
    issues = reference.validate_package(
        package, acceptance_manifest=manifest
    )
    if issues:
        raise RunError(
            "reference_package_invalid",
            "生成的审阅包未通过自洽校验",
            details={"issues": issues},
        )
    out_root = Path(args.out)
    reference.write_package(package, out_root, force=args.force)
    print(
        json.dumps(
            {
                "status": "written",
                "package_id": package["manifest"]["package_id"],
                "annotation_version": package["manifest"]["annotation_version"],
                "path": str(out_root),
                "draft_count": package["manifest"]["draft_count"],
                "locator_failure_count": package["manifest"][
                    "locator_failure_count"
                ],
                "files": [
                    "manifest.json",
                    "drafts.json",
                    "transcripts.json",
                    "review.json",
                    reference.PAGE_FILE,
                    reference.MARKDOWN_FILE,
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _command_reference_validate(args: Any) -> int:
    if not args.package:
        raise RunError("missing_package", "reference validate 缺少必需参数：--package")
    package = reference.load_package(Path(args.package))
    manifest = None
    if args.manifest:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            raise RunError(
                "manifest_not_found",
                "验收清单文件不存在",
                details={"path": str(manifest_path)},
            )
        manifest = read_json(manifest_path)
    issues = reference.validate_package(package, acceptance_manifest=manifest)
    dataset_issues: list[dict[str, str]] = []
    if args.dataset_root:
        dataset_issues = reference.verify_against_dataset(
            package, Path(args.dataset_root)
        )
    all_issues = issues + dataset_issues
    print(
        json.dumps(
            {
                "status": "pass" if not all_issues else "fail",
                "package_id": (package.get("manifest") or {}).get("package_id"),
                "draft_count": (package.get("manifest") or {}).get("draft_count"),
                "review_pending": reference.review_is_pending(
                    package.get("review") or {}
                ),
                "structure_issues": len(issues),
                "dataset_issues": len(dataset_issues),
                "issues": all_issues,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not all_issues else 4


def _command_reference_export(args: Any) -> int:
    if not args.package:
        raise RunError("missing_package", "reference export 缺少必需参数：--package")
    package = reference.load_package(Path(args.package))
    payload = reference.export_effective(package)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(out_path, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "package_id": payload["package_id"],
                "annotation_version": payload["annotation_version"],
                "draft_count": payload["draft_count"],
                "effective_count": payload["effective_count"],
                "problem_count": payload["problem_count"],
                "out": str(args.out) if args.out else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _command_reference_refresh(args: Any) -> int:
    if not args.package:
        raise RunError("missing_package", "reference refresh 缺少必需参数：--package")
    root = Path(args.package)
    package = reference.load_package(root)
    status = reference.refresh_review_artifacts(package, root)
    review = package.get("review") or {}
    decided = sum(
        1
        for item in review.get("items") or []
        if isinstance(item, dict) and item.get("decision")
    )
    print(
        json.dumps(
            {
                "status": status,
                "package_id": (package.get("manifest") or {}).get("package_id"),
                "decided": decided,
                "draft_count": (package.get("manifest") or {}).get("draft_count"),
                "files": ["review.json", reference.PAGE_FILE, reference.MARKDOWN_FILE],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _configure_console() -> None:
    """优先把标准输出/错误切到 UTF-8，避免 Windows 控制台编码导致 traceback。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
