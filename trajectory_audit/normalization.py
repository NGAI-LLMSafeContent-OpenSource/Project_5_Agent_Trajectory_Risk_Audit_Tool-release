# SPDX-License-Identifier: Apache-2.0
"""统一轨迹构建器：稳定事件身份、来源步骤与来源标签解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import STAGE_SOURCE_LOCATOR, ConversionError
from .model import (
    REFERENCE_STATUS_ABSENT,
    SOURCE_LABEL_SCOPE_STEP,
    SOURCE_LABEL_SCOPE_TRAJECTORY,
    SOURCE_LABEL_SCOPES,
    Event,
    NormalizedTrajectory,
    SourceLabel,
    SourceLocator,
    SourceStep,
    )


@dataclass(frozen=True)
class _PendingLabel:
    scope: str
    label_id: str
    source_step_id: int | str | None
    located: bool
    verdict: str | None
    categories: list[str]
    source_categories: list[str]
    reason: str | None
    mapping: dict[str, Any]
    raw: Any


class TrajectoryBuilder:
    """按归一顺序构建一条轨迹。

    一个来源步骤可以产生多个事件；事件的 ``source_step_id`` 只在来源确实
    提供步骤身份时填写。来源标签解析到步骤后，再按相同 ``source_step_id``
    关联事件。
    """

    def __init__(
        self,
        *,
        sample_id: str,
        source_format: str,
        adapter_id: str,
        adapter_version: str,
        input_sha256: str,
        dataset_id: str,
        dataset_version: str,
        record_index: int,
        record_json_path: str,
        record_sha256: str,
        record_raw: Any,
        unmapped_top_level_fields: list[str] | None = None,
        tool_definitions: Any = None,
    ) -> None:
        self.sample_id = sample_id
        self.source_format = source_format
        self.adapter_id = adapter_id
        self.adapter_version = adapter_version
        self.input_sha256 = input_sha256
        self.dataset_id = dataset_id
        self.dataset_version = dataset_version
        self.record_index = record_index
        self.record_json_path = record_json_path
        self.record_sha256 = record_sha256
        self.record_raw = record_raw
        self.unmapped_top_level_fields = sorted(unmapped_top_level_fields or [])
        self.tool_definitions = tool_definitions
        self.events: list[Event] = []
        self.source_steps: list[SourceStep] = []
        self.source_labels: list[SourceLabel] = []
        self._step_ids: set[Any] = set()
        self._pending_labels: list[_PendingLabel] = []

    def emit(
        self,
        event_type: str,
        actor: str,
        content: dict[str, Any],
        *,
        json_path: str,
        raw: Any,
        source_step_id: int | str | None = None,
    ) -> Event:
        event = Event(
            event_id=f"evt-{len(self.events) + 1:04d}",
            sequence=len(self.events) + 1,
            type=event_type,
            actor=actor,
            source_step_id=source_step_id,
            content=content,
            source=SourceLocator(
                record_index=self.record_index,
                json_path=json_path,
                record_sha256=self.record_sha256,
                dataset_id=self.dataset_id,
                dataset_version=self.dataset_version,
            ),
            raw=raw,
        )
        self.events.append(event)
        return event

    def add_step(
        self,
        *,
        source_step_id: int | str | None,
        source_index: int,
        json_path: str,
        role: str,
        raw: Any,
    ) -> None:
        if source_step_id is not None:
            if source_step_id in self._step_ids:
                raise ConversionError(
                    "duplicate_source_step_id",
                    f"来源步骤身份重复：{source_step_id!r}",
                    stage=STAGE_SOURCE_LOCATOR,
                    details={
                        "source_step_id": source_step_id,
                        "sample_id": self.sample_id,
                    },
                    sample_id=self.sample_id,
                )
            self._step_ids.add(source_step_id)
        self.source_steps.append(
            SourceStep(
                source_step_id=source_step_id,
                source_index=source_index,
                json_path=json_path,
                role=role,
                raw=raw,
            )
        )

    def add_source_label(
        self,
        *,
        scope: str,
        source_step_id: int | str | None,
        verdict: str | None,
        categories: list[str],
        source_categories: list[str],
        reason: str | None,
        mapping: dict[str, Any],
        raw: Any,
    ) -> None:
        if scope not in SOURCE_LABEL_SCOPES:
            raise ValueError(f"未知来源标签作用范围：{scope}")
        located = False
        if scope == SOURCE_LABEL_SCOPE_STEP:
            if source_step_id is None or source_step_id not in self._step_ids:
                raise ConversionError(
                    "source_locator_failed",
                    "来源步骤标签指向了轨迹中不存在的步骤，拒绝伪造定位",
                    stage=STAGE_SOURCE_LOCATOR,
                    details={
                        "sample_id": self.sample_id,
                        "source_step_id": source_step_id,
                        "known_source_step_ids": sorted(
                            (step.source_step_id for step in self.source_steps if step.source_step_id is not None),
                            key=lambda value: (str(type(value)), str(value)),
                        ),
                    },
                    sample_id=self.sample_id,
                )
            located = True
        elif source_step_id is not None:
            raise ConversionError(
                "source_locator_failed",
                "轨迹级来源标签不应携带步骤身份",
                stage=STAGE_SOURCE_LOCATOR,
                details={
                    "sample_id": self.sample_id,
                    "source_step_id": source_step_id,
                },
                sample_id=self.sample_id,
            )
        self._pending_labels.append(
            _PendingLabel(
                scope=scope,
                label_id=f"src-label-{len(self._pending_labels) + 1:04d}",
                source_step_id=source_step_id,
                located=located,
                verdict=verdict,
                categories=list(categories),
                source_categories=list(source_categories),
                reason=reason,
                mapping=dict(mapping),
                raw=raw,
            )
        )

    def finalize(self) -> NormalizedTrajectory:
        event_ids_by_step: dict[Any, list[str]] = {}
        for event in self.events:
            if event.source_step_id is None:
                continue
            event_ids_by_step.setdefault(event.source_step_id, []).append(event.event_id)

        for pending in self._pending_labels:
            event_ids = (
                event_ids_by_step.get(pending.source_step_id, []) if pending.located else []
            )
            self.source_labels.append(
                SourceLabel(
                    label_id=pending.label_id,
                    scope=pending.scope,
                    source_step_id=pending.source_step_id,
                    event_ids=event_ids,
                    located=pending.located,
                    verdict=pending.verdict,
                    categories=pending.categories,
                    source_categories=pending.source_categories,
                    reason=pending.reason,
                    mapping=pending.mapping,
                    raw=pending.raw,
                )
            )

        return NormalizedTrajectory(
            sample_id=self.sample_id,
            source_format=self.source_format,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            input_sha256=self.input_sha256,
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            record_index=self.record_index,
            record_json_path=self.record_json_path,
            record_sha256=self.record_sha256,
            record_raw=self.record_raw,
            events=self.events,
            source_steps=self.source_steps,
            source_labels=self.source_labels,
            tool_definitions=self.tool_definitions,
            unmapped_top_level_fields=self.unmapped_top_level_fields,
            reference={
                "status": REFERENCE_STATUS_ABSENT,
                "labels": [],
                "note": "本项目尚未为该样本建立人工确认的 reference 标注。",
            },
        )


__all__ = [
    "SOURCE_LABEL_SCOPE_STEP",
    "SOURCE_LABEL_SCOPE_TRAJECTORY",
    "TrajectoryBuilder",
]
