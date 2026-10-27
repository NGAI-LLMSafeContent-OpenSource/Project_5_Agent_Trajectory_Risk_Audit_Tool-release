# SPDX-License-Identifier: Apache-2.0
"""离线审阅页面渲染。

页面完全由 run 包内的结构化结果渲染为单个静态 HTML 文件：不引用外部
CSS/JS/CDN，不发起运行时网络请求，不需要数据库或常驻 HTTP 服务。
"""

from __future__ import annotations

import html
import json
from typing import Any


_VERDICT_LABELS = {
    "unsafe": "unsafe · 存在风险发现",
    "safe": "safe · 已证明安全",
    "undetermined": "undetermined · 证据不足",
}
_VERDICT_TONES = {"unsafe": "risk", "safe": "ok", "undetermined": "warn"}
_CHECK_LABELS = {
    "findings": "findings · 命中规则",
    "no_findings": "no_findings · 未命中规则",
}
_EVENT_TYPE_LABELS = {
    "message": "消息",
    "thought": "显式 thought",
    "tool_call": "工具调用",
    "tool_result": "工具返回",
    "state_change": "状态变化",
    "unsupported_content": "未支持内容",
}
_LINK_STATUS_LABELS = {
    "linked": "已关联工具调用",
    "positional": "按相邻环境步骤关联",
    "unknown_call_id": "调用 id 无法解析",
    "no_call_id": "缺少调用 id",
}
_SOURCE_SCOPE_LABELS = {
    "trajectory": "轨迹级",
    "source_step": "来源步骤级",
}
_DECLARED_STATUS_LABELS = {
    "success": "success",
    "error": "error",
    "unknown": "未声明",
}
_AVAILABILITY_LABELS = {
    "token_usage": "token 使用量",
    "wall_clock": "耗时",
    "tool_success_rate": "工具成功率",
}

_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f6f3;
  --panel: #ffffff;
  --ink: #191917;
  --muted: #6a6a64;
  --line: #e3e2dc;
  --line-strong: #c9c8c0;
  --accent: #1f4e79;
  --accent-soft: #eef3f8;
  --risk: #b42318;
  --risk-soft: #fdeeec;
  --warn: #8a5300;
  --warn-soft: #fff5e6;
  --ok: #2f6b3f;
  --ok-soft: #edf6ef;
  --code-bg: #f3f2ed;
  --radius: 10px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC",
    "Microsoft YaHei", sans-serif;
  -webkit-text-size-adjust: 100%;
}
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 12.5px;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 40px 24px 72px; }
.kicker {
  margin: 0 0 8px;
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
}
h1 { margin: 0 0 8px; font-size: 26px; line-height: 1.25; letter-spacing: -.01em; }
.lede { margin: 0 0 18px; max-width: 72ch; color: var(--muted); font-size: 14px; }
.badge-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 20px; }
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 12.5px;
  font-weight: 600;
}
.badge--risk { background: var(--risk-soft); color: var(--risk); border-color: #f3c9c4; }
.badge--warn { background: var(--warn-soft); color: var(--warn); border-color: #f0d9b0; }
.badge--ok { background: var(--ok-soft); color: var(--ok); border-color: #c4dcc9; }
.badge--neutral { background: var(--code-bg); color: var(--ink); border-color: var(--line-strong); }
.run-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px 20px;
  margin: 0;
  padding: 16px 18px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.run-meta div { min-width: 0; }
.run-meta dt { font-size: 11.5px; color: var(--muted); letter-spacing: .03em; }
.run-meta dd { margin: 3px 0 0; font-size: 13.5px; overflow-wrap: anywhere; }
.panel {
  margin: 20px 0 0;
  padding: 20px 22px 22px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.panel > h2 {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 0 0 4px;
  font-size: 17px;
  letter-spacing: -.01em;
}
.panel > h2 .index {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.panel > .panel-note { margin: 0 0 16px; color: var(--muted); font-size: 13px; }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: #fdfdfb; }
.card dt { font-size: 12px; color: var(--muted); }
.card dd {
  margin: 5px 0 0;
  font-size: 21px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.card .hint { display: block; margin-top: 3px; font-size: 11.5px; font-weight: 400; color: var(--muted); }
.verdict { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.35fr); gap: 14px; }
@media (max-width: 780px) { .verdict { grid-template-columns: 1fr; } }
.verdict__main {
  border: 1px solid var(--line);
  border-left: 4px solid var(--line-strong);
  border-radius: 8px;
  padding: 14px 16px;
}
.verdict__main[data-tone="risk"] { border-left-color: var(--risk); background: var(--risk-soft); }
.verdict__main[data-tone="warn"] { border-left-color: #d39229; background: var(--warn-soft); }
.verdict__main[data-tone="ok"] { border-left-color: var(--ok); background: var(--ok-soft); }
.verdict__label { margin: 0; font-size: 11.5px; letter-spacing: .05em; text-transform: uppercase; color: var(--muted); }
.verdict__value { margin: 4px 0 8px; font-size: 19px; font-weight: 600; }
.verdict__main p { margin: 0; font-size: 13px; }
.kv-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.kv-table th, .kv-table td { padding: 7px 0; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.kv-table th { width: 132px; color: var(--muted); font-weight: 500; }
.kv-table tr:last-child th, .kv-table tr:last-child td { border-bottom: none; }
.finding {
  margin: 14px 0 0;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--risk);
  border-radius: 8px;
  background: #fffdfc;
}
.finding:target { outline: 2px solid var(--risk); outline-offset: 3px; }
.finding__head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.finding__head h3 { margin: 0; font-size: 14.5px; font-family: ui-monospace, Menlo, Consolas, monospace; }
.finding__reason { margin: 10px 0 6px; font-size: 14px; }
.chip {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--code-bg);
  font-size: 11.5px;
  font-weight: 500;
  white-space: nowrap;
}
.chip--risk { background: var(--risk-soft); border-color: #f3c9c4; color: var(--risk); }
.chip--ghost { background: transparent; color: var(--muted); }
.subtle { color: var(--muted); font-size: 12.5px; }
.empty {
  margin: 12px 0 0;
  padding: 12px 14px;
  border: 1px dashed var(--line-strong);
  border-radius: 8px;
  background: #fbfbf9;
  font-size: 13.5px;
}
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 9px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: #faf9f6; font-size: 11.5px; font-weight: 600; color: var(--muted); white-space: nowrap; }
tr:last-child td { border-bottom: none; }
td { overflow-wrap: anywhere; }
.timeline { list-style: none; margin: 12px 0 0; padding: 0 0 0 22px; border-left: 1px solid var(--line-strong); }
.event { position: relative; padding: 14px 0 16px 2px; border-bottom: 1px dashed var(--line); }
.event:last-child { border-bottom: none; }
.event::before {
  content: "";
  position: absolute;
  left: -26px;
  top: 22px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--panel);
  border: 2px solid var(--line-strong);
}
.event--flagged::before { border-color: var(--risk); background: var(--risk); }
.event:target { background: var(--warn-soft); border-radius: 6px; }
.event__head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.event__seq { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.event__type { font-size: 12.5px; font-weight: 600; }
.event__type code { margin-left: 6px; font-weight: 400; color: var(--muted); }
.event__actor { font-size: 12px; color: var(--muted); }
.event__id { padding: 1px 6px; border: 1px solid var(--line); border-radius: 4px; background: var(--code-bg); }
.event__source { margin: 6px 0 8px; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
.event__source code { padding: 1px 4px; background: var(--code-bg); border-radius: 3px; color: var(--ink); }
.event__body { font-size: 13.5px; }
.pre-wrap { white-space: pre-wrap; word-break: break-word; margin: 0; }
pre.text { padding: 10px 12px; background: #fbfbf9; border: 1px solid var(--line); border-radius: 8px; }
.fold { margin: 8px 0 0; }
.fold > summary { cursor: pointer; font-size: 12.5px; color: var(--accent); font-weight: 500; }
.fold pre {
  margin: 8px 0 0;
  padding: 10px 12px;
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 8px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.fold--raw > summary { color: var(--muted); }
.warn-line {
  margin: 8px 0;
  padding: 8px 12px;
  background: var(--warn-soft);
  border: 1px solid #f0d9b0;
  border-radius: 8px;
  color: #6b3f00;
  font-size: 12.5px;
}
.hint { margin: 0 0 6px; color: var(--muted); font-size: 12.5px; }
.kv { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0; font-size: 13px; }
.kv > span:first-child { min-width: 78px; color: var(--muted); }
.availability { margin: 14px 0 0; padding: 0; list-style: none; }
.availability li { display: flex; flex-wrap: wrap; gap: 8px; padding: 7px 0; border-top: 1px solid var(--line); font-size: 13px; }
.availability .label { min-width: 110px; color: var(--muted); }
.availability .state { font-weight: 600; }
.availability .state--unavailable { color: var(--warn); }
.availability .state--available { color: var(--ok); }
.artifacts td:first-child { white-space: nowrap; font-weight: 500; }
footer { margin: 28px 0 0; color: var(--muted); font-size: 12px; }
@media print {
  body { background: #fff; }
  .wrap { max-width: none; padding: 0; }
  .panel, .run-meta { break-inside: avoid; }
}
"""


def render_review_page(
    *,
    run: dict[str, Any],
    trajectory: dict[str, Any],
    audit_result: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    sample = trajectory["sample"]
    sample_id = _esc(sample["sample_id"])
    verdict = audit_result["verdict"]
    check_status = audit_result["checks"]["status"]
    tone = _VERDICT_TONES.get(verdict["value"], "warn")
    provider = audit_result["provider"]

    findings_by_event: dict[str, list[dict[str, Any]]] = {}
    for finding in audit_result["findings"]:
        for event_id in finding["origin_event_ids"]:
            findings_by_event.setdefault(event_id, []).append(finding)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>轨迹审计 · {sample_id} · {_esc(run["run_id"])}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="kicker">Agent 轨迹风险审计 · 离线审阅</p>
  <h1>{sample_id}</h1>
  <p class="lede">本页由本次 run 包内的结构化结果直接渲染为静态文件，不依赖数据库、常驻 HTTP 服务、外部 CDN 或运行时网络请求。</p>
  <div class="badge-row">
    <span class="badge badge--{tone}" data-verdict="{_esc(verdict["value"])}">判定 · {_esc(_VERDICT_LABELS.get(verdict["value"], verdict["value"]))}</span>
    <span class="badge badge--neutral" data-check-status="{_esc(check_status)}">检查 · {_esc(_CHECK_LABELS.get(check_status, check_status))}</span>
    <span class="badge badge--neutral">provider · {_esc(provider["id"])}</span>
    <span class="badge badge--neutral">来源 · {_esc(sample["source_format"])}</span>
  </div>
  <dl class="run-meta">
    <div><dt>run 身份</dt><dd><code>{_esc(run["run_id"])}</code></dd></div>
    <div><dt>样本 id</dt><dd><code>{sample_id}</code></dd></div>
    <div><dt>来源数据版本</dt><dd><code>{_esc(trajectory["source"]["dataset_id"])}</code><br>{_esc(trajectory["source"]["dataset_version"])}</dd></div>
    <div><dt>输入 sha256</dt><dd><code>{_esc(sample["input_sha256"][:16])}…</code></dd></div>
    <div><dt>完成时间</dt><dd>{_esc(run["completed_at"])}</dd></div>
  </dl>
</header>

<section class="panel" id="summary">
  <h2><span class="index">01</span>概览</h2>
  <p class="panel-note">统计口径来自本次归一轨迹与审计结果；来源未提供的指标保持为空，不使用估算值。</p>
  {_render_summary_cards(trajectory, audit_result, metrics)}
</section>

<section class="panel" id="source-layers">
  <h2><span class="index">02</span>来源事实、reference 与 detected 分层</h2>
  <p class="panel-note"><code>source</code> 是来源数据集提供的事实，<code>reference</code> 是项目人工确认标注（当前未建立时为 absent），<code>detected</code> 是审计 provider 的自动结果。三者不互相冒充。</p>
  {_render_source_layers(trajectory)}
</section>

<section class="panel" id="audit">
  <h2><span class="index">03</span>审计判定与风险发现</h2>
  <p class="panel-note"><code>no_findings</code> 只表示本次检查没有命中规则，不等于判定为 <code>safe</code>。判定与检查状态分别记录。</p>
  <div class="verdict">
    <div class="verdict__main" data-tone="{tone}">
      <p class="verdict__label">判定 verdict · detected</p>
      <p class="verdict__value">{_esc(verdict["value"])}</p>
      <p>{_esc(verdict["reason"])}</p>
      <p class="subtle" style="margin-top:8px">basis · <code>{_esc(verdict["basis"])}</code> · 检查状态 · <code>{_esc(check_status)}</code></p>
    </div>
    <div>
      {_render_provider_table(provider, audit_result["checks"])}
    </div>
  </div>
  {_render_findings(audit_result)}
  {_render_audit_context(audit_result)}
</section>

<section class="panel" id="tools">
  <h2><span class="index">04</span>工具交互</h2>
  <p class="panel-note">工具名称、调用参数、返回与关联状态；未配对调用与无法解析的返回会如实标注。</p>
  {_render_tools(trajectory)}
</section>

<section class="panel" id="timeline">
  <h2><span class="index">05</span>事件时间线</h2>
  <p class="panel-note">按归一顺序展示可观察记录。每个事件保留稳定身份、来源数据版本、原始记录定位、可选 source_step_id 和原始字段；点击风险发现中的事件链接可直接跳转。</p>
  {_render_timeline(trajectory, findings_by_event)}
</section>

<section class="panel" id="run-bundle">
  <h2><span class="index">06</span>run 包与指标口径</h2>
  <p class="panel-note">本次运行的不可变产物与指标可用性。</p>
  {_render_artifacts(run)}
  {_render_availability(metrics)}
</section>

<footer>
  schema · run {_esc(run["schema_version"])} · trajectory {_esc(trajectory["schema_version"])} ·
  audit-result {_esc(audit_result["schema_version"])} · metrics {_esc(metrics["schema_version"])} ·
  tool trajectory_audit {_esc(run["tool"]["version"])}
</footer>
</div>
<script type="application/json" id="audit-result-data">{_script_json(audit_result)}</script>
<script type="application/json" id="trajectory-data">{_script_json(trajectory)}</script>
</body>
</html>
'''


def _render_source_layers(trajectory: dict[str, Any]) -> str:
    source = trajectory["source"]
    labels = trajectory.get("source_labels", [])
    reference = trajectory.get("reference", {})
    label_rows = []
    for label in labels:
        event_links = " ".join(
            f'<a href="#{_esc(event_id)}">{_esc(event_id)}</a>'
            for event_id in label.get("event_ids", [])
        ) or '<span class="subtle">—</span>'
        categories = "".join(
            f'<span class="chip chip--ghost">{_esc(category)}</span>'
            for category in label.get("categories", [])
        ) or '<span class="subtle">—</span>'
        mapping = label.get("mapping", {})
        label_rows.append(
            "<tr>"
            f'<td><code>{_esc(label["label_id"])}</code></td>'
            f'<td>{_esc(_SOURCE_SCOPE_LABELS.get(label["scope"], label["scope"]))}'
            f'<div class="subtle"><code>{_esc(label["scope"])}</code></div></td>'
            f'<td><code>{_esc(label.get("source_step_id") if label.get("source_step_id") is not None else "—")}</code></td>'
            f'<td>{"已定位" if label.get("located") else "未定位"}</td>'
            f'<td>{_esc(label.get("verdict") or "—")}</td>'
            f'<td>{categories}</td>'
            f'<td>{event_links}</td>'
            f'<td>{_esc(label.get("reason") or "—")}'
            f'<div class="subtle">mapping · <code>{_esc(mapping.get("status", "—"))}</code>'
            f' · <code>{_esc(mapping.get("version", "—"))}</code></div></td>'
            f'<td>{_fold("来源标签原文", label.get("raw"))}</td>'
            "</tr>"
        )
    if label_rows:
        label_table = (
            '<div class="table-wrap" style="margin-top:12px"><table>'
            "<thead><tr><th>标签</th><th>作用范围</th><th>源步骤</th><th>定位</th>"
            "<th>来源判定</th><th>类别</th><th>对应事件</th><th>理由 / 映射</th><th>原文</th></tr></thead>"
            f'<tbody>{"".join(label_rows)}</tbody></table></div>'
        )
    else:
        label_table = '<p class="empty">本次轨迹没有来源标签。</p>'

    reference_status = reference.get("status", "absent")
    reference_count = len(reference.get("labels") or [])
    return (
        '<table class="kv-table">'
        f'<tr><th>source 数据集</th><td><code>{_esc(source.get("dataset_id"))}</code></td></tr>'
        f'<tr><th>source 版本</th><td><code>{_esc(source.get("dataset_version"))}</code></td></tr>'
        f'<tr><th>原始记录</th><td>record[{_esc(source.get("record", {}).get("index"))}] · '
        f'<code>{_esc(source.get("record", {}).get("json_path"))}</code> · '
        f'sha256 <code>{_esc(source.get("record", {}).get("sha256", "")[:16])}…</code></td></tr>'
        f'<tr><th>source 步骤</th><td>{len(trajectory.get("source_steps", []))} 个来源步骤</td></tr>'
        f'<tr><th>reference</th><td><code>{_esc(reference_status)}</code> · {reference_count} 条项目参考标注</td></tr>'
        f'<tr><th>detected</th><td>审计判定与风险发现见下一节；三者分层保存，不互相冒充</td></tr>'
        "</table>"
        f"{label_table}"
    )


def _render_summary_cards(
    trajectory: dict[str, Any], audit_result: dict[str, Any], metrics: dict[str, Any]
) -> str:
    cards = [
        ("事件", metrics["events"]["total"], f"{len(trajectory['events'])} 条归一事件"),
        ("来源标签", len(trajectory.get("source_labels", [])), "source 层，不是 detected"),
        ("工具调用", metrics["tools"]["calls"], "assistant tool_calls"),
        ("工具返回", metrics["tools"]["results"], f"{metrics['tools']['unlinked_results']} 条未关联"),
        ("未支持内容", metrics["events"]["unsupported_count"], "已保留，未静默丢弃"),
        ("风险发现", metrics["audit"]["finding_count"], audit_result["checks"]["status"]),
        ("判定", audit_result["verdict"]["value"], audit_result["verdict"]["basis"]),
    ]
    items = "".join(
        f'<li class="card"><dl><dt>{_esc(label)}</dt>'
        f'<dd>{_esc(value)}<span class="hint">{_esc(hint)}</span></dd></dl></li>'
        for label, value, hint in cards
    )
    return f'<ul class="cards">{items}</ul>'


def _render_provider_table(provider: dict[str, Any], checks: dict[str, Any]) -> str:
    rows = [
        ("provider kind", provider["kind"]),
        ("provider id", provider["id"]),
        ("版本", provider["version"]),
        ("选择方式", provider["selection"]),
        ("实现", provider["implementation"]),
        ("已执行规则", ", ".join(checks["rules_evaluated"]) or "—"),
        ("说明", provider["description"]),
    ]
    body = "".join(
        f'<tr><th>{_esc(label)}</th><td><code>{_esc(value)}</code></td></tr>'
        for label, value in rows
    )
    return f'<table class="kv-table">{body}</table>'


def _render_findings(audit_result: dict[str, Any]) -> str:
    findings = audit_result["findings"]
    if not findings:
        return (
            '<p class="empty">本次检查没有产生风险发现。'
            "<code>no_findings</code> 不是 <code>safe</code>：当前只有受控 baseline，"
            "且没有来源安全真值或项目参考标注。</p>"
        )

    cards = []
    for finding in findings:
        categories = "".join(
            f'<span class="chip chip--risk">{_esc(category)}</span>'
            for category in finding["categories"]
        )
        origin_links = " ".join(
            f'<a href="#{_esc(event_id)}">{_esc(event_id)}</a>'
            for event_id in finding["origin_event_ids"]
        )
        evidence_rows = "".join(
            "<tr>"
            f'<td><code>{_esc(item["kind"])}</code></td>'
            f'<td>{_event_link(item.get("event_id"))}</td>'
            f'<td><code>{_esc(item.get("field_path") or "—")}</code></td>'
            f'<td>{_esc(item.get("excerpt") or "—")}</td>'
            f'<td><code>{_esc(item.get("json_path") or "—")}</code></td>'
            "</tr>"
            for item in finding["evidence"]
        )
        affected = (
            "、".join(finding["affected_event_ids"]) if finding["affected_event_ids"] else "无"
        )
        cards.append(
            f'<article class="finding" id="{_esc(finding["finding_id"])}" '
            f'data-finding-id="{_esc(finding["finding_id"])}">'
            f'<header class="finding__head"><h3>{_esc(finding["finding_id"])}</h3>{categories}'
            f'<span class="chip chip--ghost">scope: {_esc(finding["scope"])}</span>'
            f'<span class="chip chip--ghost">{"已定位" if finding["located"] else "未定位"}</span></header>'
            f'<p class="finding__reason">{_esc(finding["reason"])}</p>'
            f'<p class="subtle">规则 <code>{_esc(finding["rule"]["id"])}</code> '
            f'v{_esc(finding["rule"]["version"])} · '
            f'判定来源 <code>{_esc(finding["rule"].get("basis", "structural_rule"))}</code> · '
            f'起源事件 {origin_links} · '
            f"受影响事件 {_esc(affected)}</p>"
            '<div class="table-wrap" style="margin-top:10px"><table>'
            "<thead><tr><th>证据类型</th><th>事件</th><th>字段</th><th>证据片段</th><th>源定位</th></tr></thead>"
            f"<tbody>{evidence_rows}</tbody></table></div></article>"
        )
    return "".join(cards)


def _render_audit_context(audit_result: dict[str, Any]) -> str:
    """渲染事实层、policy 检查、Judge 与 degraded 记录。"""
    blocks: list[str] = []

    degraded = audit_result.get("degraded")
    if degraded:
        original = degraded.get("original_failure") or {}
        blocks.append(
            '<div class="finding" data-degraded="true" style="border-color:#b45309">'
            '<header class="finding__head"><h3>degraded · 显式 fallback</h3>'
            '<span class="chip chip--risk">fallback</span></header>'
            f'<p class="finding__reason">请求的 provider <code>{_esc(degraded.get("from_provider"))}</code> '
            f'不可用，调用方显式允许 fallback，改由 <code>{_esc(degraded.get("to_provider"))}</code> 审计。'
            f'原始失败已保留：<code>{_esc(original.get("code") or "unknown")}</code> · '
            f'{_esc(original.get("message") or "—")}</p></div>'
        )

    judge = audit_result.get("judge") or {}
    judge_provider = judge.get("provider") or {}
    blocks.append(
        '<p class="panel-note" data-judge-status="'
        f'{_esc(judge.get("status") or "unknown")}">语义 Judge · '
        f'<code>{_esc(judge_provider.get("id") or "none")}</code> · '
        f'状态 <code>{_esc(judge.get("status") or "unknown")}</code> · '
        "不参与判定（influences_verdict=false）: "
        f'{_esc(judge.get("reason") or "未运行")}</p>'
    )

    policy = audit_result.get("policy") or {}
    document = policy.get("document") or {}
    binding = policy.get("binding") or {}
    status = policy.get("status") or "absent"
    header = (
        '<h3 class="subtle" style="margin:14px 0 6px">Policy 检查</h3>'
        '<p class="panel-note" data-policy-status="'
        f'{_esc(status)}">'
        f'policy <code>{_esc(document.get("policy_id") or "—")}</code>'
        f'@{_esc(document.get("policy_version") or "—")} · '
        f'状态 <code>{_esc(status)}</code> · '
        f'绑定 <code>{_esc(binding.get("reason") or "—")}</code></p>'
    )
    check_rows = "".join(
        "<tr>"
        f'<td><code>{_esc(check.get("scenario"))}</code></td>'
        f'<td><code>{_esc(check.get("check"))}</code></td>'
        f'<td><code>{_esc(check.get("status"))}</code></td>'
        f'<td><code>{_esc(", ".join(check.get("rule_ids") or []) or "—")}</code></td>'
        f'<td>{_esc(check.get("reason") or "—")}</td>'
        "</tr>"
        for check in policy.get("checks") or []
    )
    check_body = check_rows or '<tr><td colspan="5">—</td></tr>'
    blocks.append(
        header
        + '<div class="table-wrap"><table>'
        "<thead><tr><th>场景</th><th>检查项</th><th>状态</th><th>规则</th><th>理由</th></tr></thead>"
        f"<tbody>{check_body}</tbody></table></div>"
    )

    facts = audit_result.get("facts") or []
    fact_rows = "".join(
        "<tr>"
        f'<td><code>{_esc(fact.get("kind"))}</code></td>'
        f'<td>{_event_link(fact.get("event_id"))}</td>'
        f'<td>{_esc(fact.get("detail"))}</td>'
        "</tr>"
        for fact in facts
    )
    fact_body = fact_rows or '<tr><td colspan="3">—</td></tr>'
    blocks.append(
        '<h3 class="subtle" style="margin:14px 0 6px">确定性事实（不单独构成风险发现）</h3>'
        '<div class="table-wrap"><table>'
        "<thead><tr><th>事实类型</th><th>事件</th><th>说明</th></tr></thead>"
        f"<tbody>{fact_body}</tbody></table></div>"
    )
    return "".join(blocks)


def _render_tools(trajectory: dict[str, Any]) -> str:
    events = trajectory["events"]
    results_by_call = {
        event["content"].get("call_event_id"): event
        for event in events
        if event["type"] == "tool_result"
    }
    rows = []
    for event in events:
        if event["type"] != "tool_call":
            continue
        content = event["content"]
        tool = content.get("tool") or "（空工具名）"
        result = results_by_call.get(event["event_id"])
        if result is None:
            result_cell = '<span class="chip chip--risk">未配对</span> <span class="subtle">没有工具返回</span>'
            status_cell = '<span class="chip chip--ghost">call only</span>'
        else:
            status_cell = (
                f'<a class="chip chip--ghost" href="#{_esc(result["event_id"])}">'
                f'{_esc(_LINK_STATUS_LABELS.get(result["content"].get("link_status"), result["content"].get("link_status")))}</a>'
                f'<div class="subtle">declared: {_esc(result["content"].get("declared_status"))}</div>'
            )
            result_cell = (
                f'<div>{_esc(_DECLARED_STATUS_LABELS.get(result["content"].get("declared_status"), "未声明"))}</div>'
                f'{_fold("工具返回", result["content"].get("output"), open_by_default=_is_short(result["content"].get("output")))}'
            )
        rows.append(
            "<tr>"
            f'<td><code>{_esc(tool)}</code>'
            f'<div class="subtle">{_esc(content.get("call_id") or "无调用 id")}</div></td>'
            f'<td><a href="#{_esc(event["event_id"])}">{_esc(event["event_id"])}</a>'
            f'<div class="subtle"><code>{_esc(event["source"]["json_path"])}</code></div></td>'
            f'<td>{_fold("参数 · " + str(content.get("arguments_status")), content.get("arguments_raw"), open_by_default=_is_short(content.get("arguments_raw")))}</td>'
            f"<td>{result_cell}</td>"
            f"<td>{status_cell}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty">本次轨迹没有工具调用。</p>'
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>工具</th><th>调用事件</th><th>参数</th><th>返回</th><th>状态</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_timeline(
    trajectory: dict[str, Any], findings_by_event: dict[str, list[dict[str, Any]]]
) -> str:
    items = "".join(
        _render_event(event, findings_by_event.get(event["event_id"], []))
        for event in trajectory["events"]
    )
    return f'<ol class="timeline">{items}</ol>'


def _render_event(event: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    event_type = event["type"]
    content = event["content"]
    source = event["source"]
    classes = f"event event--{event_type.replace('_', '-')}"
    if findings:
        classes += " event--flagged"
    finding_chips = "".join(
        f'<a class="chip chip--risk" href="#{_esc(finding["finding_id"])}">'
        f'发现 {_esc(finding["finding_id"])} · {_esc(finding["categories"][0])}</a>'
        for finding in findings
    )
    return (
        f'<li class="{classes}" id="{_esc(event["event_id"])}" '
        f'data-event-id="{_esc(event["event_id"])}" data-event-type="{_esc(event_type)}">'
        '<div class="event__head">'
        f'<span class="event__seq">#{event["sequence"]:02d}</span>'
        f'<span class="event__type">{_esc(_EVENT_TYPE_LABELS.get(event_type, event_type))}'
        f"<code>{_esc(event_type)}</code></span>"
        f'<span class="event__actor">actor · {_esc(event["actor"])}</span>'
        f'<code class="event__id">{_esc(event["event_id"])}</code>'
        f"{finding_chips}</div>"
        f'<div class="event__source">record[{source["record_index"]}] · '
        f'<code>{_esc(source["json_path"])}</code> · '
        f'step <code>{_esc(event.get("source_step_id") if event.get("source_step_id") is not None else "—")}</code> · '
        f'<code>{_esc(source.get("dataset_id"))}@{_esc(source.get("dataset_version"))}</code> · '
        f'sha256 <code>{_esc(source["record_sha256"][:12])}…</code></div>'
        f'<div class="event__body">{_render_event_body(event)}</div>'
        f'{_fold("原始记录 / 原始片段", event["raw"])}'
        "</li>"
    )


def _render_event_body(event: dict[str, Any]) -> str:
    content = event["content"]
    event_type = event["type"]

    if event_type == "message":
        return (
            f'<p class="subtle" style="margin:0 0 6px">role · <code>{_esc(content.get("role"))}</code></p>'
            f'{_text_block(content.get("text", ""))}'
        )
    if event_type == "thought":
        return (
            '<p class="hint">显式 thought 文本字段：这是可观察的模型输出，不是模型内部思维链。</p>'
            f'{_text_block(content.get("text", ""))}'
        )
    if event_type == "tool_call":
        status = content.get("arguments_status")
        warning = ""
        if status != "object":
            warning = (
                f'<p class="warn-line">arguments_status = <code>{_esc(status)}</code>'
                f'{" · " + _esc(content.get("arguments_error")) if content.get("arguments_error") else ""}</p>'
            )
        return (
            f'<div class="kv"><span>工具</span><code>{_esc(content.get("tool") or "（空工具名）")}</code></div>'
            f'<div class="kv"><span>调用 id</span><code>{_esc(content.get("call_id") or "—")}</code></div>'
            f"{warning}<div class=\"kv\"><span>参数</span></div>"
            f'{_fold("arguments", content.get("arguments_raw"), open_by_default=_is_short(content.get("arguments_raw")))}'
        )
    if event_type == "tool_result":
        call_event_id = content.get("call_event_id")
        return (
            f'<div class="kv"><span>关联调用</span>{_event_link(call_event_id)}</div>'
            f'<div class="kv"><span>链接状态</span><code>{_esc(content.get("link_status"))}</code></div>'
            f'<div class="kv"><span>返回状态</span><code>{_esc(content.get("declared_status"))}</code></div>'
            f'{_fold("工具返回", content.get("output"), open_by_default=_is_short(content.get("output")))}'
        )
    if event_type == "unsupported_content":
        return (
            '<p class="warn-line">未支持内容已保留，未静默丢弃。'
            f'原因 <code>{_esc(content.get("reason"))}</code> · '
            f'类型 <code>{_esc(content.get("part_type") if content.get("part_type") is not None else "—")}</code></p>'
        )
    if event_type == "state_change":
        return (
            f'<div class="kv"><span>状态</span><code>{_esc(content.get("state"))}</code></div>'
            f'<div class="kv"><span>依据</span><code>{_esc(content.get("basis"))}</code></div>'
            f'{_fold("状态声明原文", content.get("declaration"))}'
        )
    return f'<pre class="pre-wrap">{_esc(_pretty(content))}</pre>'


def _render_artifacts(run: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{_esc(name)}</code></td><td><code>{_esc(path)}</code></td></tr>"
        for name, path in run["artifacts"].items()
    )
    return (
        '<div class="table-wrap artifacts" style="margin-top:12px"><table>'
        "<thead><tr><th>产物</th><th>run 包内相对路径</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _render_availability(metrics: dict[str, Any]) -> str:
    rows = []
    for key, label in _AVAILABILITY_LABELS.items():
        entry = metrics["availability"].get(key, {"status": "unavailable", "reason": "未记录"})
        if entry.get("status") == "available":
            value = entry.get("value")
            text = f"{value}" if value is not None else "可用"
            state_class = "state--available"
            detail = entry.get("basis", "")
        else:
            text = "数据源未提供"
            state_class = "state--unavailable"
            detail = entry.get("reason", "")
        rows.append(
            f'<li><span class="label">{_esc(label)}</span>'
            f'<span class="state {state_class}">{_esc(text)}</span>'
            f'<span class="subtle">{_esc(detail)}</span></li>'
        )
    return f'<ul class="availability">{"".join(rows)}</ul>'


def render_batch_page(*, batch: dict[str, Any]) -> str:
    """批次索引页：同一次批量运行的逐样本入口与失败汇总。"""
    status = batch["status"]
    tone = "ok" if status == "complete" else "warn"
    adapter = batch["adapter"]
    provider = batch["provider"]
    source = batch["source"]
    records = batch["records"]
    samples = batch["samples"]
    failures = batch["failures"]

    sample_rows = "".join(
        "<tr>"
        f'<td><a href="{_esc(sample["run_dir"])}/index.html"><code>{_esc(sample["sample_id"])}</code></a></td>'
        f'<td>record[{_esc(sample["record_index"])}]</td>'
        f'<td><span class="chip chip--risk">{_esc(sample["verdict"])}</span></td>'
        f'<td><code>{_esc(sample["check_status"])}</code></td>'
        f'<td>{_esc(sample["event_count"])}</td>'
        f'<td>{_esc(sample["source_steps"])}</td>'
        f'<td>{_esc(sample["source_labels"])}（已定位 {_esc(sample["located_source_labels"])}）</td>'
        f'<td><code>{_esc(sample["run_id"])}</code></td>'
        "</tr>"
        for sample in samples
    )
    samples_table = (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>样本</th><th>来源记录</th><th>判定</th><th>检查状态</th>"
        "<th>事件</th><th>来源步骤</th><th>来源标签</th><th>run 身份</th></tr></thead>"
        f"<tbody>{sample_rows}</tbody></table></div>"
        if sample_rows
        else '<p class="empty">本批次没有成功生成的样本。</p>'
    )

    failure_rows = "".join(
        "<tr>"
        f'<td><code>{_esc(failure["sample_id"])}</code></td>'
        f'<td>record[{_esc(failure["record_index"])}]</td>'
        f'<td><code>{_esc(failure["stage"])}</code></td>'
        f'<td><code>{_esc(failure["code"])}</code></td>'
        f'<td>{_esc(failure["reason"])}</td>'
        f'{_fold("失败详情", failure.get("details"))}'
        "</tr>"
        for failure in failures
    )
    failures_table = (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>样本</th><th>来源记录</th><th>阶段</th><th>代码</th><th>原因</th><th>详情</th></tr></thead>"
        f"<tbody>{failure_rows}</tbody></table></div>"
        if failure_rows
        else '<p class="empty">本批次没有逐样本失败。</p>'
    )

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>批量轨迹审计 · {_esc(batch["batch_id"])}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="kicker">Agent 轨迹风险审计 · 批量离线审阅</p>
  <h1>批量运行 · {_esc(source["dataset_id"])}</h1>
  <p class="lede">本页是批次索引；每个样本仍生成与单条运行相同形态的 run 包。页面不依赖数据库、常驻 HTTP 服务、外部 CDN 或运行时网络请求。</p>
  <div class="badge-row">
    <span class="badge badge--{tone}" data-batch-status="{_esc(status)}">批次状态 · {_esc(status)}</span>
    <span class="badge badge--neutral">adapter · {_esc(adapter["id"])}</span>
    <span class="badge badge--neutral">provider · {_esc(provider["id"])}</span>
    <span class="badge badge--neutral">来源 · {_esc(source["dataset_id"])}</span>
  </div>
  <dl class="run-meta">
    <div><dt>批次身份</dt><dd><code>{_esc(batch["batch_id"])}</code></dd></div>
    <div><dt>来源版本</dt><dd><code>{_esc(source["dataset_version"])}</code></dd></div>
    <div><dt>来源记录</dt><dd>{_esc(records["total"])} 条 · 成功 {_esc(records["converted"])} · 失败 {_esc(records["failed"])}</dd></div>
    <div><dt>样本</dt><dd>完成 {_esc(records["samples_completed"])} · 失败 {_esc(records["samples_failed"])}</dd></div>
    <div><dt>完成时间</dt><dd>{_esc(batch["completed_at"])}</dd></div>
  </dl>
</header>

<section class="panel" id="samples">
  <h2><span class="index">01</span>逐样本结果</h2>
  <p class="panel-note">每个样本链接到自己的 <code>index.html</code>；单条失败不会丢弃其他已完成结果。</p>
  {samples_table}
</section>

<section class="panel" id="failures">
  <h2><span class="index">02</span>逐样本失败</h2>
  <p class="panel-note">失败携带样本身份、阶段、机器可读代码和原因，并同时写入 <code>failures.json</code>。</p>
  {failures_table}
</section>

<section class="panel" id="layers">
  <h2><span class="index">03</span>分层口径</h2>
  <p class="panel-note"><code>source</code> 来源事实、<code>reference</code> 项目参考标注与 <code>detected</code> 自动检测结果在样本 run 包中分属不同结构；批次页只汇总逐样本结果与失败。</p>
  <ul class="availability">
    <li><span class="label">source 层</span><span class="state state--available">来源数据集</span><span class="subtle">原始记录、步骤与标签事实</span></li>
    <li><span class="label">reference 层</span><span class="state state--unavailable">按样本</span><span class="subtle">当前 smoke 样本未建立项目参考标注</span></li>
    <li><span class="label">detected 层</span><span class="state state--available">provider</span><span class="subtle">判定、检查状态与风险发现</span></li>
  </ul>
</section>

<footer>
  schema · batch {_esc(batch["schema_version"])} · adapter {_esc(adapter["id"])}@{_esc(adapter["version"])} ·
  tool trajectory_audit {_esc(batch["tool"]["version"])}
</footer>
</div>
<script type="application/json" id="batch-data">{_script_json(batch)}</script>
</body>
</html>
'''


def _event_link(event_id: str | None) -> str:
    if not event_id:
        return '<span class="subtle">—</span>'
    return f'<a href="#{_esc(event_id)}">{_esc(event_id)}</a>'


def _text_block(text: Any, limit: int = 420) -> str:
    value = "" if text is None else str(text)
    if not value:
        return '<p class="subtle">（空文本）</p>'
    if len(value) > limit:
        return (
            f'<details class="fold"><summary>正文 {len(value)} 字符 · 默认折叠，展开阅读</summary>'
            f'<pre class="pre-wrap">{_esc(value)}</pre></details>'
        )
    return f'<pre class="pre-wrap text">{_esc(value)}</pre>'


def _fold(summary: str, value: Any, *, open_by_default: bool = False) -> str:
    open_attr = " open" if open_by_default else ""
    return (
        f'<details class="fold fold--raw"{open_attr}><summary>{_esc(summary)}</summary>'
        f"<pre>{_esc(_pretty(value))}</pre></details>"
    )


def _is_short(value: Any, limit: int = 200) -> bool:
    """短内容默认展开，长内容默认折叠。"""
    return len(_pretty(value)) <= limit


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _script_json(value: dict[str, Any]) -> str:
    """内联 JSON；转义 < 以避免提前闭合 script 标签。"""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)
