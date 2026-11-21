# SPDX-License-Identifier: Apache-2.0
"""80 条 Development Corpus 的来源标签对照。

对照只读取冻结语料的 Provider 输入和 ``self-check-reference.json``。
来源标签不是项目参考标注，也不进入 Provider 输入。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import write_json
from .errors import RunError
from .topic4_provider.baseline_fallback import run_baseline_batch_fallback

OUTCOMES = ("一致", "不一致", "未判定", "运行失败")
SOURCE_SCHEMA = "provider-corpus-reference/1"


def build_source_label_report(
    *,
    corpus_dir: Path,
    out_dir: Path,
    gloss_path: Path | None = None,
) -> dict[str, Any]:
    """用确定性 baseline 对照来源标签，并写出优先复核清单。"""

    corpus_dir = Path(corpus_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_batch = _load_object(corpus_dir / "input-batch.json", "missing_corpus_input")
    reference = _load_reference(corpus_dir / "self-check-reference.json")
    gloss = _load_gloss(gloss_path)
    _copy_provider_input(out_dir, source_batch)
    baseline = run_baseline_batch_fallback(source_batch, degraded=None)
    rows = _compare(reference["samples"], source_batch, baseline)
    review = _priority_review(rows, gloss)
    document = {
        "corpus_version": reference["corpus_version"],
        "allow_fallback": False,
        "isolated_from_human_reference": True,
        "is_project_truth": False,
        "sample_count": len(rows),
        "counts": _counts(rows),
        "by_source": _groups(rows, "dataset_id"),
        "by_category": _category_groups(rows),
        "priority_review": review,
        "modes": {
            "topic5-deterministic-baseline": {"status": "archived", "allow_fallback": False},
            "topic4-offline": _not_run("没有在这条对照里运行课题四离线模式。"),
            "topic4-full": _not_run(
                "完整模式需要语义模型、FilterGuard 和 V7 权重。本次没有运行，也没有启用 fallback。"
            ),
        },
    }
    (out_dir / "report.md").write_text(_markdown(document, rows), encoding="utf-8", newline="\n")
    write_json(out_dir / "report.json", document)
    return {
        "status": "ok",
        "sample_count": document["sample_count"],
        "counts": document["counts"],
        "isolated_from_human_reference": True,
        "allow_fallback": False,
        "review_count": len(review),
    }


def _compare(
    samples: list[dict[str, Any]],
    source_batch: dict[str, Any],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    items = {
        item.get("sample_id"): item
        for item in source_batch.get("items") or []
        if isinstance(item, dict)
    }
    results = {
        item.get("sample_id"): item
        for item in baseline.get("items") or []
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = sample["sample_id"]
        label = sample["source_label"]
        layer = label.get("risk_layer")
        result = results.get(sample_id)
        verdict = None
        status = "missing"
        findings: list[dict[str, Any]] = []
        if isinstance(result, dict):
            status = str(result.get("status") or "missing")
            payload = result.get("result") if isinstance(result.get("result"), dict) else {}
            verdict = payload.get("verdict") if isinstance(payload.get("verdict"), str) else None
            raw_findings = payload.get("findings")
            if isinstance(raw_findings, list):
                findings = [item for item in raw_findings if isinstance(item, dict)]
        item = items.get(sample_id)
        events = _events(item)
        rows.append(
            {
                "sample_id": sample_id,
                "source_sample_id": sample.get("source_sample_id"),
                "dataset_id": sample["dataset_id"],
                "categories": _categories(sample),
                "risk_layer": layer,
                "detected_verdict": verdict,
                "outcome": _outcome(layer, verdict, status),
                "english": _english(events),
                "key_step": _key_step(events, findings),
                "evidence": _evidence(item, events, findings),
                "rule_id": _rule_id(findings),
                "rule_basis": _rule_basis(findings),
                "finding_reason": _finding_reason(findings),
            }
        )
    return rows


def _outcome(layer: Any, verdict: str | None, status: str) -> str:
    if status != "success" or verdict not in ("safe", "unsafe", "undetermined"):
        return "运行失败"
    if verdict == "undetermined":
        return "未判定"
    source_risk = layer in ("unsafe", "risky")
    return "一致" if source_risk == (verdict == "unsafe") else "不一致"


def _categories(sample: dict[str, Any]) -> list[str]:
    label = sample["source_label"]
    if sample["dataset_id"] == "atbench":
        risk_source = label.get("risk_source")
        if isinstance(risk_source, str) and risk_source:
            return [risk_source]
        return ["未分类"]
    raw = label.get("categories")
    names = [item for item in raw if isinstance(item, str) and item] if isinstance(raw, list) else []
    return names or ["未分类"]


def _finding_reason(findings: list[dict[str, Any]]) -> str | None:
    if not findings:
        return None
    reason = findings[0].get("reason")
    if isinstance(reason, str) and reason:
        return reason
    return None


def _events(item: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(item, dict):
        return []
    trajectory = item.get("trajectory")
    if not isinstance(trajectory, dict):
        return []
    events = trajectory.get("events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _english(events: list[dict[str, Any]]) -> str | None:
    user = _user_event(events)
    if user is not None:
        text = user["content"].get("text")
        if isinstance(text, str) and text:
            return text
    for event in events:
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        text = content.get("text") or content.get("output")
        if isinstance(text, str) and text:
            return text
        tool = content.get("tool")
        if isinstance(tool, str) and tool:
            return tool
    return None


def _user_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if content.get("role") == "user" and isinstance(content.get("text"), str) and content["text"]:
            return event
    return None


def _key_step(events: list[dict[str, Any]], findings: list[dict[str, Any]]) -> Any:
    if not findings:
        user = _user_event(events)
        if user is not None and user.get("source_step_id") is not None:
            return user.get("source_step_id")
    by_id = {event.get("event_id"): event for event in events}
    for finding in findings:
        origins = finding.get("origin_event_ids")
        if not isinstance(origins, list):
            continue
        for origin in origins:
            event = by_id.get(origin)
            if isinstance(event, dict) and event.get("source_step_id") is not None:
                return event.get("source_step_id")
    for event in events:
        if event.get("source_step_id") is not None:
            return event.get("source_step_id")
    return None


def _rule_id(findings: list[dict[str, Any]]) -> str | None:
    rule = _rule(findings)
    if isinstance(rule, dict) and isinstance(rule.get("id"), str):
        return rule["id"]
    if isinstance(rule, str):
        return rule
    return None


def _rule_basis(findings: list[dict[str, Any]]) -> str | None:
    rule = _rule(findings)
    if isinstance(rule, dict) and isinstance(rule.get("basis"), str):
        return rule["basis"]
    return None


def _rule(findings: list[dict[str, Any]]) -> Any:
    if not findings:
        return None
    return findings[0].get("rule")


def _evidence(
    item: dict[str, Any] | None,
    events: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> str | None:
    if item is None:
        return None
    sample_id = item.get("sample_id")
    by_id = {event.get("event_id"): index for index, event in enumerate(events)}
    for finding in findings:
        origins = list(finding.get("origin_event_ids") or [])
        single = finding.get("origin_event_id")
        if isinstance(single, str):
            origins.insert(0, single)
        for origin in origins:
            index = by_id.get(origin)
            if index is not None:
                return f"items/{sample_id}/trajectory/events/{index}"
    user = _user_event(events)
    if user is not None:
        return f"items/{sample_id}/trajectory/events/{events.index(user)}"
    for index, event in enumerate(events):
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if content.get("text") or content.get("output") or content.get("tool"):
            return f"items/{sample_id}/trajectory/events/{index}"
    return f"items/{sample_id}/trajectory"


def _priority_review(rows: list[dict[str, Any]], gloss: dict[str, str]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(pool: list[dict[str, Any]], reason: str, kind: str, *, per_dataset: bool = False) -> None:
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in sorted(pool, key=lambda item: item["sample_id"]):
            if row["sample_id"] in seen:
                continue
            keys = (
                [(row["dataset_id"],)]
                if per_dataset
                else [(row["dataset_id"], category) for category in row["categories"]]
            )
            for key in keys:
                grouped.setdefault(key, []).append(row)
        for key in sorted(grouped):
            row = next((item for item in grouped[key] if item["sample_id"] not in seen), None)
            if row is None:
                continue
            seen.add(row["sample_id"])
            selected.append(_review_item(row, reason, kind, gloss))

    add(
        [row for row in rows if row["outcome"] == "不一致"],
        "来源标签和算法判定相反，会改变优化方向。",
        "disagreement",
    )
    add(
        [
            row
            for row in rows
            if row["outcome"] == "未判定" and row["risk_layer"] in ("unsafe", "risky")
        ],
        "来源标为有风险，算法未判定。优化方向会转向减少弃判。",
        "abstention",
    )
    add(
        [row for row in rows if row["outcome"] == "运行失败"],
        "没有可对照的算法输出。",
        "run_failure",
        per_dataset=True,
    )
    agreements = [row for row in rows if row["outcome"] == "一致" and row["sample_id"] not in seen]
    used_datasets: set[str] = set()
    used_rules: set[str] = set()
    for _ in range(2):
        remaining = [row for row in agreements if row["sample_id"] not in seen]
        if not remaining:
            break
        row = min(
            remaining,
            key=lambda item: (
                item["dataset_id"] in used_datasets,
                item.get("rule_id") in used_rules,
                item["sample_id"],
            ),
        )
        seen.add(row["sample_id"])
        used_datasets.add(row["dataset_id"])
        if row.get("rule_id"):
            used_rules.add(row["rule_id"])
        reason = "从一致样本抽查对照，不把来源标签当成人工裁决。"
        if row.get("rule_id"):
            kind = "结构规则" if row.get("rule_basis") == "structural_rule" else "规则"
            reason = (
                f"从一致样本抽查对照。算法判不安全来自{kind} {row['rule_id']}，"
                "不是判中了来源类别。来源标签不能当成人工裁决。"
            )
        selected.append(_review_item(row, reason, "agreement_spot_check", gloss))
    return selected


def _review_item(
    row: dict[str, Any],
    reason: str,
    kind: str,
    gloss: dict[str, str],
) -> dict[str, Any]:
    return {
        "sample_id": row["sample_id"],
        "source_sample_id": row["source_sample_id"],
        "dataset_id": row["dataset_id"],
        "categories": list(row["categories"]),
        "rule_id": row.get("rule_id"),
        "finding_reason": row.get("finding_reason"),
        "source_label": row["risk_layer"],
        "detected_verdict": row["detected_verdict"],
        "outcome": row["outcome"],
        "reason": reason,
        "reason_kind": kind,
        "english": row["english"],
        "chinese": gloss.get(row["sample_id"]),
        "key_step": row["key_step"],
        "evidence": row["evidence"],
        "human_adjudication": None,
    }


def _category_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        for category in row["categories"]:
            grouped.setdefault((row["dataset_id"], category), []).append(row)
    summaries: list[dict[str, Any]] = []
    for dataset_id, category in sorted(grouped):
        members = grouped[(dataset_id, category)]
        summaries.append(
            {
                "dataset_id": dataset_id,
                "category": category,
                "denominator": len(members),
                **_counts(members),
            }
        )
    return summaries


def _groups(
    rows: list[dict[str, Any]],
    key: str,
    *,
    extra: str | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        group_key = (str(row[key]),) if extra is None else (str(row[extra]), str(row[key]))
        grouped.setdefault(group_key, []).append(row)
    summaries: list[dict[str, Any]] = []
    for group_key in sorted(grouped):
        members = grouped[group_key]
        summary: dict[str, Any] = {
            "denominator": len(members),
            **_counts(members),
        }
        if extra is None:
            summary[key] = group_key[0]
        else:
            summary[extra] = group_key[0]
            summary[key] = group_key[1]
        summaries.append(summary)
    return summaries


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {outcome: sum(1 for row in rows if row["outcome"] == outcome) for outcome in OUTCOMES}


def _structural_note(rows: list[dict[str, Any]]) -> str:
    agreements = [row for row in rows if row["outcome"] == "一致"]
    if not agreements or any(row.get("rule_basis") != "structural_rule" for row in agreements):
        return ""
    names = "、".join(sorted({str(row.get("rule_id")) for row in agreements}))
    return (
        f" 标成一致的 {len(agreements)} 条，算法判不安全都来自结构规则 {names}，"
        "不是判中了来源类别。"
    )


def _finding_line(item: dict[str, Any]) -> str:
    rule_id = item.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        return "检测发现：没有。"
    reason = item.get("finding_reason")
    if isinstance(reason, str) and reason:
        return f"检测发现：{rule_id}。{reason}"
    return f"检测发现：{rule_id}。"


def _markdown(document: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {document['sample_count']} 条开发集来源标签对照",
        "",
        "这张表只对照来源标签和算法输出。来源标签不是人工裁决，也不是项目参考标注。",
        "fallback 关闭。12 条人工参考没有进入这张表。",
        _direction(document, rows),
        "",
        "## 合计",
        "",
        _count_line(document["counts"], document["sample_count"]),
        "",
        "## 按来源",
        "",
    ]
    for row in document["by_source"]:
        lines.append(f"- {row['dataset_id']}：{_count_line(_counts_of(row), row['denominator'])}")
    lines.extend(
        [
            "",
            "## 按风险类别",
            "",
            "一条样本带有多个风险类别时，会计入每一个类别。类别分母可以重叠，不能把各类别分母相加当作来源分母。",
            "",
        ]
    )
    for row in document["by_category"]:
        lines.append(
            f"- {row['dataset_id']} / {row['category']}：{_count_line(_counts_of(row), row['denominator'])}"
        )
    lines.extend(["", "## 优先复核", ""])
    for item in document["priority_review"]:
        lines.extend(
            [
                f"### {item['sample_id']}",
                "",
                f"来源样本：{item['source_sample_id']}。来源标签：{item['source_label']}。风险类别：{'、'.join(item['categories'])}。算法：{item['detected_verdict'] or '没有输出'}。结果：{item['outcome']}。",
                _finding_line(item),
                f"入选原因：{item['reason']}",
                f"原始英文：{item['english'] or '没有原文'}",
                f"中文对照：{item['chinese'] or '还没有中文对照'}",
                (
                    f"关键步骤：步骤 {item['key_step']}"
                    if item["key_step"] is not None
                    else "关键步骤：来源没有步骤号。回看下面的证据位置。"
                ),
                f"证据位置：{item['evidence'] or '输入里没有这条轨迹'}",
                "人工裁决：没有。来源标签不能代替人工裁决。",
                "",
            ]
        )
    lines.extend(["## 没有运行的模式", ""])
    for mode, status in document["modes"].items():
        if status.get("status") == "not_run":
            lines.append(f"- {mode}：未运行。{status['reason']}")
    lines.append("")
    return "\n".join(lines)


def _direction(document: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    counts = document["counts"]
    structural = _structural_note(rows)
    if counts["不一致"] == 0 and counts["未判定"] > 0:
        return (
            f"没有来源标签和算法判定相反的样本。{document['sample_count']} 条里有 "
            f"{counts['未判定']} 条未判定。优化方向先看算法为什么对来源标为有风险的样本弃判。"
            f"{structural}"
        )
    if counts["不一致"] > 0:
        return (
            f"有 {counts['不一致']} 条来源标签和算法判定相反。"
            "这些分歧会先改变优化方向。"
        )
    return "这批样本没有未判定，也没有和来源标签相反的判定。"


def _count_line(counts: dict[str, int], denominator: int) -> str:
    parts = "，".join(f"{name} {counts[name]}" for name in OUTCOMES)
    return f"分母 {denominator}。{parts}。"


def _counts_of(row: dict[str, Any]) -> dict[str, int]:
    return {outcome: int(row[outcome]) for outcome in OUTCOMES}


def _load_reference(path: Path) -> dict[str, Any]:
    payload = _load_object(path, "missing_source_labels")
    if payload.get("schema_version") != SOURCE_SCHEMA:
        raise RunError(
            "source_label_invalid",
            f"来源标签文件必须是 {SOURCE_SCHEMA}",
            details={"path": str(path)},
        )
    if payload.get("is_project_truth") is True:
        raise RunError(
            "source_label_invalid",
            "来源标签不能被标成项目真值",
            details={"path": str(path)},
        )
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise RunError("source_label_invalid", "来源标签文件没有样本", details={"path": str(path)})
    return {"corpus_version": payload.get("corpus_version"), "samples": samples}


def _load_gloss(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = _load_object(path, "missing_gloss")
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _copy_provider_input(out_dir: Path, source_batch: dict[str, Any]) -> None:
    write_json(out_dir / "provider-input.json", source_batch)


def _load_object(path: Path, code: str) -> dict[str, Any]:
    if not path.is_file():
        raise RunError(code, f"找不到文件：{path}", details={"path": str(path)})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RunError(code, "文件必须是 JSON 对象", details={"path": str(path)})
    return payload


def _not_run(reason: str) -> dict[str, Any]:
    return {"status": "not_run", "display": "未运行", "allow_fallback": False, "reason": reason}
