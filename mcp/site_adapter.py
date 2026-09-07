# -*- coding: utf-8 -*-
"""站点业务适配器的文件化、签名和兼容性比较。

适配器是显式声明的业务配置，不是从网页自由文本自动逆向出来的模型。
本模块只负责安全读取、规范化和比较，不负责执行页面动作。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from business_runtime import normalize_site_adapter
except ImportError:
    from mcp.business_runtime import normalize_site_adapter


SCHEMA_VERSION = "0.1"
SITE_ADAPTER_DIR = Path(__file__).parent / "site_adapters"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def adapter_signature(adapter: object) -> str:
    """对规范化适配器做稳定摘要,不把摘要当成安全凭据。"""
    normalized = normalize_site_adapter(adapter)
    return hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()[:16]


def _safe_file_path(file_name: str) -> Path:
    raw = str(file_name or "").strip()
    if not raw or Path(raw).name != raw or not raw.lower().endswith(".json"):
        raise ValueError("站点适配器只能使用 site_adapters 目录下的 JSON 文件名")
    base = SITE_ADAPTER_DIR.resolve()
    path = (base / raw).resolve()
    if path.parent != base:
        raise ValueError("站点适配器文件路径越过了受控目录")
    return path


def load_site_adapter_file(file_name: str) -> dict:
    """只允许读取受控目录下的适配器 JSON 文件。"""
    path = _safe_file_path(file_name)
    if not path.exists() or not path.is_file():
        raise ValueError(f"站点适配器文件不存在: {file_name}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"站点适配器文件不是有效 JSON: {file_name}") from exc
    normalized = normalize_site_adapter(raw)
    if not normalized.get("adapter_id"):
        raise ValueError("站点适配器必须声明 adapter_id")
    normalized["source_file"] = path.name
    normalized["signature"] = adapter_signature(normalized)
    return normalized


def _by_id(items: object, key: str) -> dict[str, dict]:
    return {
        str(item.get(key)): item
        for item in (items or [])
        if isinstance(item, dict) and item.get(key)
    }


def compare_site_adapters(base: object, candidate: object) -> dict:
    """比较两个适配器,对会影响旧任务图的变化采取保守判定。"""
    base_adapter = normalize_site_adapter(base)
    candidate_adapter = normalize_site_adapter(candidate)
    base_id = base_adapter.get("adapter_id")
    candidate_id = candidate_adapter.get("adapter_id")
    result = {
        "schema_version": SCHEMA_VERSION,
        "base": {
            "adapter_id": base_id or None,
            "adapter_version": base_adapter.get("adapter_version") or None,
            "signature": adapter_signature(base_adapter),
        },
        "candidate": {
            "adapter_id": candidate_id or None,
            "adapter_version": candidate_adapter.get("adapter_version") or None,
            "signature": adapter_signature(candidate_adapter),
        },
        "state_rules": {"added": [], "removed": [], "changed": []},
        "operations": {"added": [], "removed": [], "changed": []},
        "breaking_changes": [],
        "review_required": [],
    }
    if not base_id or not candidate_id:
        result["status"] = "invalid"
        result["reason"] = "两个适配器都必须声明 adapter_id"
        return result
    if base_id != candidate_id:
        result["status"] = "identity_mismatch"
        result["reason"] = "两个适配器的 adapter_id 不同,不能直接比较兼容性"
        return result
    if result["base"]["signature"] == result["candidate"]["signature"]:
        result["status"] = "same"
        result["reason"] = "两个适配器的规范化内容一致"
        return result

    base_states = _by_id(base_adapter.get("state_rules"), "id")
    candidate_states = _by_id(candidate_adapter.get("state_rules"), "id")
    result["state_rules"]["added"] = sorted(set(candidate_states) - set(base_states))
    result["state_rules"]["removed"] = sorted(set(base_states) - set(candidate_states))
    result["state_rules"]["changed"] = sorted(
        key for key in set(base_states) & set(candidate_states)
        if _canonical(base_states[key]) != _canonical(candidate_states[key])
    )
    base_ops = _by_id(base_adapter.get("operation_contracts"), "name")
    candidate_ops = _by_id(candidate_adapter.get("operation_contracts"), "name")
    result["operations"]["added"] = sorted(set(candidate_ops) - set(base_ops))
    result["operations"]["removed"] = sorted(set(base_ops) - set(candidate_ops))
    result["operations"]["changed"] = sorted(
        key for key in set(base_ops) & set(candidate_ops)
        if _canonical(base_ops[key]) != _canonical(candidate_ops[key])
    )

    for key in result["state_rules"]["removed"]:
        result["breaking_changes"].append(f"移除了业务状态规则: {key}")
    for key in result["state_rules"]["changed"]:
        result["breaking_changes"].append(f"修改了业务状态规则: {key}")
    for key in result["operations"]["removed"]:
        result["breaking_changes"].append(f"移除了业务操作契约: {key}")
    for key in result["operations"]["changed"]:
        result["breaking_changes"].append(f"修改了业务操作契约: {key}")
    for key in result["state_rules"]["added"]:
        result["review_required"].append(f"新增业务状态规则,需检查是否造成规则重叠: {key}")
    for key in result["operations"]["added"]:
        result["review_required"].append(f"新增业务操作契约,需补充回放覆盖: {key}")

    if base_adapter.get("workflow_id") != candidate_adapter.get("workflow_id"):
        result["breaking_changes"].append("修改了 workflow_id")
    if base_adapter.get("site_version") != candidate_adapter.get("site_version"):
        result["review_required"].append("修改了 site_version,历史任务图需要重新回放")
    result["status"] = "incompatible" if result["breaking_changes"] else "compatible"
    result["reason"] = (
        "发现会影响既有任务图的破坏性变化"
        if result["breaking_changes"]
        else "没有发现结构性破坏变化,但仍需完成新增内容的回放检查"
    )
    return result
