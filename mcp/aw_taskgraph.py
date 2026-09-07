# -*- coding: utf-8 -*-
"""任务图运行时工具簇——业务状态投影、操作契约闸门、任务规划、回放核对、适配器对比、轨迹与候选图(archive/assess/bundle)。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖方向:aw_core ← aw_runtime ← 本簇。
"""
try:
    from aw_runtime import (
    _ok,
    _page_signal_snapshot,
    _result_payload,
    _runtime_context,
    _world,
    )
except ImportError:
    from mcp.aw_runtime import (
    _ok,
    _page_signal_snapshot,
    _result_payload,
    _runtime_context,
    _world,
    )
try:
    from aw_timeline import (
    _trace_store,
    )
except ImportError:
    from mcp.aw_timeline import (
    _trace_store,
    )
try:
    from aw_outcome import (
    _errored_card,
    )
except ImportError:
    from mcp.aw_outcome import (
    _errored_card,
    )
try:
    from task_runtime import (
    build_graph,
    normalize_page_state,
    persistence_enabled,
    plan_graph,
    state_key,
    validate_replay_step,
    )
except ImportError:
    from mcp.task_runtime import (
    build_graph,
    normalize_page_state,
    persistence_enabled,
    plan_graph,
    state_key,
    validate_replay_step,
    )
try:
    from business_runtime import (
    check_operation,
    project_business_state,
    )
except ImportError:
    from mcp.business_runtime import (
    check_operation,
    project_business_state,
    )
try:
    from site_adapter import (
    compare_site_adapters,
    load_site_adapter_file,
    )
except ImportError:
    from mcp.site_adapter import (
    compare_site_adapters,
    load_site_adapter_file,
    )


def _business_state_snapshot(wid):
    w = _world(wid)
    page_signal = _page_signal_snapshot(wid)
    runtime_state = normalize_page_state(page_signal)
    business = project_business_state(runtime_state, w.get("business_state_rules"))
    # 业务状态一旦被明确匹配,它必须成为运行时状态身份的一部分。
    # 否则两个页面事实相同但业务语义不同的节点会被错误合并。
    if business.get("status") == "matched":
        runtime_state["business_state"] = business["state_id"]
        runtime_state["state_key"] = state_key(runtime_state)
    return runtime_state, business


def _t_world_business_state(args):
    """读取当前页面状态对应的显式业务状态。"""
    wid = int(args["world_id"])
    w = _world(wid)
    runtime_state, business = _business_state_snapshot(wid)
    return _ok({
        "world_id": wid,
        "channel": "business-state",
        "runtime_state": runtime_state,
        "business_state": business,
        "rule_count": len(w.get("business_state_rules") or []),
        "source": "declared-rule",
    })


def _t_world_operation_check(args):
    """只检查操作契约，不执行操作。"""
    wid = int(args["world_id"])
    operation = str(args.get("operation") or "").strip()
    if not operation:
        raise ValueError("operation 不能为空")
    runtime_state, business = _business_state_snapshot(wid)
    result = check_operation(
        _world(wid).get("operation_contracts"),
        operation,
        business,
        runtime_context=_runtime_context(_world(wid)),
    )
    return _ok({
        "world_id": wid,
        "channel": "operation-precondition-check",
        "runtime_state": runtime_state,
        "business_state": business,
        "runtime_context": _runtime_context(_world(wid)),
        "check": result,
        "executed": False,
    })


def _graph_trace_source(w, args):
    """收集当前世界及调用方明确指定的归档轨迹,按任务和步骤去除重复记录。"""
    traces = list(w.get("trace_log", []))
    requested_task_ids = []
    for item in args.get("task_ids") or []:
        task_id = str(item or "")[:120]
        if task_id and task_id not in requested_task_ids and len(requested_task_ids) < 50:
            requested_task_ids.append(task_id)
    missing_task_ids = []
    archived_trace_count = 0
    known_trace_keys = {
        (
            str(trace.get("task_id") or ""),
            str(trace.get("step_index") or ""),
            str(trace.get("trace_id") or ""),
        )
        for trace in traces
        if isinstance(trace, dict) and (trace.get("task_id") or trace.get("trace_id"))
    }
    if requested_task_ids:
        if persistence_enabled():
            for task_id in requested_task_ids[:50]:
                rows = _trace_store.read(task_id, limit=1000)
                if not rows:
                    missing_task_ids.append(task_id)
                    continue
                for row in rows:
                    trace_key = (
                        str(row.get("task_id") or task_id),
                        str(row.get("step_index") or ""),
                        str(row.get("trace_id") or ""),
                    )
                    if trace_key in known_trace_keys:
                        continue
                    traces.append(row)
                    known_trace_keys.add(trace_key)
                    archived_trace_count += 1
        else:
            missing_task_ids = list(requested_task_ids)
    return traces, {
        "current_trace_count": len(w.get("trace_log", [])),
        "archived_trace_count": archived_trace_count,
        "task_ids": requested_task_ids,
        "missing_task_ids": missing_task_ids,
    }


def _t_world_task_plan(args):
    """从当前运行时状态沿任务图规划路径,不执行任何页面动作。"""
    wid = int(args["world_id"])
    w = _world(wid)
    runtime_state, business = _business_state_snapshot(wid)
    traces, source = _graph_trace_source(w, args)
    graph = build_graph(
        traces,
        task_id=str(w.get("task_id") or ""),
        goal=str(w.get("task_goal") or ""),
        min_replays=int(args.get("min_replays", 2)),
        valid_until=w.get("graph_valid_until"),
        context=_runtime_context(w),
    )
    plan = plan_graph(
        graph,
        runtime_state.get("state_key"),
        str(args.get("goal_state") or ""),
        max_steps=int(args.get("max_steps", 8)),
        allow_candidate=bool(args.get("allow_candidate", False)),
        current_business_state=business.get("state_id"),
    )
    return _ok({
        "world_id": wid,
        "channel": "task-plan",
        "runtime_state": runtime_state,
        "business_state": business,
        "graph_status": graph.get("status"),
        "plan": plan,
        "executed": False,
        "source": {
            "trace_count": graph.get("trace_count", 0),
            **source,
            "graph": "当前世界内存轨迹及调用方明确指定的归档轨迹",
        },
    })


def _t_world_graph_replay_check(args):
    """将当前世界的一条实际轨迹与指定图边逐项核对,不执行动作。"""
    wid = int(args["world_id"])
    w = _world(wid)
    traces, source = _graph_trace_source(w, args)
    graph = build_graph(
        traces,
        task_id=str(w.get("task_id") or ""),
        goal=str(w.get("task_goal") or ""),
        min_replays=int(args.get("min_replays", 2)),
        valid_until=w.get("graph_valid_until"),
        context=_runtime_context(w),
    )
    edge_id = str(args.get("edge_id") or "")[:160]
    edge = next(
        (item for item in graph.get("edges", [])
         if str(item.get("edge_id") or "") == edge_id),
        None,
    )
    current_traces = list(w.get("trace_log", []))
    trace_step = args.get("trace_step")
    if trace_step is None:
        trace = current_traces[-1] if current_traces else None
    else:
        try:
            wanted_step = int(trace_step)
        except (TypeError, ValueError):
            wanted_step = -1
        trace = next(
            (item for item in current_traces
             if int(item.get("step_index", -1)) == wanted_step),
            None,
        )
    if not edge:
        replay = {
            "status": "no_edge",
            "passed": False,
            "edge_id": edge_id or None,
            "reason": "没有找到指定的任务图迁移边",
        }
    elif not trace:
        replay = {
            "status": "no_trace",
            "passed": False,
            "edge_id": edge_id,
            "reason": "当前世界没有可核对的实际轨迹",
        }
    else:
        replay = validate_replay_step(trace, edge)
    return _ok({
        "world_id": wid,
        "channel": "task-replay-check",
        "edge_id": edge_id or None,
        "graph_status": graph.get("status"),
        "replay": replay,
        "expected_edge": edge,
        "trace": trace,
        "executed": False,
        "source": {**source, "graph": "当前世界内存轨迹及调用方明确指定的归档轨迹"},
    })


def _t_world_adapter_compare(args):
    """比较两个受控目录中的站点业务适配器,只读文件不操作网页。"""
    base_file = str(args.get("base_file") or "").strip()
    candidate_file = str(args.get("candidate_file") or "").strip()
    if not base_file or not candidate_file:
        raise ValueError("base_file 和 candidate_file 都不能为空")
    base = load_site_adapter_file(base_file)
    candidate = load_site_adapter_file(candidate_file)
    return _ok({
        "channel": "site-adapter-compatibility",
        "base_file": base_file,
        "candidate_file": candidate_file,
        "base_adapter": {
            "adapter_id": base.get("adapter_id"),
            "adapter_version": base.get("adapter_version") or None,
            "signature": base.get("signature"),
        },
        "candidate_adapter": {
            "adapter_id": candidate.get("adapter_id"),
            "adapter_version": candidate.get("adapter_version") or None,
            "signature": candidate.get("signature"),
        },
        "comparison": compare_site_adapters(base, candidate),
        "executed": False,
    })


def _t_world_trace(args):
    """读取当前世界的脱敏任务轨迹。"""
    wid = int(args["world_id"])
    since = max(0, int(args.get("since", 0)))
    limit = max(1, min(int(args.get("limit", 50)), 200))
    w = _world(wid)
    all_items = [
        item for item in w.get("trace_log", [])
        if int(item.get("step_index", 0)) > since
    ]
    items = all_items[:limit]
    next_since = int(items[-1].get("step_index", since)) if items else since
    latest = max(
        (int(item.get("step_index", 0)) for item in w.get("trace_log", [])),
        default=0,
    )
    return _ok({
        "world_id": wid,
        "channel": "task-trace",
        "schema_version": "0.1",
        "task_id": w.get("task_id"),
        "trace_id": w.get("trace_id"),
        "task_goal": w.get("task_goal") or None,
        "persistence_enabled": persistence_enabled(),
        "runtime_context": _runtime_context(w),
        "graph_valid_until": w.get("graph_valid_until"),
        "from": since,
        "to": next_since,
        "latest": latest,
        "has_more": len(all_items) > len(items),
        "traces": items,
        "security": {
            "input_values": "已省略",
            "input_digest": "仅保存单向 SHA-256 摘要前缀",
            "page_free_text": "不参与状态身份判断",
        },
    })


def _t_world_graph(args):
    """从当前世界轨迹即时生成候选任务运行时图。"""
    wid = int(args["world_id"])
    w = _world(wid)
    graph = build_graph(
        list(w.get("trace_log", [])),
        task_id=str(w.get("task_id") or ""),
        goal=str(w.get("task_goal") or ""),
        expected_outcomes=args.get("expected_outcomes"),
        min_replays=int(args.get("min_replays", 2)),
        valid_until=w.get("graph_valid_until"),
        context=_runtime_context(w),
    )
    return _ok({
        "world_id": wid,
        "channel": "task-runtime-graph",
        "graph": graph,
        "source": {
            "trace_id": w.get("trace_id"),
            "evidence_latest": int(w.get("evidence_seq", 0)),
            "world_epoch": int(w.get("epoch", 0)),
        },
    })


def _t_world_trace_archive(args):
    """读取关闭网页世界后仍可访问的脱敏轨迹。"""
    task_id = str(args.get("task_id") or "")[:120]
    if not task_id:
        raise ValueError("task_id 不能为空")
    if not persistence_enabled():
        return _ok({
            "channel": "task-trace-archive",
            "enabled": False,
            "task_id": task_id,
            "traces": [],
            "why": "本地轨迹归档默认关闭;请明确设置 AGENT_TASK_RUNTIME_PERSIST=1",
        })
    since = max(0, int(args.get("since", 0)))
    limit = max(1, min(int(args.get("limit", 200)), 1000))
    rows = [
        item for item in _trace_store.read(task_id, limit=limit)
        if int(item.get("step_index", 0)) > since
    ]
    next_since = int(rows[-1].get("step_index", since)) if rows else since
    return _ok({
        "channel": "task-trace-archive",
        "schema_version": "0.1",
        "enabled": True,
        "task_id": task_id,
        "from": since,
        "to": next_since,
        "traces": rows,
        "storage": "本地 JSONL 追加式归档",
    })


def _t_world_graph_archive(args):
    """从已归档轨迹生成关闭网页世界后的候选图。"""
    task_id = str(args.get("task_id") or "")[:120]
    if not task_id:
        raise ValueError("task_id 不能为空")
    if not persistence_enabled():
        return _ok({
            "channel": "task-runtime-graph-archive",
            "enabled": False,
            "task_id": task_id,
            "graph": build_graph([], task_id=task_id, goal=str(args.get("goal") or ""),
                                 expected_outcomes=args.get("expected_outcomes"),
                                 min_replays=int(args.get("min_replays", 2)),
                                 valid_until=args.get("valid_until")),
            "why": "本地轨迹归档默认关闭;请明确设置 AGENT_TASK_RUNTIME_PERSIST=1",
        })
    traces = _trace_store.read(task_id, limit=1000)
    return _ok({
        "channel": "task-runtime-graph-archive",
        "enabled": True,
        "task_id": task_id,
        "graph": build_graph(
            traces,
            task_id=task_id,
            goal=str(args.get("goal") or ""),
            expected_outcomes=args.get("expected_outcomes"),
            min_replays=int(args.get("min_replays", 2)),
            valid_until=args.get("valid_until"),
        ),
        "source": {"trace_count": len(traces), "storage": "本地 JSONL 追加式归档"},
    })


def _t_world_graph_assess(args):
    """评估当前网页世界的候选图,不执行动作也不发布图。"""
    wid = int(args["world_id"])
    w = _world(wid)
    graph = build_graph(
        list(w.get("trace_log", [])),
        task_id=str(w.get("task_id") or ""),
        goal=str(w.get("task_goal") or ""),
        expected_outcomes=args.get("expected_outcomes"),
        min_replays=int(args.get("min_replays", 2)),
        valid_until=w.get("graph_valid_until"),
        context=_runtime_context(w),
    )
    return _ok({
        "world_id": wid,
        "channel": "task-runtime-graph-assessment",
        "graph_status": graph.get("status"),
        "lifecycle": graph.get("lifecycle"),
        "graph": graph,
        "publishable": False,
        "why": "评估接口只生成审查结果,不会自动发布候选图",
    })


def _t_world_graph_bundle(args):
    """合并多个已归档任务实例,用于跨会话回放评估。"""
    raw_ids = args.get("task_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("task_ids 必须是非空列表")
    task_ids = []
    for item in raw_ids[:50]:
        task_id = str(item or "")[:120]
        if task_id and task_id not in task_ids:
            task_ids.append(task_id)
    if not task_ids:
        raise ValueError("task_ids 不能全部为空")
    if not persistence_enabled():
        return _ok({
            "channel": "task-runtime-graph-bundle",
            "enabled": False,
            "task_ids": task_ids,
            "graph": build_graph([], task_id="bundle", goal=str(args.get("goal") or ""),
                                 expected_outcomes=args.get("expected_outcomes"),
                                 min_replays=int(args.get("min_replays", 2)),
                                 valid_until=args.get("valid_until")),
            "why": "本地轨迹归档默认关闭;请明确设置 AGENT_TASK_RUNTIME_PERSIST=1",
        })
    traces = []
    missing = []
    for task_id in task_ids:
        rows = _trace_store.read(task_id, limit=1000)
        if not rows:
            missing.append(task_id)
        traces.extend(rows)
    graph = build_graph(
        traces,
        task_id="bundle",
        goal=str(args.get("goal") or ""),
        expected_outcomes=args.get("expected_outcomes"),
        min_replays=int(args.get("min_replays", 2)),
        valid_until=args.get("valid_until"),
    )
    return _ok({
        "channel": "task-runtime-graph-bundle",
        "enabled": True,
        "task_ids": task_ids,
        "missing_task_ids": missing,
        "graph": graph,
        "source": {"task_count": len(task_ids), "trace_count": len(traces)},
        "publishable": False,
    })


def _contract_gate(wid, action_args, before_signal=None):
    """严格模式的执行前闸门:契约失败时记录 errored,但不触碰页面。"""
    w = _world(wid)
    if not (w.get("enforce_contracts") or bool(action_args.get("enforce_contracts"))):
        return None
    operation = str(action_args.get("operation") or "").strip()
    # 兼容已有低层调用:没有声明业务 operation 时仍允许原有 world_act 行为。
    # 一旦声明 operation,严格模式就不能绕过契约。
    if not operation:
        return None
    _, business = _business_state_snapshot(wid)
    check = check_operation(
        w.get("operation_contracts"),
        operation,
        business,
        runtime_context=_runtime_context(w),
    )
    if check.get("allowed"):
        return None
    reason = f"业务操作 {operation} 未通过前置检查: {check.get('reason', '未知原因')}"
    card = _errored_card(
        wid,
        "world_act.contract",
        action_args,
        before_signal,
        ValueError(reason),
    )
    payload = _result_payload(card)
    if payload:
        payload["contract_check"] = check
        payload["executed"] = False
        return _ok(payload)
    return card
