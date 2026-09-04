# -*- coding: utf-8 -*-
"""显式业务状态规则与操作契约。

页面运行时只能告诉我们“看到了什么”。本模块允许网站适配器另外声明：
这些页面事实在当前任务中对应什么业务状态、某个业务操作需要什么前置状态。
没有声明、声明冲突或无法匹配时，结果保持 unknown/ambiguous，不自动猜测。
"""
from __future__ import annotations

try:
    from task_runtime import sanitize_bindings, state_key
except ImportError:
    from mcp.task_runtime import sanitize_bindings, state_key


SCHEMA_VERSION = "0.1"


def _text(value: object, limit: int = 160) -> str:
    return str(value or "")[:limit]


def normalize_state_rules(rules: object) -> list[dict]:
    if not isinstance(rules, list):
        return []
    normalized = []
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        state_id = _text(rule.get("id"), 120)
        when = rule.get("when")
        if not state_id or state_id in seen or not isinstance(when, dict):
            continue
        seen.add(state_id)
        normalized.append({
            "id": state_id,
            "when": {
                _text(key, 80): value
                for key, value in when.items()
                if isinstance(key, str)
            },
            "description": _text(rule.get("description"), 200),
        })
    return normalized


def normalize_operation_contracts(contracts: object) -> list[dict]:
    if not isinstance(contracts, list):
        return []
    normalized = []
    seen = set()
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        name = _text(contract.get("name"), 120)
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append({
            "name": name,
            "preconditions": [
                _text(item, 120) for item in (contract.get("preconditions") or [])
                if str(item)
            ],
            "inputs": sanitize_bindings(contract.get("inputs")),
            "outputs": sanitize_bindings(contract.get("outputs")),
            "effects": [
                _text(item, 120) for item in (contract.get("effects") or [])
                if str(item)
            ],
            "executor": _text(contract.get("executor"), 80) or "world_act",
            "description": _text(contract.get("description"), 200),
        })
    return normalized


def _match_condition(runtime_state: dict, key: str, expected: object) -> bool:
    if key == "has_overlay":
        return any(item.get("kind") == expected for item in runtime_state.get("overlays", []))
    if key == "has_any_overlay":
        return bool(runtime_state.get("overlays")) == bool(expected)
    actual = runtime_state.get(key)
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def project_business_state(runtime_state: dict, rules: object) -> dict:
    """用显式规则投影业务状态；多规则命中时拒绝擅自选择。"""
    normalized = normalize_state_rules(rules)
    matches = [
        rule for rule in normalized
        if all(_match_condition(runtime_state or {}, key, expected)
               for key, expected in rule["when"].items())
    ]
    if len(matches) == 1:
        rule = matches[0]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "matched",
            "state_id": rule["id"],
            "rule_id": rule["id"],
            "description": rule.get("description") or None,
            "source": "declared-rule",
        }
    if len(matches) > 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ambiguous",
            "state_id": None,
            "matches": [rule["id"] for rule in matches],
            "source": "declared-rule",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "unknown",
        "state_id": None,
        "matches": [],
        "source": "declared-rule",
    }


def operation_contract(contracts: object, name: str) -> dict | None:
    for contract in normalize_operation_contracts(contracts):
        if contract["name"] == name:
            return contract
    return None


def check_operation(
    contracts: object,
    operation: str,
    business_state: dict,
) -> dict:
    contract = operation_contract(contracts, operation)
    if not contract:
        return {
            "allowed": False,
            "status": "unknown_contract",
            "operation": _text(operation, 120),
            "reason": "没有找到明确的业务操作契约",
        }
    state_id = (business_state or {}).get("state_id")
    state_status = (business_state or {}).get("status")
    if state_status != "matched" or not state_id:
        return {
            "allowed": False,
            "status": "unknown_state",
            "operation": contract["name"],
            "contract": contract,
            "reason": "当前业务状态未知或规则冲突,不能执行操作",
        }
    required = contract.get("preconditions") or []
    if required and state_id not in required:
        return {
            "allowed": False,
            "status": "precondition_failed",
            "operation": contract["name"],
            "contract": contract,
            "current_state": state_id,
            "required_states": required,
            "reason": "当前业务状态不满足操作前置条件",
        }
    return {
        "allowed": True,
        "status": "allowed",
        "operation": contract["name"],
        "contract": contract,
        "current_state": state_id,
        "reason": "当前业务状态满足操作前置条件",
    }


def attach_business_runtime(trace: dict, rules: object, contracts: object) -> dict:
    """把业务状态投影和匹配到的操作契约附加到一条轨迹。"""
    if not rules and not contracts:
        return trace
    before = trace.get("before") or {}
    after = trace.get("after") or {}
    before_business = project_business_state(before, rules)
    after_business = project_business_state(after, rules)
    trace["business_before"] = before_business
    trace["business_after"] = after_business
    if before_business.get("status") == "matched":
        before["business_state"] = before_business["state_id"]
        before["state_key"] = state_key(before)
    if after_business.get("status") == "matched":
        after["business_state"] = after_business["state_id"]
        after["state_key"] = state_key(after)
    contract = operation_contract(contracts, trace.get("operation") or "")
    if contract:
        trace["operation_contract"] = contract
        if not trace.get("dataflow", {}).get("inputs"):
            trace.setdefault("dataflow", {})["inputs"] = contract.get("inputs", [])
        if not trace.get("dataflow", {}).get("outputs"):
            trace.setdefault("dataflow", {})["outputs"] = contract.get("outputs", [])
    return trace

