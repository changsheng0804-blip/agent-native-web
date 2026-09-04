# -*- coding: utf-8 -*-
"""任务运行时图的第一阶段基础类型。

本模块只做三件事：
1. 把页面动作转换成脱敏的任务轨迹；
2. 把动作前后的小状态转换成稳定的状态快照；
3. 从轨迹建立“候选”状态迁移图。

这里的图是观测结果，不是网站源码的逆向还原。边的出现次数只表示
观测次数，不自动把频率解释成业务规则，也不删除只出现过一次的分支。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
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
) -> dict:
    effect = payload.get("effect") or {}
    situation = payload.get("situation") or {}
    page_outcome = payload.get("page_outcome")
    before_state = normalize_page_state(before)
    after_state = normalize_page_state(after, page_outcome)
    operation = _clip(args.get("operation") or action, 120)
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "task_id": task_id,
        "step_index": int(step_index),
        "operation_id": _clip(args.get("operation_id") or new_id("op"), 80),
        "operation": operation,
        "action": sanitize_action(action, args),
        "executor": _clip(args.get("executor") or "world_act", 80),
        "before": before_state,
        "after": after_state,
        "effect": {
            "page_outcome": page_outcome,
            "situation": _clip(situation.get("type"), 80) or None,
            "verdict": _clip(effect.get("verdict"), 80) or None,
            "confidence": _clip(effect.get("confidence"), 30) or None,
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

    def __init__(self, task_id: str = "", goal: str = ""):
        self.task_id = task_id
        self.goal = _clip(goal, 300)
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
            "dataflow": [],
            "outcomes": [],
            "observations": 0,
            "evidence_refs": [],
        })
        edge["observations"] += 1
        if outcome not in edge["outcomes"]:
            edge["outcomes"].append(outcome)
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
            "status": "candidate",
            "trace_count": self.trace_count,
            "states": [self.states[key] for key in sorted(self.states)],
            "edges": [self.edges[key] for key in sorted(self.edges)],
            "notes": [
                "候选图只表示已观测迁移,不代表完整业务规则",
                "observations 只记录出现次数,不是执行概率",
                "dataflow 需要后续由明确输入输出契约补充",
            ],
        }


def build_graph(traces: list[dict], task_id: str = "", goal: str = "") -> dict:
    graph = TaskRuntimeGraph(task_id=task_id, goal=goal)
    for trace in traces or []:
        if isinstance(trace, dict):
            graph.add(trace)
    return graph.to_dict()
