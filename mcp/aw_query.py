# -*- coding: utf-8 -*-
"""查询与生命周期工具簇——entities/entity/layers/map/resolve/changes/change_digest/state/find/outcome/close/list。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖方向:aw_core ← aw_runtime ← 本簇。
"""
import json, time
from aw_core import (
SOURCE_FACT,
SOURCE_UNTRUSTED,
_DIGEST_HIGH_ROLES,
_entity_match,
_event_importance,
)
from aw_runtime import (
PROFILES_DIR,
_activate_new_page,
_evaluate,
_evaluate_query_retry,
_expire_idle_sessions,
_ok,
_page_signal_snapshot,
_resolve_id,
_task_public,
_task_update,
_world,
_world_pages_summary,
_worlds,
)


def _t_world_entities(args):
    wid = args["world_id"]
    try:
        _activate_new_page(wid, poll_ms=0)
    except Exception:
        pass
    f = {k: v for k, v in args.items() if k not in ("world_id",) and v is not None}
    if "in_viewport" in f:
        f["inViewport"] = f.pop("in_viewport")
    if "max_results" in f:
        f["maxResults"] = f.pop("max_results")
    try:
        entities = _evaluate_query_retry(wid, "(f) => agentWorld.query.findEntities(f)", f)
    except Exception as exc:
        # 转链页连续换文档时，返回结构化 pending，不把异常文本交给 MCP 客户端。
        try:
            current_url = _world(wid)["page"].url[:300]
        except Exception:
            current_url = ""
        return _ok({"world_id": wid, "count": 0, "entities": [], "pending": True,
                    "retry_after_ms": 500, "url": current_url,
                    "reason": "页面正在切换，网页运行时尚未就绪",
                    "error_type": type(exc).__name__})
    return _ok({"world_id": wid, "count": len(entities), "entities": entities})


def _t_world_entity(args):
    wid = args["world_id"]
    try:
        target = _resolve_id(wid, args["id"])
        ent = _evaluate_query_retry(wid, "(id) => agentWorld.query.getEntity(id)", target)
    except Exception as exc:
        try:
            current_url = _world(wid)["page"].url[:300]
        except Exception:
            current_url = ""
        return _ok({"world_id": wid, "id": args.get("id"), "pending": True,
                    "retry_after_ms": 500, "url": current_url,
                    "reason": "页面正在切换，网页运行时尚未就绪",
                    "error_type": type(exc).__name__})
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    # F2 来源标记:页面自由文本(name/text/attributes.ariaLabel/placeholder)标 untrusted,
    # 编号/指纹/坐标等结构事实标 fact
    ent = dict(ent)
    ent["sources"] = {
        "id": SOURCE_FACT,
        "fingerprint": SOURCE_FACT,
        "bounds": SOURCE_FACT,
        "semantic": SOURCE_FACT,
        "name": SOURCE_UNTRUSTED,
        "text": SOURCE_UNTRUSTED,
        "attributes.ariaLabel": SOURCE_UNTRUSTED,
        "attributes.placeholder": SOURCE_UNTRUSTED,
        "attributes.value": SOURCE_UNTRUSTED,
    }
    return _ok(ent)


def _t_world_layers(args):
    wid = args["world_id"]
    return _ok(_evaluate(wid, "agentWorld.query.layers()"))


def _t_world_map(args):
    wid = args["world_id"]
    max_entries = int(args.get("max_entries", 6))
    return _ok(_evaluate(wid, "(n) => agentWorld.query.map(n)", max_entries))


def _t_world_resolve(args):
    wid = args["world_id"]
    return _ok(_evaluate(wid, "(q) => agentWorld.query.resolve(q)", args["query"]))


# ── 变更可读化(语义摘要 + 重要性加权)──────────────────────────
# 目标:world_changes 返回的不再是"裸事件流",而是带重要性标注 + 人话摘要的结构。
# 这是实时闭环反馈的基础设施:让智能体每轮少读、快速判断"页面发生了什么、值不值得看"。

# 交互/结构性语义角色 → 高重要性(出现/消失通常是操作结果)
# 供 effect 判定使用(宽口径:按钮/链接出现也可能是操作结果的间接证据)


def _change_digest(events):
    """把一批变更事件归纳成结构化语义摘要(CAD 图纸风格)。
    返回 {counts, key}——不写人话句子,只用强 ID 引用:
      counts: 数量骨架(新增/移除/更新/可见性)
      key:    高价值强 ID 引用列表(操作结果的直接证据),每条 {type,id,semantic,name}
              agent 拿到 id(如 el_595)可用 world_entity 查详图(位置/属性/邻居/区域),
              如 CAD 图纸上的 004# 圆孔——编号即一切属性的入口,无需猜。
    强信号口径:_DIGEST_HIGH_ROLES(弹窗/菜单/选项/组合框/输入框等几乎必是操作结果的角色),
    外壳(button/link/navigation)降权避免重型 SPA 重渲染"假新增"刷屏。
    批量信号例外:同批大量 remove(+update)是整页替换/导航的最强证据,不经过
    单条语义降权(实测:导航后 563 remove + 1437 update 曾全 medium,key 为空)。
    """
    counts = {"add": 0, "remove": 0, "update": 0, "visibility": 0}
    key = []  # 高价值强 ID 引用(操作结果的直接证据)
    for evt in events:
        etype = evt.get("type")
        if etype in counts:
            counts[etype] += 1
        if _event_importance(evt) == "high" and etype in ("add", "remove"):
            key.append({
                "type": etype,
                "id": evt.get("id"),
                "semantic": evt.get("semantic"),
                "name": evt.get("name"),
            })
    if counts["remove"] >= 100 and counts["update"] >= counts["remove"]:
        key.insert(0, {
            "type": "bulk",
            "semantic": "page-replacement",
            "counts": {"remove": counts["remove"], "update": counts["update"]},
            "note": "大量移除+更新(整页替换/导航或全量重绘)",
        })
    return {"counts": counts, "key": key[:10]}


def _t_world_changes(args):
    wid = args["world_id"]
    since = int(args.get("since", 0))
    data = _evaluate(wid, "(s) => agentWorld.changes(s)", since)
    events = data.get("events", [])
    # 逐条附世界号 + 重要性(不新增往返:内核事件已带 name/semantic)
    # world_id 即标签页 ID:AI 同时管理多个世界时,光看事件就知道属于哪一页,
    # 避免跨世界 el_595 混淆(不同世界的 el_N 各自独立编号)
    for evt in events:
        evt["world_id"] = wid
        evt["importance"] = _event_importance(evt)
    data["digest"] = _change_digest(events)
    return _ok(data)


def _t_world_state(args):
    """页面状态信道:只返回当前最新状态,不附加全量工具 status。"""
    wid = args["world_id"]
    # 原生网页动作也可能在 MCP 之外打开新标签页；读取状态时同步接管，
    # 但不额外等待，避免把状态查询变成隐藏延迟。
    try:
        _activate_new_page(wid, poll_ms=0)
    except Exception:
        pass
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }")
    except Exception:
        pass
    return _ok({
        "world_id": wid,
        "channel": "page-state",
        "state": _page_signal_snapshot(wid),
        "pages": _world_pages_summary(wid),
        "task": _task_public(wid),
    })


def _t_world_change_digest(args):
    """变化摘要信道:读取变化但不把原始事件列表发给智能体。"""
    wid = args["world_id"]
    since = int(args.get("since", 0))
    data = _evaluate(wid, "(s) => agentWorld.changes(s)", since)
    events = data.get("events", [])
    for evt in events:
        evt["world_id"] = wid
        evt["importance"] = _event_importance(evt)
    digest = _change_digest(events)
    importance_counts = {}
    semantic_counts = {}
    for evt in events:
        importance = evt.get("importance", "medium")
        importance_counts[importance] = importance_counts.get(importance, 0) + 1
        semantic = evt.get("semantic") or "unknown"
        semantic_counts[semantic] = semantic_counts.get(semantic, 0) + 1
    return _ok({
        "world_id": wid,
        "channel": "change-digest",
        "from": since,
        "to": data.get("to", since),
        "cursor_reset": data.get("to", since) < since,
        "changed": bool(events),
        "events_seen": len(events),
        "counts": digest.get("counts", {}),
        "importance_counts": importance_counts,
        "semantic_counts": semantic_counts,
        "key": digest.get("key", []),
        "raw_events_available_via": "world_changes",
    })


def _t_world_find(args):
    """默认协议:按条件定位构件(替代 world_entities/world_resolve 的日常用法)。

    q 提供时走弱 ID 解析(强 ID/名字/页面原生 id);否则按 role/text/name/interactive 过滤。
    只返回 matches[] 与 ambiguous,禁止在 find 里执行动作。
    """
    wid = args["world_id"]
    q = args.get("q")
    max_results = max(1, min(int(args.get("max_results", 20)), 100))
    filters = {k: v for k, v in args.items()
               if k in ("role", "tag", "text", "name", "fingerprint", "interactive", "in_viewport")
               and v is not None}
    if "in_viewport" in filters:
        filters["inViewport"] = filters.pop("in_viewport")
    if "max_results" in args:
        filters["maxResults"] = max_results

    try:
        entities = []
        if q:
            r = _evaluate_query_retry(wid, "(q) => agentWorld.query.resolve(q)", str(q)) or {}
            ids = []
            if r.get("id"):
                ids = [r["id"]]
            elif r.get("matches"):
                ids = list(r.get("matches"))[:max_results]
            for i in ids:
                ent = _evaluate_query_retry(wid, "(id) => agentWorld.query.getEntity(id)", i)
                if ent:
                    entities.append(ent)
            # 文本兜底:resolve 未命中时,按可见文本/名字做大小写不敏感子串匹配(与内核 findEntities 口径一致)
            if not entities:
                fallback = _evaluate_query_retry(wid, "(q) => agentWorld.query.findEntities({text: q})", str(q)) or []
                if not fallback:
                    fallback = _evaluate_query_retry(wid, "(q) => agentWorld.query.findEntities({name: q})", str(q)) or []
                entities = fallback
            # q 解析后仍可叠加过滤器(角色/文本/可交互),过滤候选
            if filters:
                entities = [e for e in entities if _entity_match(e, filters)]
            # 解析命中父容器后被过滤条件淘汰时，不能直接返回空；
            # 聚合站/商品页常同时存在 content→tab→span 多层语义，
            # 需要用 q 作为文本条件重新查找真正可交互的子元素。
            if not entities and filters:
                retry_filters = dict(filters)
                retry_filters.setdefault("text", str(q))
                entities = _evaluate_query_retry(
                    wid, "(f) => agentWorld.query.findEntities(f)", retry_filters
                ) or []
        else:
            entities = _evaluate_query_retry(wid, "(f) => agentWorld.query.findEntities(f)", filters) or []
    except Exception as exc:
        try:
            current_url = _world(wid)["page"].url[:300]
        except Exception:
            current_url = ""
        return _ok({"world_id": wid, "count": 0, "matches": [], "ambiguous": False,
                    "pending": True, "retry_after_ms": 500, "url": current_url,
                    "reason": "页面正在切换，网页运行时尚未就绪",
                    "error_type": type(exc).__name__})

    matches = [{
        "id": e.get("id"),
        "name": e.get("name"),
        "text": (e.get("text") or "")[:240],
        "tag": e.get("tag"),
        "semantic": e.get("semantic"),
        "fingerprint": e.get("fingerprint"),
        "bounds": e.get("bounds"),
        "interactive": e.get("interactive"),
        "in_viewport": e.get("inViewport"),
        "href": e.get("href"),
        # F2 来源标记:页面自由文本字段(name/text/aria-label/placeholder)默认 untrusted
        "sources": {
            "id": SOURCE_FACT,
            "fingerprint": SOURCE_FACT,
            "bounds": SOURCE_FACT,
            "semantic": SOURCE_FACT,
            "name": SOURCE_UNTRUSTED,
            "text": SOURCE_UNTRUSTED,
            "href": SOURCE_UNTRUSTED,
        },
    } for e in entities[:max_results] if e.get("id")]
    interactive_hits = [m for m in matches if m.get("interactive")]
    return _ok({
        "world_id": wid,
        "count": len(matches),
        "ambiguous": len(interactive_hits) > 1,
        "matches": matches,
    })


def _t_world_outcome(args):
    """默认协议:读最近一张统一后果卡(幂等,弱模型"我刚才到底怎样了"的唯一查询)。

    since 传入 evidence_seq 时,仅当存在更新动作的卡才返回;否则返回 none 卡。
    watch_id 为阶段 C(验尸官模式)预留,当前忽略。
    """
    wid = args["world_id"]
    since = int(args.get("since", 0))
    w = _world(wid)
    last = w.get("last_outcome_card")
    if last and last.get("evidence_seq", 0) > since:
        data = dict(last)
        data["task_state"] = _task_public(wid)
        return _ok(data)
    return _ok({
        "world_id": wid,
        "channel": "outcome",
        "page_outcome": "none",
        "situation": {"type": "none", "to_url": None},
        "confidence": "high",
        "why": "since 之后没有新动作" if since else "尚无动作;先 world_act 或 world_open",
        "target": None,
        "action": {"kind": "outcome", "via": "self"},
        "evidence_seq": int(w.get("evidence_seq", 0)),
        "changes_seq": {"before": 0, "after": 0},
        "world_epoch": int(w.get("epoch", 0)),
        "task_state": _task_public(wid),
    })


def _t_world_close(args):
    wid = args["world_id"]
    try:
        _activate_new_page(wid, poll_ms=0)
    except Exception:
        pass
    existing = _worlds.get(int(wid))
    if existing and bool(args.get("keep_session", False)):
        existing["last_activity"] = time.time()
        existing["idle_ttl_ms"] = max(0, int(args.get("idle_ttl_ms", existing.get("idle_ttl_ms", 600000))))
        _task_update(wid, status="idle", current_action=None)
        return _ok({"world_id": wid, "closed": False, "retained": True,
                    "task_state": _task_public(wid),
                    "session": {"reused": False, "reuse_type": "retained", "idle_ttl_ms": existing["idle_ttl_ms"]}})
    if existing:
        _task_update(wid, status="closed", current_action=None)
        task_state = _task_public(wid)
    else:
        task_state = None
    w = _worlds.pop(int(wid), None)
    if w:
        # CDP 连接:只断开,不关闭用户浏览器,也不导出 profile(会话属于用户日常浏览器)
        if w.get("cdp_url"):
            try:
                w["handle"].close()
            except Exception:
                pass
            return _ok({"world_id": wid, "closed": True, "cdp_disconnected": True, "task_state": task_state})
        try:
            # 导出会话状态(session cookie 也保留),供同 profile 重开时恢复登录态
            if w.get("profile") and w.get("context"):
                state_file = PROFILES_DIR / str(w["profile"]) / "storage_state.json"
                state = w["context"].storage_state()
                state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[world] storage state 保存失败: {e}")
        try:
            w["handle"].close()
        except Exception:
            pass
    return _ok({"world_id": wid, "closed": bool(w), "task_state": task_state})


def _t_world_list(args):
    _expire_idle_sessions()
    return _ok({"worlds": [{"world_id": k, "url": v["url"], "opened_at": v["opened_at"],
                             "last_activity": v.get("last_activity"),
                             "task": _task_public(k)} for k, v in _worlds.items()]})


# ── 入口 ─────────────────────────────────────────────────────
