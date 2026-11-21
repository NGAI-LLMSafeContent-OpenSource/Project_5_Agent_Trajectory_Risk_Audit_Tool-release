# SPDX-License-Identifier: Apache-2.0
"""Replayable five-case Development Corpus; no annotation or Provider execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import openai_messages
from .audit_input import (
    AUDIT_INPUT_SCHEMA, AUDIT_TRAJECTORY_SCHEMA, build_batch, canonical_item,
    validate_audit_input,
)
from .corpus_common import (
    artifact_digests, content_sha256, existing_matches, normalize_record,
    provider_sample_id, write_payloads,
)
from .errors import RunError
from .model import NormalizedTrajectory, sha256_json

VERSION = 'topic4-provider-corpus-v3'
MANIFEST_SCHEMA = 'development-only-corpus/1'
DATASET = 'pujiang-public-second-task'
SOURCE_SHA256 = '25d4c1fb7aacb6ee076dfdc076ef4304a0152fb5064cd6175a4c5eb1c4460a7c'
DEFAULT_SOURCE = Path('acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl')
EXPECTED_IDS = ['s01', 'p14', 's04', 'science_agent_bench_66', 'science_agent_bench_48']


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read every JSONL record, retaining physical line numbers and failing closed."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RunError('invalid_source', 'Cannot read UTF-8 JSONL source') from exc
    return parse_jsonl(raw)


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f'Invalid JSON constant: {value}')


def parse_jsonl(raw: bytes) -> list[dict[str, Any]]:
    """Validate a complete uploaded JSONL file before any subset is selected."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        text = raw.decode('utf-8')
        lines = text.split('\n')
        if lines[-1] == '':
            lines.pop()
    except UnicodeDecodeError as exc:
        raise RunError('invalid_source', 'Cannot read UTF-8 JSONL source') from exc
    for line_number, line in enumerate(lines, 1):
        try:
            record = json.loads(line, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError) as exc:
            raise RunError('invalid_source_line', f'Invalid JSON at line {line_number}') from exc
        identity = record.get('trajectory_id') if isinstance(record, dict) else None
        if not isinstance(identity, str) or not identity.strip():
            raise RunError('invalid_trajectory_id', f'Missing trajectory_id at line {line_number}')
        if identity.strip() in seen:
            raise RunError('duplicate_trajectory_id', f'Duplicate trajectory_id at line {line_number}')
        issues = openai_messages.validate_record(record)
        if issues:
            raise RunError('invalid_source_record', f'Invalid messages at line {line_number}',
                           details={'issues': issues})
        seen.add(identity.strip())
        records.append({'line_number': line_number, 'record': record})
    if not records:
        raise RunError('empty_source', 'JSONL batch must not be empty')
    return records


def source_mapping(trajectory: NormalizedTrajectory, *, line_number: int) -> dict[str, Any]:
    """Map events to original fields and generated race steps without source_step_id."""
    record = trajectory.record_raw
    steps: list[dict[str, Any]] = []
    by_message: dict[int, list[int]] = {}
    by_call: dict[tuple[int, int], int] = {}
    for index, message in enumerate(record['messages']):
        if message['role'] != 'assistant':
            continue
        calls = message.get('tool_calls') or []
        reasoning = message.get('reasoning_content')
        content = message.get('content')
        has_body = (isinstance(reasoning, str) and bool(reasoning)) or (
            isinstance(content, str) and bool(content)
        ) or (isinstance(content, list) and bool(content))
        count = len(calls) if calls else int(has_body)
        for call_index in range(count):
            step_id = len(steps) + 1
            by_message.setdefault(index, []).append(step_id)
            if calls:
                by_call[index, call_index] = step_id
            steps.append({'step_id': step_id, 'message_index': index,
                          'tool_call_index': call_index if calls else None,
                          'event_ids': []})

    call_steps: dict[str, list[int]] = {}
    event_rows: list[dict[str, Any]] = []
    for event in trajectory.events:
        index = event.content['message_index']
        step_ids = by_message.get(index, [])
        if event.type == 'tool_call':
            match = re.search(r'\.tool_calls\[(\d+)\]$', event.source.json_path)
            assert match is not None
            step_ids = [by_call[index, int(match[1])]]
            call_steps[event.event_id] = step_ids
        elif event.type == 'tool_result':
            call_event_id = event.content.get('call_event_id')
            step_ids = call_steps.get(call_event_id, []) if isinstance(call_event_id, str) else []
        row = {'event_id': event.event_id, 'type': event.type, 'message_index': index,
               'source_field': event.source.json_path, 'line_number': line_number,
               'step_ids': step_ids}
        if event.type in {'message', 'tool_result'}:
            row['source_field'] += '.content'
        if event.type == 'tool_result':
            row.update({key: event.content[key] for key in (
                'call_event_id', 'link_status', 'candidate_call_event_ids', 'unresolved_reason',
            )})
        event_rows.append(row)
        for step_id in step_ids:
            steps[step_id - 1]['event_ids'].append(event.event_id)
    return {
        'trajectory_id': record['trajectory_id'],
        'sample_id': 'pujiang-' + sha256_json(record['trajectory_id'])[:16],
        'provider_sample_id': trajectory.sample_id,
        'record_index': trajectory.record_index, 'line_number': line_number,
        'record_sha256': trajectory.record_sha256, 'raw_record': record,
        'steps': steps, 'events': event_rows,
        'tool_call_count': sum(e.type == 'tool_call' for e in trajectory.events),
    }


def delivery_payloads(source: Path) -> dict[str, Any]:
    """Construct all frozen bytes from the pinned source and versioned adapter."""
    try:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise RunError('invalid_source', 'Cannot read source file') from exc
    if digest != SOURCE_SHA256:
        raise RunError('source_digest_mismatch', 'Source does not match the frozen five cases')
    rows = load_jsonl(source)
    if [row['record']['trajectory_id'] for row in rows] != EXPECTED_IDS:
        raise RunError('source_identity_mismatch', 'Expected the ordered five public cases')
    items: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    states: Counter[str] = Counter()
    for index, row in enumerate(rows):
        record = row['record']
        record_digest = sha256_json(record)
        trajectory = normalize_record(
            openai_messages, record, record_index=index, dataset_id=DATASET,
            dataset_version=SOURCE_SHA256, file_sha256=digest,
            record_sha256=record_digest,
            sample_id=provider_sample_id(DATASET, index, record_digest),
        )
        items.append(canonical_item(trajectory))
        mappings.append(source_mapping(trajectory, line_number=row['line_number']))
        states.update(e.content['link_status'] for e in trajectory.events if e.type == 'tool_result')
    if [len(r['steps']) for r in mappings] != [7, 43, 22, 6, 24] or [
        r['tool_call_count'] for r in mappings
    ] != [6, 42, 21, 5, 24]:
        raise RunError('mapping_count_mismatch', 'Generated race steps/calls differ from #57')
    batch = build_batch(items)
    issues = validate_audit_input(batch)
    if issues:
        raise RunError('invalid_audit_input', 'Projection failed the existing input contract',
                       details={'issues': issues})
    artifacts: dict[str, Any] = {
        'input-batch.json': batch,
        'internal-mapping.json': {'schema_version': 'pujiang-source-mapping/1',
                                  'records': mappings},
        'README.md': _readme(dict(states)),
    }
    manifest: dict[str, Any] = {
        'schema_version': MANIFEST_SCHEMA, 'corpus_version': VERSION,
        'purpose': 'public-development-only', 'blind_acceptance': None,
        'historical_relationship': 'Independent five-case delivery; v1/v2 unchanged',
        'source': {'archive': 'saima-risk-visualization-sample-v1',
                   'filename': 'samples.jsonl', 'sha256': digest},
        'adapter': {'id': openai_messages.ADAPTER_ID, 'version': openai_messages.ADAPTER_VERSION},
        'input_schema': AUDIT_INPUT_SCHEMA, 'trajectory_schema': AUDIT_TRAJECTORY_SCHEMA,
        'batch_id': batch['batch_id'], 'record_count': len(rows), 'tool_call_count': 98,
        'step_counts': [7, 43, 22, 6, 24], 'link_status_counts': dict(states),
        'artifacts': artifact_digests(artifacts),
        'reproducibility': 'No wall-clock timestamp; UTF-8 LF deterministic JSON bytes',
    }
    manifest['integrity'] = {'content_sha256': content_sha256(manifest)}
    artifacts['manifest.json'] = manifest
    return artifacts


def build_delivery(source: Path, out: Path) -> dict[str, Any]:
    payloads = delivery_payloads(source)
    if out.exists() and any(out.iterdir()):
        if not existing_matches(out, payloads) or {p.name for p in out.iterdir()} != set(payloads):
            raise RunError('frozen_delivery_conflict', 'Existing frozen directory differs; use a new version',
                           exit_code=3)
    else:
        write_payloads(out, payloads, manifest_name='manifest.json')
    return {'status': 'complete', 'corpus_version': VERSION,
            'batch_id': payloads['input-batch.json']['batch_id'],
            'content_sha256': payloads['manifest.json']['integrity']['content_sha256']}


def verify_delivery(source: Path, root: Path) -> dict[str, Any]:
    payloads = delivery_payloads(source)
    if not root.is_dir() or not existing_matches(root, payloads) or {
        p.name for p in root.iterdir()
    } != set(payloads):
        raise RunError('delivery_mismatch', 'Frozen artifacts differ from source replay')
    return {'status': 'valid', 'corpus_version': VERSION,
            'content_sha256': payloads['manifest.json']['integrity']['content_sha256']}


def _readme(states: dict[str, int]) -> str:
    return f'''# {VERSION}

公开 Development Corpus：s01、p14、s04、science_agent_bench_66、science_agent_bench_48。
独立冻结五例（98 个工具调用，赛题步骤 7/43/22/6/24）；不包含 Blind Acceptance Batch。
不合并或覆盖 v1/v2 的 80 条开发集和 48 条盲测集，不声明通过双子集 edition 校验。
manifest 使用独立 development-only-corpus/1，以下 verify 入口按来源完整重放校验。

## 构建、验证与加载

在仓库根目录运行（Python 3.11+，也可用 uv run --frozen python）：

```bash
python -m trajectory_audit pujiang build --source acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl --out corpus/topic4-provider-corpus-v3
python -m trajectory_audit pujiang verify --source acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl --root corpus/topic4-provider-corpus-v3
topic4-audit run --input corpus/topic4-provider-corpus-v3/input-batch.json --output result.json --config config.json
```

最后一条要求课题四提供自己的 CLI/config；构建与验证无需模型或 GPU。
Provider **只读取 input-batch.json**，不能将整个目录作为模型上下文。
internal-mapping.json 是课题五内部来源核对产物，含完整原始记录、来源身份、行号、
内部 sample_id 与 Provider 身份对应、事件原字段及赛题步骤。组织方参考答案仅供独立运行后核对，
不是 Project Reference Annotation，构建不读取参考答案。
manifest 的逐文件 SHA-256 与整版内容摘要可重算；没有生成时间，全部 UTF-8 LF 字节可重现。
冻结目录内容冲突会拒绝覆盖；verify 对固定来源重建全部产物，也可检出清单、映射或说明篡改。

## 课题四迁移与验证事项

1. corpus 升为 v3，输入仍为 topic4-audit-input/1，轨迹仍为 audit-trajectory/1。
   复用既有外层、provider_sample_id 不透明身份公式、摘要与文件式 CLI，无赛题专用输入。
   来源元数据变化可能改变基于完整记录身份派生的 sample_id，但不改变事件投影与 audit_input_sha256。
2. message/thought/tool_call/tool_result 字段不变。工具定义通过既有白名单投影，实际可能非空；
   必须消费其语义，缺失则为 []。工具名称大小写、正文、Unicode、空格和解码后换行保留。
3. 本版实际 tool_result 状态计数：{json.dumps(states, ensure_ascii=False, sort_keys=True)}。
   历史 positional 不是唯一形式；本版可按回复内 ID linked。一个调用可以有多个返回；
   不得假定每条返回均可关联。匹配只在当前 assistant 回复范围内，user/system/developer 消息关闭范围。
   同范围重复 ID 保持 unknown_call_id，call_event_id=null；无 ID 为 no_call_id，孤立/过期 ID 为 unknown_call_id。
   候选及歧义原因只保存在内部映射，Provider 不能从 unknown_call_id 区分歧义与孤立返回；
   两者都必须视为未解析，不能猜配。未返回的调用照常保留。不新增字段或枚举。
4. 约束正文、显式 reasoning_content、arguments_raw、解码 arguments 和完整 output 使用既有字段。
   参数解析失败保持原串，arguments 不以空对象代替；missing/null/空值在内部 raw_record 可区别。
   不从普通正文推断 thought；共享思考仅一个事件，内部可映射多个步骤。
   未支持片段保留内部原文并以 unsupported_content/coverage 声明，不能声称可见上下文完整。
5. source_step_id 始终 null，coverage.source_step_ids=[]，不是生成的赛题步骤。
   课题四引用已有 event_id 及合法证据位置；课题五通过内部映射还原原文和从 1 开始的 step_id，
   message_index 从 0 开始。每调用一步；无调用但有思考/回复的 assistant 单独一步；空占位不计步。
6. policy 显式 null 仅表示无额外 policy 文档，不表示全部行为获授权。
   system/developer/user 约束仍在事件中，Provider 需要读取这些事件。
7. usage、会话完成元数据、来源路径、标签、参考答案、人工参考标注、既有检测结论均不进入推理输入。
   来源定位与完整原始记录只在课题五内部，不能传入模型。
8. #56 的 race-audit/1 属于另一个输出扩展，不在此票新增输入字段。
   本版完成只证明输入适配与交付，不代表课题四已实现该扩展、算法验收通过或页面完成。
'''
