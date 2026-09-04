# -*- coding: utf-8 -*-
"""任务运行时图的第一阶段基础类型。

本模块只做四件事：
1. 把页面动作转换成脱敏的任务轨迹；
2. 把动作前后的小状态转换成稳定的状态快照；
3. 从轨迹建立“候选”状态迁移图；
4. 在明确开启时，将脱敏轨迹追加归档到本地 JSONL 文件。

这里的图是观测结果，不是网站源码的逆向还原。边的出现次数只表示
观测次数，不自动把频率解释成业务规则，也不删除只出现过一次的分支。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


TRACE_SCHEMA_VERSION = "0.1"
GRAPH_SCHEMA_VERSION = "0.1"
_DYNAMIC_ELEMENT_ID = re.compile(r"^el_\d+$")


def new_id(prefix: str) -> str:
    """生成不可预测但不携带业务含义的运行时编号。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _digest(value: object) -> str:
    raw = str(value).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def _clip(value: object, limit: int = 120) -> str:
    return str(value or "")[:limit]


def stable_route(url: object) -> str:
    """只保留路由，不把查询参数、锚点或可能含凭据的文本写入图。"""
    raw = _clip(url, 500)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        path = parts.path or "/"
        if parts.scheme == "file":
            return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    except Exception:
        return raw.split("?", 1)[0].split("#", 1)[0]


def _field_key(field: dict, index: int) -> str:
    """字段编号只作为状态等价判断依据，不保存字段的实际值。"""
    field_id = field.get("id") or ""
    if _DYNAMIC_ELEMENT_ID.match(str(field_id)):
        field_id = ""
    raw = "|".join(
        str(value or "")
        for value in (field_id, field.get("name"), field.get("type"),
                      field.get("role"), field.get("placeholder"))
    )
    return f"field-{index}-{_digest(raw)}"


def _overlay_ref(item: object) -> str:
    if not isinstance(item, dict):
        return f"text-{_digest(item)}"
    identifier = item.get("id") or item.get("name") or item.get("role") or "overlay"
    # el_N 是当前页面内的临时编号，不能成为跨轨迹状态的身份。
    if _DYNAMIC_ELEMENT_ID.match(str(identifier)):
        identifier = item.get("name") or item.get("role") or "overlay"
    return f"overlay-{_digest(identifier)}"


def normalize_page_state(signal: dict | None, outcome: str | None = None) -> dict:
    """把页面小状态转换为可比较的运行时状态，不保存输入值和自由文本。"""
    signal = signal if isinstance(signal, dict) else {}
    raw_fields = signal.get("form_fields") or []
    if not raw_fields and signal.get("forms"):
        # 兼容旧快照：旧 forms 只列出有值字段，因此只能保守标为 partial。
        raw_fields = [dict(item, _legacy_filled=True) for item in signal.get("forms", [])]

    fields = []
    for index, field in enumerate(raw_fields):
        if not isinstance(field, dict):
            continue
        filled = bool(field.get("filled", field.get("_legacy_filled", False)))
        fields.append({"key": _field_key(field, index), "filled": filled})

    total = len(fields)
    filled = sum(1 for item in fields if item["filled"])
    if total == 0:
        form_state = "none"
    elif filled == 0:
        form_state = "empty"
    elif filled == total:
        form_state = "complete"
    else:
        form_state = "partial"

    overlays = []
    for kind in ("dialogs", "menus"):
        for item in signal.get(kind, []) or []:
            overlays.append({"kind": kind, "ref": _overlay_ref(item)})
    overlays.sort(key=lambda item: (item["kind"], item["ref"]))

    # page_outcome 是页面观测结果，不等于业务事实；只把异常/阻断类结果
    # 作为状态提示保留，正常 progressed 不强行写入状态身份。
    outcome_hint = outcome if outcome in ("challenged", "errored", "uncertain") else None
    state = {
        "route": stable_route(signal.get("url")),
        "page_state": _clip(signal.get("state"), 30) or "unknown",
        "form_state": form_state,
        "form_fields": fields,
        "overlays": overlays,
        "outcome_hint": outcome_hint,
    }
    state["state_key"] = state_key(state)
    return state


def state_key(state: dict) -> str:
    body = {k: v for k, v in state.items() if k != "state_key"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"state-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def _value_meta(value: object) -> dict:
    text = str(value or "")
    return {"present": bool(text), "length": len(text), "sha256": _digest(text)}


def persistence_enabled() -> bool:
    """轨迹归档默认关闭，只有明确设置环境变量才写入本地。"""
    return os.environ.get("AGENT_TASK_RUNTIME_PERSIST", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


class TraceStore:
    """脱敏轨迹的本地追加式归档器(JSONL：每行一条 JSON 记录)。"""

    def __init__(self, base_dir: str | Path | None = None):
        configured = base_dir or os.environ.get("AGENT_TASK_RUNTIME_STORE_DIR")
        self.base_dir = Path(configured) if configured else Path(__file__).parent / "runtime_traces"

    def path_for(self, task_id: str) -> Path:
        # 文件名不暴露任务目标或账号名，任务编号只作为内容查询条件。
        return self.base_dir / f"task-{_digest(task_id)}.jsonl"

    def append(self, task_id: str, trace: dict) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(task_id)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(trace, ensure_ascii=False, separators=(",", ":")) + "\n")

    def read(self, task_id: str, limit: int = 200) -> list[dict]:
        path = self.path_for(task_id)
        if not path.exists():
            return []
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        return rows[-max(1, min(int(limit), 1000)):]


def sanitize_bindings(value: object) -> list[dict]:
    """只保存逻辑字段引用，不保存字段实际值。"""
    if not isinstance(value, list):
        return []
    bindings = []
    allowed = ("name", "ref", "from", "to", "source", "target", "required")
    for item in value:
        if isinstance(item, str):
            bindings.append({"ref": _clip(item, 160)})
            continue
        if not isinstance(item, dict):
            continue
        binding = {}
        for key in allowed:
            if item.get(key) is not None:
                binding[key] = bool(item[key]) if key == "required" else _clip(item[key], 160)
        if binding:
            bindings.append(binding)
    return bindings


def sanitize_action(action: str, args: dict | None) -> dict:
    """保存动作结构，但把文本输入替换为长度和摘要，避免轨迹变成凭据仓库。"""
    args = args if isinstance(args, dict) else {}
    safe = {
        "name": _clip(action, 80),
        "kind": _clip(args.get("kind") or action.replace("world_", ""), 40),
    }
    for key in ("id", "key", "operation", "executor"):
        if args.get(key) is not None:
            safe[key] = _clip(args.get(key), 160)
    if args.get("text") is not None:
        safe["text"] = _value_meta(args.get("text"))
    if isinstance(args.get("fields"), list):
        safe["fields"] = [
            {
                "id": _clip(item.get("id"), 160),
                "text": _value_meta(item.get("text")),
            }
            for item in args["fields"]
            if isinstance(item, dict)
        ]
    for key in ("input_bindings", "output_bindings"):
        if key in args:
            safe[key] = sanitize_bindings(args.get(key))
    if isinstance(args.get("steps"), list):
        safe["steps"] = [
            sanitize_action("world_act.step", step)
            for step in args["steps"]
            if isinstance(step, dict)
        ]
    for key in ("visual_evidence", "verbose", "type_delay_ms"):
        if key in args:
            safe[key] = args[key]
    return safe


def build_trace_entry(
    *,
    trace_id: str,
    task_id: str,
    step_index: int,
    action: str,
    args: dict,
    before: dict,
    after: dict,
    payload: dict,
    evidence_seq: int,
    world_epoch: int,
    context: dict | None = None,
) -> dict:
    effect = payload.get("effect") or {}
    situation = payload.get("situation") or {}
    page_outcome = payload.get("page_outcome")
    before_state = normalize_page_state(before)
    after_state = normalize_page_state(after, page_outcome)
    operation = _clip(args.get("operation") or action, 120)
    safe_action = sanitize_action(action, args)
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "task_id": task_id,
        "step_index": int(step_index),
        "operation_id": _clip(args.get("operation_id") or new_id("op"), 80),
        "operation": operation,
        "action": safe_action,
        "executor": _clip(args.get("executor") or "world_act", 80),
        "before": before_state,
        "after": after_state,
        "effect": {
            "page_outcome": page_outcome,
            "situation": _clip(situation.get("type"), 80) or None,
            "verdict": _clip(effect.get("verdict"), 80) or None,
            "confidence": _clip(effect.get("confidence"), 30) or None,
        },
        "dataflow": {
            "inputs": safe_action.get("input_bindings", []),
            "outputs": safe_action.get("output_bindings", []),
        },
        "runtime_context": {
            key: _clip(context.get(key), 160)
            for key in ("workflow_id", "site_version", "role", "permission_scope")
            if isinstance(context, dict) and context.get(key) is not None
        },
        "evidence_ref": {
            "evidence_seq": int(evidence_seq),
            "changes_seq": {
                "before": before.get("changes_seq", 0),
                "after": after.get("changes_seq", 0),
            },
            "world_epoch": int(world_epoch),
        },
    }


class TaskRuntimeGraph:
    """从已记录轨迹构建候选状态迁移图。"""

    def __init__(self, task_id: str = "", goal: str = "", context: dict | None = None):
        self.task_id = task_id
        self.goal = _clip(goal, 300)
        self.context = {
            key: _clip(context.get(key), 160)
            for key in ("workflow_id", "site_version", "role", "permission_scope")
            if isinstance(context, dict) and context.get(key) is not None
        }
        self.states: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.trace_count = 0

    def _add_state(self, snapshot: dict) -> str:
        key = snapshot.get("state_key") or state_key(snapshot)
        if key not in self.states:
            self.states[key] = {
                "state_key": key,
                "snapshot": snapshot,
                "observations": 0,
            }
        self.states[key]["observations"] += 1
        return key

    def add(self, trace: dict) -> None:
        before = trace.get("before") or {}
        after = trace.get("after") or {}
        from_key = self._add_state(before)
        to_key = self._add_state(after)
        effect = trace.get("effect") or {}
        operation = _clip(trace.get("operation") or "unknown", 120)
        outcome = _clip(effect.get("page_outcome"), 40) or "unknown"
        situation = _clip(effect.get("situation"), 80) or "none"
        edge_material = "|".join((from_key, to_key, operation, outcome, situation))
        edge_id = f"edge-{_digest(edge_material)}"
        edge = self.edges.setdefault(edge_id, {
            "edge_id": edge_id,
            "from": from_key,
            "to": to_key,
            "operation": operation,
            "status": "candidate",
            "preconditions": {"state_key": from_key},
            "effects": {"state_key": to_key},
            "dataflow": {"inputs": [], "outputs": []},
            "outcomes": [],
            "confidences": [],
            "observations": 0,
            "trace_ids": [],
            "evidence_refs": [],
        })
        edge["observations"] += 1
        if outcome not in edge["outcomes"]:
            edge["outcomes"].append(outcome)
        confidence = _clip(effect.get("confidence"), 30)
        if confidence and confidence not in edge["confidences"]:
            edge["confidences"].append(confidence)
        trace_id = _clip(trace.get("trace_id"), 100)
        if trace_id and trace_id not in edge["trace_ids"]:
            edge["trace_ids"].append(trace_id)
        flow = trace.get("dataflow") or {}
        for direction in ("inputs", "outputs"):
            for binding in flow.get(direction, []) or []:
                if binding not in edge["dataflow"][direction]:
                    edge["dataflow"][direction].append(binding)
        ref = trace.get("evidence_ref") or {}
        if ref and ref not in edge["evidence_refs"]:
            edge["evidence_refs"].append(ref)
        edge["effects"]["page_outcome"] = outcome
        edge["effects"]["situation"] = situation
        self.trace_count += 1

    def to_dict(self) -> dict:
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "task_id": self.task_id,
            "goal": self.goal,
            "source_context": self.context,
            "status": "candidate",
            "trace_count": self.trace_count,
            "states": [self.states[key] for key in sorted(self.states)],
            "edges": [self.edges[key] for key in sorted(self.edges)],
            "notes": [
                "候选图只表示已观测迁移,不代表完整业务规则",
                "observations 只记录出现次数,不是执行概率",
                "dataflow 只接受明确的逻辑输入输出引用,不自动猜测真实值",
            ],
        }


def build_graph(
    traces: list[dict],
    task_id: str = "",
    goal: str = "",
    *,
    expected_outcomes: list[str] | None = None,
    min_replays: int = 2,
    valid_until: int | None = None,
    context: dict | None = None,
) -> dict:
    if context is None:
        context = next(
            (trace.get("runtime_context") for trace in traces or []
             if isinstance(trace, dict) and trace.get("runtime_context")),
            None,
        )
    graph = TaskRuntimeGraph(task_id=task_id, goal=goal, context=context)
    for trace in traces or []:
        if isinstance(trace, dict):
            graph.add(trace)
    return assess_graph(
        graph.to_dict(),
        expected_outcomes=expected_outcomes,
        min_replays=min_replays,
        valid_until=valid_until,
    )


def assess_graph(
    graph: dict,
    *,
    expected_outcomes: list[str] | None = None,
    min_replays: int = 2,
    valid_until: int | None = None,
) -> dict:
    """根据明确的回放阈值评估图生命周期，不自动改变外部发布状态。"""
    graph = dict(graph or {})
    min_replays = max(1, int(min_replays))
    expected = [str(item) for item in (expected_outcomes or []) if str(item)]
    now = int(time.time())
    expired = valid_until is not None and now >= int(valid_until)
    observed_outcomes = sorted({
        outcome
        for edge in graph.get("edges", [])
        for outcome in edge.get("outcomes", [])
    })
    missing_outcomes = [item for item in expected if item not in observed_outcomes]
    edge_statuses = []
    all_edge_verified = bool(graph.get("edges"))
    any_replayed = False
    for edge in graph.get("edges", []):
        trace_ids = sorted(set(edge.get("trace_ids", [])))
        replay_count = len(trace_ids)
        has_uncertain = "uncertain" in (edge.get("outcomes") or [])
        has_low_confidence = "low" in (edge.get("confidences") or [])
        if expired:
            status = "expired"
        elif replay_count >= min_replays and expected and not has_uncertain and not has_low_confidence:
            status = "verified"
        elif replay_count >= min_replays:
            status = "replayed"
        else:
            status = "candidate"
        edge["verification"] = {
            "status": status,
            "independent_runs": replay_count,
            "required_replays": min_replays,
            "has_uncertain_outcome": has_uncertain,
            "has_low_confidence": has_low_confidence,
        }
        edge_statuses.append(status)
        any_replayed = any_replayed or status in ("replayed", "verified")
        all_edge_verified = all_edge_verified and status == "verified"

    if expired:
        status = "expired"
    elif expected and not missing_outcomes and all_edge_verified:
        status = "verified"
    elif any_replayed:
        status = "replayed"
    else:
        status = "candidate"
    graph["status"] = status
    graph["lifecycle"] = {
        "status": status,
        "checked_at": now,
        "required_replays": min_replays,
        "expected_outcomes": expected,
        "observed_outcomes": observed_outcomes,
        "missing_outcomes": missing_outcomes,
        "expires_at": int(valid_until) if valid_until is not None else None,
        "promotion_rule": "必须提供预期分支,所有边达到回放阈值且不存在 uncertain 才能标记 verified",
    }
    return graph


def validate_transition(current_state: dict, edge: dict) -> dict:
    """验证一条候选边的最小前置条件：当前状态必须匹配边的起点。"""
    current_key = (current_state or {}).get("state_key")
    if not current_key:
        current_key = state_key(current_state or {})
    required_key = ((edge or {}).get("preconditions") or {}).get("state_key")
    allowed = bool(required_key and current_key == required_key)
    return {
        "allowed": allowed,
        "current_state_key": current_key,
        "required_state_key": required_key,
        "reason": "当前状态满足迁移起点" if allowed else "当前状态不满足迁移前置条件",
    }
