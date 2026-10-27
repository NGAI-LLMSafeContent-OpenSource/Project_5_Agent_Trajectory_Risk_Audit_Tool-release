# SPDX-License-Identifier: Apache-2.0
"""参考标注审阅包的离线页面与说明文档。

页面完全由审阅包内的结构化结果渲染为单个静态 HTML 文件：不引用外部
CSS/JS/CDN，不发起运行时网络请求。页面把 `source` 事实、待确认的
`reference` 草稿与 `detected` 结果分成三块呈现，从不合并。
"""

from __future__ import annotations

import html
from typing import Any

_PAGE_TITLE = "ATBench 项目参考标注审阅包"
_VERDICT_LABELS = {"unsafe": "unsafe · 有风险", "safe": "safe · 无风险"}
_VERDICT_TONES = {"unsafe": "risk", "safe": "ok"}
_SCOPE_LABELS = {
    "trajectory": "轨迹级 · 未定位",
    "event": "事件级",
    "span": "片段级",
}
_EVENT_TYPE_LABELS = {
    "message": "消息",
    "thought": "显式 thought",
    "tool_call": "工具调用",
    "tool_result": "工具返回",
    "state_change": "状态变化",
    "unsupported_content": "未支持内容",
}
_DECISION_LABELS = {
    None: "未决定",
    "accept": "接受",
    "revise": "修订",
    "reject": "拒绝",
}
_REVIEW_STATUS_LABELS = {
    "pending": "pending · 未开始",
    "in_progress": "in_progress · 审阅中",
    "confirmed": "confirmed · 已完成",
}
_REVIEW_STATUS_TONES = {
    "pending": "warn",
    "in_progress": "warn",
    "confirmed": "ok",
}
_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f5f2;
  --panel: #ffffff;
  --ink: #1a1a18;
  --muted: #6b6b64;
  --line: #e2e1da;
  --line-strong: #c8c7bf;
  --source: #33506b;
  --reference: #8a5300;
  --detected: #8a2c22;
  --ok: #2f6b3f;
  --risk: #b42318;
  --warn: #8a5300;
  --code-bg: #f2f1ec;
  --soft-source: #eef2f6;
  --soft-reference: #fdf6e9;
  --soft-detected: #fbeeec;
  --radius: 10px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC",
    "Microsoft YaHei", sans-serif;
}
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 40px 24px 72px; }
.kicker {
  margin: 0 0 8px;
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
}
h1 { margin: 0 0 10px; font-size: 26px; line-height: 1.25; }
h2 { margin: 34px 0 12px; font-size: 19px; }
h3 { margin: 0 0 8px; font-size: 15px; }
.lede { margin: 0 0 18px; max-width: 76ch; color: var(--muted); font-size: 14px; }
.badge-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 18px; }
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: var(--panel);
  font-size: 12.5px;
  font-weight: 600;
}
.badge--warn { background: var(--soft-reference); color: var(--warn); border-color: #eddcb8; }
.badge--risk { background: var(--soft-detected); color: var(--risk); border-color: #f2cdc7; }
.badge--ok { background: #edf6ef; color: var(--ok); border-color: #c6dccb; }
.badge--neutral { background: var(--code-bg); }
.callout {
  margin: 0 0 22px;
  padding: 14px 18px;
  border-left: 4px solid var(--warn);
  background: var(--soft-reference);
  border-radius: 0 var(--radius) var(--radius) 0;
}
.callout p { margin: 0 0 6px; font-size: 13.5px; }
.callout p:last-child { margin-bottom: 0; }
.layers { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.layer { border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); padding: 14px 16px; }
.layer--source { border-top: 3px solid var(--source); background: var(--soft-source); }
.layer--reference { border-top: 3px solid var(--reference); background: var(--soft-reference); }
.layer--detected { border-top: 3px solid var(--detected); background: var(--soft-detected); }
.layer h3 { font-size: 13px; letter-spacing: .04em; text-transform: uppercase; }
.layer p { margin: 0; font-size: 13px; color: var(--muted); }
table { width: 100%; border-collapse: collapse; background: var(--panel); }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; font-size: 13px; }
th { font-size: 12px; letter-spacing: .03em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
tbody tr:last-child td { border-bottom: none; }
.panel { border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; background: var(--panel); }
.panel + .panel { margin-top: 12px; }
.panel__head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px 14px; border-bottom: 1px solid var(--line); background: #fafaf7;
}
.panel__head strong { font-size: 13px; }
.panel__body { padding: 12px 14px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
details.draft {
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--panel); margin: 0 0 10px; overflow: hidden;
}
details.draft > summary {
  cursor: pointer; padding: 11px 14px; display: flex; gap: 10px;
  align-items: center; flex-wrap: wrap; background: #fbfbf8;
}
details.draft > summary::-webkit-details-marker { display: none; }
details.draft[open] > summary { border-bottom: 1px solid var(--line); }
.draft__id { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.draft__body { padding: 14px; }
.review-state {
  margin-top: 12px; padding: 10px 12px; border-radius: 8px;
  background: var(--code-bg); font-size: 13px;
}
.evidence { margin: 0; padding-left: 18px; }
.evidence li { margin-bottom: 6px; }
.quote { display: block; margin-top: 3px; padding: 6px 8px; background: var(--code-bg); border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
.loc { font-weight: 600; }
.loc--ok { color: var(--ok); }
.loc--bad { color: var(--risk); }
.events td pre { margin: 0; white-space: pre-wrap; word-break: break-word; max-height: 220px; overflow: auto; }
.muted { color: var(--muted); }
.foot { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12.5px; }
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{tone}">{_e(label)}</span>'


def _evidence_state(item: dict[str, Any]) -> str:
    if item.get("located") is True:
        return '<span class="loc loc--ok">已定位</span>'
    return f'<span class="loc loc--bad">定位失败：{_e(item.get("failure"))}</span>'


def _char_range(item: dict[str, Any]) -> str:
    value = item.get("char_range")
    if not isinstance(value, list) or len(value) != 2:
        return "—"
    return f"{value[0]}–{value[1]}"


def _draft_block(
    draft: dict[str, Any],
    transcript: dict[str, Any],
    item: dict[str, Any],
) -> str:
    draft_id = draft.get("draft_id")
    verdict = draft.get("verdict")
    tone = _VERDICT_TONES.get(verdict, "neutral")
    scope = draft.get("scope")
    decision = item.get("decision")
    evidence = draft.get("evidence") or []
    located = sum(1 for entry in evidence if entry.get("located") is True)

    parts: list[str] = []
    parts.append('<details class="draft" id="' + _e(draft_id) + '">')
    parts.append("<summary>")
    parts.append(f'<span class="draft__id">{_e(draft_id)}</span>')
    parts.append(_badge(_VERDICT_LABELS.get(verdict or "", str(verdict)), tone))
    parts.append(_badge(_SCOPE_LABELS.get(scope or "", str(scope)), "neutral"))
    parts.append(
        _badge(
            f"证据 {located}/{len(evidence)} 已定位",
            "ok" if located == len(evidence) else "risk",
        )
    )
    parts.append(_badge(f"审阅：{_DECISION_LABELS.get(decision, '未决定')}", "neutral"))
    parts.append("</summary>")
    parts.append('<div class="draft__body">')

    parts.append('<div class="grid">')

    # 1. 来源事实
    source_label = draft.get("source_label") or {}
    parts.append('<div class="panel"><div class="panel__head">'
                 '<strong>source · ATBench 来源事实</strong>'
                 '<span class="muted">原样引用，不改写</span></div>'
                 '<div class="panel__body"><table><tbody>')
    for key, value in source_label.items():
        parts.append(f"<tr><th>{_e(key)}</th><td><code>{_e(value)}</code></td></tr>")
    parts.append("</tbody></table></div></div>")

    # 2. 参考标注草稿
    parts.append('<div class="panel"><div class="panel__head">'
                 '<strong>reference · 待确认草稿</strong>'
                 f'{_badge("pending", "warn")}</div><div class="panel__body">')
    parts.append("<table><tbody>")
    rows = (
        ("草稿依据", draft.get("basis")),
        ("作用范围", draft.get("scope")),
        ("已定位", draft.get("located")),
        ("起源事件", draft.get("origin_event")),
        ("判定", draft.get("verdict")),
        ("统一类别", ", ".join(draft.get("categories") or []) or "（待人工确认）"),
        ("来源类别", ", ".join(draft.get("source_categories") or []) or "—"),
        ("理由", draft.get("reason")),
        ("理由依据", draft.get("reason_basis")),
        ("类别映射", (draft.get("mapping") or {}).get("status")),
        ("标注版本", draft.get("annotation_version")),
        ("生成时间", draft.get("generated_at")),
    )
    for label, value in rows:
        parts.append(f"<tr><th>{_e(label)}</th><td>{_e(value)}</td></tr>")
    parts.append("</tbody></table>")

    parts.append('<h3 style="margin-top:14px">证据引用</h3>')
    if evidence:
        parts.append('<ul class="evidence">')
        for entry in evidence:
            parts.append(
                "<li>"
                f'<code>{_e(entry.get("kind"))}</code> · '
                f'<code>{_e(entry.get("json_path"))}</code> · '
                f'{_evidence_state(entry)}'
                f'<span class="muted"> · record {_e(entry.get("record_index"))} · '
                f'字符 {_e(_char_range(entry))}</span>'
                f'<span class="quote">{_e(entry.get("quote"))}</span>'
                "</li>"
            )
        parts.append("</ul>")
    else:
        parts.append('<p class="muted">无证据引用。</p>')
    parts.append("</div></div>")
    parts.append("</div>")

    # 3. 轨迹原文
    events = transcript.get("events") or []
    parts.append('<div class="panel"><div class="panel__head">'
                 f'<strong>可定位的轨迹原文</strong>'
                 f'<span class="muted">共 {len(events)} 个事件</span></div>'
                 '<div class="panel__body">')
    if transcript.get("problem"):
        parts.append(
            '<p class="loc loc--bad">该样本无法归一化：'
            f'{_e(transcript["problem"].get("code"))} · '
            f'{_e(transcript["problem"].get("reason"))}</p>'
        )
    if events:
        parts.append('<table class="events"><thead><tr>'
                     "<th>event_id</th><th>类型</th><th>json_path</th><th>原文</th>"
                     "</tr></thead><tbody>")
        for event in events:
            parts.append(
                "<tr>"
                f"<td><code>{_e(event.get('event_id'))}</code></td>"
                f"<td>{_e(_EVENT_TYPE_LABELS.get(event.get('type') or '', event.get('type')))}</td>"
                f"<td><code>{_e(event.get('json_path'))}</code></td>"
                f"<td><pre>{_e(event.get('summary'))}</pre></td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
        parts.append(
            '<p class="muted">事件身份、字段路径与原文可直接用于事件级修订：'
            "在 review.json 中把 scope 改为 event，origin_event 填 event_id，"
            "evidence 追加 kind=event 且 event_id 相同的条目。</p>"
        )
    elif not transcript.get("problem"):
        parts.append('<p class="muted">该样本没有可展示的事件。</p>')
    parts.append("</div></div>")

    # 4. detected（明确不合并）
    parts.append(
        '<div class="panel"><div class="panel__head">'
        "<strong>detected · 审计引擎结果</strong>"
        f'{_badge("未包含", "risk")}</div>'
        '<div class="panel__body"><p class="muted">'
        "本审阅包不包含任何 detected 结果；reference 草稿与 detected 结果"
        "分别保存在不同产物中，从不合并成一条标注。"
        "</p></div></div>"
    )

    parts.append(
        '<div class="review-state">'
        f"<strong>审阅状态：</strong>{_e(_DECISION_LABELS.get(decision, '未决定'))}"
        f" · 备注：{_e(item.get('note') or '—')}"
        + (
            f" · 修订字段：{_e(', '.join((item.get('revised') or {}).keys()))}"
            if item.get("revised")
            else ""
        )
        + "</div>"
    )
    parts.append("</div></details>")
    return "".join(parts)


def render_reference_page(package: dict[str, Any]) -> str:
    manifest = package.get("manifest") or {}
    drafts = (package.get("drafts") or {}).get("drafts") or []
    transcripts = {
        entry.get("draft_id"): entry
        for entry in (package.get("transcripts") or {}).get("transcripts") or []
        if isinstance(entry, dict)
    }
    review = package.get("review") or {}
    review_status = review.get("status") or "pending"
    assistance = review.get("assistance") or {}
    items = {
        item.get("draft_id"): item
        for item in review.get("items") or []
        if isinstance(item, dict)
    }
    inputs = manifest.get("input") or {}
    decided = sum(
        1 for item in items.values() if isinstance(item, dict) and item.get("decision")
    )
    unsafe = sum(1 for draft in drafts if draft.get("verdict") == "unsafe")
    locator_failures = manifest.get("locator_failure_count") or 0

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{_e(_PAGE_TITLE)}</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append("</head><body><div class=\"wrap\">")

    parts.append(
        '<p class="kicker">reference review package · '
        f"{_e(_REVIEW_STATUS_LABELS.get(review_status, review_status))}</p>"
    )
    parts.append(f"<h1>{_e(_PAGE_TITLE)}</h1>")
    parts.append(
        '<p class="lede">'
        f"覆盖 <code>{_e(inputs.get('acceptance_manifest_id'))}</code> 中 "
        f"{len(drafts)} 条 ATBench 验收样本。左侧来源事实原样引用，中间是待确认的"
        "项目参考标注草稿，右侧是该样本的可定位轨迹原文；三者分开呈现，从不合并。"
        "</p>"
    )

    parts.append('<div class="badge-row">')
    parts.append(_badge(f"标注版本 {manifest.get('annotation_version')}", "neutral"))
    parts.append(
        _badge(
            f"状态 {_REVIEW_STATUS_LABELS.get(review_status, review_status)}",
            _REVIEW_STATUS_TONES.get(review_status, "neutral"),
        )
    )
    parts.append(_badge(f"草稿 {len(drafts)} 条", "neutral"))
    parts.append(_badge(f"unsafe {unsafe} / safe {len(drafts) - unsafe}", "neutral"))
    parts.append(
        _badge(
            f"定位失败 {locator_failures}",
            "ok" if locator_failures == 0 else "risk",
        )
    )
    parts.append(_badge(f"已审阅 {decided}/{len(drafts)}", "neutral"))
    parts.append("</div>")

    parts.append('<div class="callout">')
    for line in manifest.get("disclaimers") or []:
        parts.append(f"<p>{_e(line)}</p>")
    parts.append("</div>")

    # 三层
    parts.append("<h2>三层分离</h2>")
    parts.append('<div class="layers">')
    for layer in manifest.get("layers") or []:
        name = layer.get("layer")
        included = "已包含" if layer.get("included") else "未包含"
        parts.append(
            f'<div class="layer layer--{_e(name)}">'
            f"<h3>{_e(name)} · {_e(layer.get('owner'))}</h3>"
            f"<p><strong>{_e(included)}</strong> · {_e(layer.get('note'))}</p>"
            "</div>"
        )
    parts.append("</div>")

    # 输入与生成器
    parts.append("<h2>版本与生成器</h2>")
    parts.append('<div class="panel"><div class="panel__body"><table><tbody>')
    generator = manifest.get("generator") or {}
    meta = (
        ("包身份", manifest.get("package_id")),
        ("标注版本", manifest.get("annotation_version")),
        ("生成时间", manifest.get("generated_at")),
        ("工具版本", manifest.get("tool_version")),
        ("数据集", inputs.get("dataset_id")),
        ("数据集版本", inputs.get("dataset_version")),
        ("数据集 SHA-256", inputs.get("dataset_sha256")),
        ("数据集许可", inputs.get("dataset_license")),
        ("验收清单", inputs.get("acceptance_manifest_id")),
        ("验收清单内容哈希", inputs.get("acceptance_content_sha256")),
        ("生成器", f"{generator.get('kind')} · {generator.get('id')} v{generator.get('version')}"),
        ("辅助模型", generator.get("model") or "null（未使用）"),
        ("提示模板版本", generator.get("prompt_template") or "null（未使用）"),
        ("生成器说明", generator.get("note")),
        ("作用范围策略", manifest.get("scope_policy")),
    )
    for label, value in meta:
        parts.append(f"<tr><th>{_e(label)}</th><td><code>{_e(value)}</code></td></tr>")
    parts.append("</tbody></table></div></div>")

    parts.append("<h2>人工审阅与辅助血缘</h2>")
    parts.append('<div class="panel"><div class="panel__body"><table><tbody>')
    review_meta = (
        ("整版状态", review_status),
        ("人工审阅人", review.get("reviewer") or "待填写"),
        ("确认时间", review.get("reviewed_at") or "待填写"),
        ("辅助模式", assistance.get("mode")),
        ("辅助产品", assistance.get("product") or "null"),
        ("辅助模型", assistance.get("model") or "null"),
        ("审阅协议", assistance.get("prompt_protocol") or "null"),
        ("最终裁决者", assistance.get("final_authority")),
        ("辅助说明", assistance.get("note")),
        (
            "项目参考类别词表",
            (manifest.get("category_taxonomy") or {}).get("id"),
        ),
    )
    for label, value in review_meta:
        parts.append(f"<tr><th>{_e(label)}</th><td><code>{_e(value)}</code></td></tr>")
    parts.append("</tbody></table></div></div>")

    # 审阅工作流
    parts.append("<h2>审阅工作流</h2>")
    parts.append('<div class="panel"><div class="panel__body">')
    parts.append("<ol>")
    parts.append(
        "<li>在 <code>review.json</code> 中为每条草稿填写 <code>decision</code>："
        "<code>accept</code>、<code>revise</code> 或 <code>reject</code>，并写明 "
        "<code>note</code>。</li>"
    )
    parts.append(
        "<li>accept 需要把 <code>scope</code>、<code>origin_event</code>、"
        "<code>evidence</code>、<code>categories</code>、<code>reason</code> 五个字段"
        "逐一签字；revise 需要把要改的字段在 <code>revised</code> 中写明。</li>"
    )
    parts.append(
        "<li>运行 <code>python -m trajectory_audit reference validate --package "
        "&lt;目录&gt; --dataset-root .cache/datasets</code> 复核草稿未被改写、"
        "证据仍可回读。</li>"
    )
    parts.append(
        "<li>每轮决定后运行 <code>python -m trajectory_audit reference refresh "
        "--package &lt;目录&gt;</code>，安全同步整版状态、页面与 README；该命令不重写草稿。"
        "</li>"
    )
    parts.append(
        "<li>运行 <code>python -m trajectory_audit reference export --package "
        "&lt;目录&gt; --out effective-labels.json</code>；未确认前 "
        "<code>effective_count</code> 必须为 0。</li>"
    )
    parts.append("</ol>")
    parts.append("</div></div>")

    # 草稿列表
    parts.append(f"<h2>参考标注草稿（{len(drafts)} 条）</h2>")
    for draft in drafts:
        draft_id = draft.get("draft_id")
        parts.append(
            _draft_block(
                draft,
                transcripts.get(draft_id) or {},
                items.get(draft_id) or {},
            )
        )

    parts.append('<p class="foot">')
    parts.append(
        "本页面由 <code>python -m trajectory_audit reference build</code> 生成的静态文件，"
        "不引用外部资源，不发起网络请求。项目参考标注不是来源数据集真值，"
        "也不是双人金标或线上人工复核流程。"
    )
    parts.append("</p>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def render_reference_readme(package: dict[str, Any]) -> str:
    manifest = package.get("manifest") or {}
    drafts = (package.get("drafts") or {}).get("drafts") or []
    review = package.get("review") or {}
    assistance = review.get("assistance") or {}
    items = {
        item.get("draft_id"): item
        for item in review.get("items") or []
        if isinstance(item, dict)
    }
    inputs = manifest.get("input") or {}
    generator = manifest.get("generator") or {}
    lines: list[str] = []
    add = lines.append

    add(f"# ATBench 项目参考标注审阅包 · `{manifest.get('annotation_version')}`")
    add("")
    add(
        "本目录是人工确认用的审阅包，覆盖 "
        f"`{inputs.get('acceptance_manifest_id')}` 中的 **{len(drafts)} 条 ATBench 验收样本**。"
    )
    add("")
    add("## 这是什么，不是什么")
    add("")
    for line in manifest.get("disclaimers") or []:
        add(f"- {line}")
    add("")
    add(
        "草稿由 ATBench 来源标签确定性投影得到，生成器为 "
        f"`{generator.get('kind')}` / `{generator.get('id')}` / v{generator.get('version')}；"
        "草稿生成未使用辅助模型。人工审阅的辅助血缘另行记录在 `review.json`，"
        "不得与草稿生成器身份混合。"
    )
    add("")

    add("## 文件")
    add("")
    add("| 文件 | Schema | 作用 |")
    add("| --- | --- | --- |")
    add("| `manifest.json` | `reference-package/1` | 输入版本、生成器身份、三层声明、免责声明 |")
    add("| `drafts.json` | `reference-drafts/1` | 待确认的参考标注草稿与证据引用 |")
    add("| `transcripts.json` | `reference-transcripts/1` | 每个样本的事件身份、字段路径与原文 |")
    add("| `review.json` | `reference-review/1` | 人工逐字段确认表（唯一生效入口） |")
    add("| `index.html` | — | 离线审阅页面，无外部资源 |")
    add("| `README.md` | — | 本说明 |")
    add("")

    add("## 输入与版本")
    add("")
    add("| 项 | 值 |")
    add("| --- | --- |")
    add(f"| 数据集 | `{inputs.get('dataset_id')}` |")
    add(f"| 数据集版本 | `{inputs.get('dataset_version')}` |")
    add(f"| 数据集 SHA-256 | `{inputs.get('dataset_sha256')}` |")
    add(f"| 数据集许可 | {inputs.get('dataset_license')} |")
    add(f"| 验收清单 | `{inputs.get('acceptance_manifest_id')}` |")
    add(f"| 验收清单内容哈希 | `{inputs.get('acceptance_content_sha256')}` |")
    add(f"| 草稿数 | {manifest.get('draft_count')} |")
    add(f"| 定位失败数 | {manifest.get('locator_failure_count')} |")
    add(f"| 轨迹事件数 | {manifest.get('transcript_event_count')} |")
    add(f"| 生成时间 | `{manifest.get('generated_at')}` |")
    add(f"| 工具版本 | `{manifest.get('tool_version')}` |")
    add(f"| 审阅状态 | `{review.get('status')}` |")
    add(f"| 人工审阅人 | `{review.get('reviewer') or '待填写'}` |")
    add(f"| 确认时间 | `{review.get('reviewed_at') or '待填写'}` |")
    add(f"| 辅助模式 | `{assistance.get('mode')}` |")
    add(f"| 辅助产品 | `{assistance.get('product') or 'null'}` |")
    add(f"| 辅助模型 | `{assistance.get('model') or 'null'}` |")
    add(f"| 审阅协议 | `{assistance.get('prompt_protocol') or 'null'}` |")
    add(
        f"| 项目参考类别词表 | `{(manifest.get('category_taxonomy') or {}).get('id')}` |"
    )
    add("")

    add("## 草稿一览")
    add("")
    add("| 草稿 | 样本 | 来源标签 | 判定 | 证据 | 审阅 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for draft in drafts:
        source = draft.get("source_label") or {}
        label = f"label={source.get('label')} · {source.get('risk_source')} / {source.get('failure_mode')}"
        evidence = draft.get("evidence") or []
        located = sum(1 for entry in evidence if entry.get("located") is True)
        item = items.get(draft.get("draft_id")) or {}
        add(
            f"| `{draft.get('draft_id')}` | `{draft.get('sample_id')}` | "
            f"{label} | {draft.get('verdict')} | {located}/{len(evidence)} 已定位 | "
            f"{_DECISION_LABELS.get(item.get('decision'), '未决定')} |"
        )
    add("")

    add("## 审阅流程")
    add("")
    add("1. 打开 `index.html`，逐条核对「来源事实 / 参考标注草稿 / 轨迹原文」。")
    add(
        "2. 在 `review.json` 中为每条草稿填写 `decision`（`accept` / `revise` / "
        "`reject`）与 `note`。"
    )
    add(
        "3. `accept` 必须把 `scope`、`origin_event`、`evidence`、`categories`、"
        "`reason` 逐字段签字；`revise` 必须把改动的字段写进 `revised`。"
    )
    add("4. 每轮决定后安全刷新页面，再复核并导出：")
    add("")
    add("```bash")
    add("python -m trajectory_audit reference refresh \\")
    add("  --package acceptance/atbench_reference_v1")
    add("python -m trajectory_audit reference validate \\")
    add("  --package acceptance/atbench_reference_v1 \\")
    add("  --dataset-root .cache/datasets")
    add("python -m trajectory_audit reference export \\")
    add("  --package acceptance/atbench_reference_v1 \\")
    add("  --out acceptance/atbench_reference_v1/effective-labels.json")
    add("```")
    add("")
    add(
        "未确认前 `effective_count` 必须是 `0`：`pending` 与 `reject` 的草稿"
        "不会出现在 `labels` 中，因此 H2 指标与正式审阅页面读不到任何生效参考标注。"
    )
    add("")

    add("## 字段语义")
    add("")
    add("| 字段 | 含义 |")
    add("| --- | --- |")
    add("| `layer` | 固定为 `reference`；`source` 与 `detected` 永不同层合并 |")
    add("| `scope` | `trajectory` / `event` / `span`；ATBench 只支持 `trajectory` |")
    add("| `located` | 是否有可定位证据；轨迹级草稿恒为 `false` |")
    add("| `origin_event` | 事件级草稿的起源事件身份，必须出现在 `transcripts.json` |")
    add("| `evidence[].kind` | `source_field` / `event` / `tool_fact` |")
    add("| `evidence[].json_path` | 回到原始记录的字段路径 |")
    add("| `evidence[].quote` / `char_range` | 原文引用与可选字符区间 |")
    add("| `evidence[].located` / `failure` | 是否可回读；失败时记录原因 |")
    add("| `review.items[].draft_sha256` | 原始草稿哈希，防止确认后改写草稿 |")
    add("| `review.items[].field_decisions` | 五个字段的逐项签字 |")
    add("| `review.items[].revised` | 仅 `revise` 使用，且只允许五个可修订字段 |")
    add("| `review.status` | `pending` / `in_progress` / `confirmed`；部分审阅不得确认整版 |")
    add("| `review.assistance` | AI 辅助产品、模型、审阅协议与人工最终裁决血缘 |")
    add("| `categories` | `safe` 必须为空；`unsafe` 必须来自版本化项目参考类别词表 |")
    add("")

    add("## 复现")
    add("")
    add("```bash")
    add("python -m trajectory_audit reference build \\")
    add("  --manifest acceptance/acceptance-v1.json \\")
    add("  --dataset-root .cache/datasets \\")
    add("  --out acceptance/atbench_reference_v1")
    add("```")
    add("")
    add(
        "重建会拒绝覆盖已有确认记录的审阅包；需要重开一版时请换 "
        "`--annotation-version` 或显式传入 `--force`。"
    )
    add("")
    return "\n".join(lines)


__all__ = ["render_reference_page", "render_reference_readme"]
