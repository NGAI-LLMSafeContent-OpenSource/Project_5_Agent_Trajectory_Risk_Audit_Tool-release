# SPDX-License-Identifier: Apache-2.0
"""面向操作者的结构化错误。"""

from __future__ import annotations

from typing import Any


# 转换阶段，用于批量运行中定位失败发生在哪一层。
STAGE_ADAPTER_VALIDATION = "adapter_validation"
STAGE_ADAPTER_NORMALIZATION = "adapter_normalization"
STAGE_SOURCE_INTEGRITY = "source_integrity"
STAGE_SOURCE_LOCATOR = "source_locator"
STAGE_SAMPLE_IDENTITY = "sample_identity"
STAGE_ARTIFACT_WRITE = "artifact_write"

# 逐记录结果分类。``source_invalid`` 表示来源数据自身的引用不成立：记录被
# 如实排除并记录原因，但不是 adapter 的转换缺陷。
OUTCOME_VALID = "valid"
OUTCOME_SOURCE_INVALID = "source_invalid"
OUTCOME_SCHEMA_INVALID = "schema_invalid"
OUTCOME_CONVERSION_ERROR = "conversion_error"


class RunError(Exception):
    """带机器可读代码的运行错误。

    退出码约定：
    1 内部错误；2 参数或输入校验错误；3 已有不可变产物（run 包、批次或冻结
    清单）与本次运行冲突；4 批次或 H1 验证完成但存在逐项失败。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "error",
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConversionError(Exception):
    """单条来源记录转换失败。

    与 ``RunError`` 的区别：批量运行不会因一条记录失败而中断其他记录，因此
    该错误携带样本身份、阶段和原因，最终写入 ``failures.json``。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        details: dict[str, Any] | None = None,
        sample_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.details = details or {}
        self.sample_id = sample_id

    def to_failure(
        self,
        *,
        sample_id: str,
        record_index: int,
        sample_id_source: str,
    ) -> dict[str, Any]:
        return {
            "sample_id": sample_id,
            "sample_id_source": sample_id_source,
            "record_index": record_index,
            "stage": self.stage,
            "code": self.code,
            "reason": self.message,
            "details": self.details,
        }
