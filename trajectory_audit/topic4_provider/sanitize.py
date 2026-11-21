# SPDX-License-Identifier: Apache-2.0
"""配置消毒：归档与日志中永不保留凭据值。

识别常见密钥名（api_key、token、password、secret 等），把对应值替换为
``[REDACTED]``，再对消毒后的规范 JSON 计算 ``config_sha256``。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY_RE = re.compile(
    r"(^|[_-])(api[_-]?key|access[_-]?key|secret|token|password|passwd|credential|auth)([_-]|$)",
    re.IGNORECASE,
)


def is_secret_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    return bool(_SECRET_KEY_RE.search(key))


def sanitize_value(value: Any) -> Any:
    """递归消毒映射中的敏感值；列表与标量按结构保留。"""

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if is_secret_key(str(key)):
                out[key] = REDACTED
            else:
                out[key] = sanitize_value(item)
        return out
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    return value


def sanitize_config(config: Any) -> Any:
    """返回配置的深拷贝消毒结果，不修改调用方对象。"""

    return sanitize_value(copy.deepcopy(config))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def config_sha256(config: Any) -> str:
    """对消毒后的配置计算稳定 SHA-256。"""

    sanitized = sanitize_config(config)
    return hashlib.sha256(canonical_json(sanitized).encode("utf-8")).hexdigest()


def diagnostic_secrets(config: Any, config_path=None, env=None) -> set[str]:
    """Collect explicit service credentials, including referenced model config."""
    environment = os.environ if env is None else env
    secrets: set[str] = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if is_secret_key(key) and isinstance(item, str):
                    secrets.add(environment.get(item, "") if key.endswith("_env") else item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(config)
    secrets.add(environment.get("TOPIC4_MODEL_API_KEY", ""))
    if isinstance(config, dict) and config_path is not None:
        blocks = [config.get("semantic", {}), *config.get("integrations", {}).values()]
        for block in blocks:
            reference = block.get("config_path")
            if isinstance(reference, str):
                path = Path(config_path).resolve().parent / os.path.expandvars(os.path.expanduser(reference))
                try:
                    collect(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
    # 'local' is the delivery's public no-auth placeholder, not a credential.
    return secrets - {"", "local", REDACTED}


def redact_diagnostics(value: Any, secrets: set[str]) -> Any:
    """Redact known credential values in diagnostics, leaving input evidence alone."""
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {key: redact_diagnostics(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_diagnostics(item, secrets) for item in value]
    return value
