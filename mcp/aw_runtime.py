# -*- coding: utf-8 -*-
"""运行时状态与 IO 原语层——自 mcp/server.py 拆出,行为不变。

分层约束:本层只负责「世界注册表状态 + Playwright IO + 任务上下文/路线记忆 +
结果包装(_ok/_result_payload)」;工具实现(_t_*)与业务判定(_outcome_card 等)在上层。
状态约定:_worlds 字典跨模块共享同一对象(只做项级读写,禁止整体重绑);
_next_world_id 因留守的 _t_world_open 使用 global 重绑而保留在 server.py。
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mcp.types as types
from playwright.sync_api import sync_playwright

try:
    from task_runtime import TraceStore
except ImportError:
    from mcp.task_runtime import TraceStore

from aw_core import (
    _page_node_identity,
    _same_origin,
)

# ── 世界注册表状态(与上层模块共享同一对象;_next_world_id 留守 server.py)──
_worlds = {}
_playwright = None

# Playwright 同步 API 强依赖 greenlet 协程上下文,必须在单一固定 OS 工作线程内运行


_pw_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright_worker")


_pending_actions = {}


_pending_actions_lock = threading.Lock()


def _cleanup_pending_actions(max_age_s=600):
    now = time.time()
    with _pending_actions_lock:
        expired = [k for k, v in _pending_actions.items()
                   if now - float(v.get("created_at", now)) > max_age_s]
        for key in expired:
            _pending_actions.pop(key, None)


sys.stdout.reconfigure(encoding="utf-8")


ALL_IN_ONE = Path(__file__).parent.parent / "extension" / "all-in-one.js"


ROUTE_MEMORY_DIR = Path(os.environ.get("AGENT_WORLD_MEMORY_DIR", str(Path(__file__).parent / "memory")))
ROUTE_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


ROUTE_MEMORY_FILE = ROUTE_MEMORY_DIR / "routes.jsonl"
# 历史路线只做本机被动记录，不跨用户共享；测试可通过环境变量指定隔离目录。
_route_memory_cache = None


_route_memory_lock = threading.RLock()


def _get_pw():
    global _playwright
    if _playwright is None:
        _playwright = sync_playwright().start()
    return _playwright


def _world(world_id):
    w = _worlds.get(int(world_id))
    if not w:
        raise ValueError(f"世界 {world_id} 不存在,先用 world_open 打开")
    return w


def _touch_world(wid):
    """记录最近活动时间，供热会话空闲过期判断。"""
    w = _world(wid)
    w["last_activity"] = time.time()
    return w


def _expire_idle_sessions():
    """懒清理空闲保留会话；只在服务器自己的 Playwright 线程中调用。"""
    now = time.time()
    for wid, world in list(_worlds.items()):
        if (world.get("task_context") or {}).get("status") != "idle":
            continue
        ttl = max(0, int(world.get("idle_ttl_ms", 600000))) / 1000
        if now - float(world.get("last_activity", now)) <= ttl:
            continue
        _worlds.pop(wid, None)
        try:
            world["handle"].close()
        except Exception:
            pass


def _find_reusable_world(url, task_id, profile, cdp_url, headful, idle_ttl_ms):
    """保守复用：同一 task_id、同一站点、空闲且页面仍健康。"""
    if not task_id:
        return None
    now = time.time()
    for wid, world in _worlds.items():
        ctx = world.get("task_context") or {}
        if ctx.get("task_id") != str(task_id):
            continue
        if world.get("profile") != profile or world.get("cdp_url") != cdp_url:
            continue
        if bool(world.get("headful", False)) != bool(headful):
            continue
        if now - float(world.get("last_activity", world.get("opened_at", now))) > max(0, int(idle_ttl_ms)) / 1000:
            continue
        if ctx.get("status") in ("executing", "waiting_user"):
            continue
        # 动作可能已把活动页切到 /go 或外部店铺；复用同一任务时，
        # 优先在该网页世界的所有页签中找回请求的入口页，而不是只看 active page。
        try:
            pages = [p for p in world.get("context").pages if not p.is_closed()]
        except Exception:
            pages = []
        if not pages:
            pages = [world.get("page"), world.get("main_page")]
        match = None
        for page in pages:
            try:
                if page is not None and _same_origin(page.url, url) and str(page.url).rstrip("/") == str(url).rstrip("/"):
                    match = page
                    break
            except Exception:
                continue
        if match is None:
            continue
        world["reuse_target_page"] = match
        return wid
    return None


def _new_task_context(task_id, world_id):
    return {
        "task_id": str(task_id),
        "world_id": int(world_id),
        "status": "creating",
        "page": {"url": "", "node": None, "epoch": 0, "scan_revision": 0},
        "current_action": None,
        "action_queue": [],
        "last_outcome": None,
        "route": {"mode": "mixed", "source": "none", "validated": False},
        "intervention": None,
        "revision": 0,
    }


def _task_public(wid):
    """返回任务状态摘要；不暴露填写内容。"""
    w = _world(wid)
    ctx = w.get("task_context") or {}
    out = dict(ctx)
    out["action_queue"] = [dict(x) for x in (ctx.get("action_queue") or [])[-20:]]
    return out


def _task_update(wid, **changes):
    w = _world(wid)
    ctx = w.setdefault("task_context", _new_task_context(f"task_{wid}", wid))
    for key, value in changes.items():
        if key == "page" and isinstance(value, dict):
            page = dict(ctx.get("page") or {})
            page.update(value)
            ctx["page"] = page
        else:
            ctx[key] = value
    ctx["revision"] = int(ctx.get("revision", 0)) + 1
    return ctx


def _task_begin_action(wid, name, args):
    action_id = str(args.get("_action_id") or f"act_{uuid.uuid4().hex[:12]}")
    action = {"action_id": action_id, "kind": name.replace("world_", "", 1),
              "target": args.get("id"), "started_at": int(time.time() * 1000)}
    ctx = _task_update(wid, status="executing", current_action=action, intervention=None)
    return action_id


def _task_finish_action(wid, name, args, result, error=None, before_signal=None):
    payload = _result_payload(result)
    outcome = payload.get("page_outcome")
    current = ((_world(wid).get("task_context") or {}).get("current_action") or {})
    action_id = str(args.get("_action_id") or current.get("action_id", ""))
    if error is not None:
        outcome = "errored"
    intervention = None
    if outcome == "challenged":
        intervention = {"required": True, "type": "human_challenge"}
    status = "waiting_user" if intervention else ("ready" if outcome else "idle")
    last = {"action_id": action_id, "kind": name.replace("world_", "", 1),
            "page_outcome": outcome, "evidence_seq": payload.get("evidence_seq"),
            "recorded_at": int(time.time() * 1000)}
    page = {}
    if payload.get("page", {}).get("after_url"):
        page["url"] = payload["page"]["after_url"]
    page["epoch"] = payload.get("world_epoch", (_world(wid).get("epoch", 0)))
    try:
        signal = _page_signal_snapshot(wid)
        node_id, _ = _page_node_identity(signal)
        page.update({"url": signal.get("url"), "node": node_id})
    except Exception:
        pass
    _task_update(wid, status=status, current_action=None, last_outcome=last,
                 intervention=intervention, page=page)
    # 直接动作的历史边记录；world_act steps 在每一步完成时单独记录，避免只留下聚合边。
    if not (name == "world_act" and args.get("steps")):
        _record_route_memory(wid, name, args, before_signal, result, error=error)


def _route_memory_load():
    """读取本机路线记录；坏行跳过，记录数量有上限，避免历史文件无限膨胀。"""
    global _route_memory_cache
    if _route_memory_cache is not None:
        return _route_memory_cache
    rows = []
    try:
        with _route_memory_lock:
            if ROUTE_MEMORY_FILE.exists():
                for line in ROUTE_MEMORY_FILE.read_text(encoding="utf-8").splitlines()[-2000:]:
                    try:
                        item = json.loads(line)
                        if isinstance(item, dict) and item.get("from_node"):
                            rows.append(item)
                    except Exception:
                        continue
    except Exception:
        rows = []
    _route_memory_cache = rows
    return rows


def _route_memory_append(row):
    """追加一条不含表单值的路线事实；失败不能阻断动作结果。"""
    global _route_memory_cache
    safe = dict(row)
    safe.pop("form_values", None)
    try:
        with _route_memory_lock:
            with ROUTE_MEMORY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
            rows = _route_memory_load()
            rows.append(safe)
            _route_memory_cache = rows[-2000:]
    except Exception:
        pass


def _record_route_memory(wid, action, args, before, result, error=None):
    """按组合页面节点记录动作边，供 world_guide 提供高置信度提示。"""
    if not before:
        return
    payload = _result_payload(result)
    outcome = "errored" if error is not None else payload.get("page_outcome")
    if not outcome:
        return
    try:
        after = _page_signal_snapshot(wid)
        from_node, _ = _page_node_identity(before)
        to_node, _ = _page_node_identity(after)
    except Exception:
        return
    target = payload.get("target") or {}
    target_fp = target.get("fingerprint") if isinstance(target, dict) else None
    if action == "world_batch_fill":
        target_key = "batch:" + ",".join(str(x.get("id")) for x in args.get("fields", []) if isinstance(x, dict))
    elif action == "world_navigate":
        target_key = str(args.get("url", ""))[:300]
    else:
        target_key = str(target.get("id") or args.get("id") or "")
    row = {
        "from_node": from_node,
        "to_node": to_node,
        "action_kind": (str(args.get("kind")) if action == "world_act" and args.get("kind") else str(action).replace("world_", "", 1)),
        "target_fingerprint": target_fp,
        "target_key": target_key,
        "page_outcome": outcome,
        "success": outcome == "progressed",
        "precondition": args.get("precondition") if isinstance(args.get("precondition"), dict) else None,
        "evidence_seq": payload.get("evidence_seq"),
        "recorded_at": int(time.time() * 1000),
    }
    _route_memory_append(row)


def _route_hint(wid, state, candidates):
    """从实时候选和本机历史构造提示；历史仅在两次成功且无冲突时升为 high。"""
    rows = _route_memory_load()
    try:
        node_id, _ = _page_node_identity(state)
    except Exception:
        node_id = None
    live = [c for c in candidates if c.get("fingerprint")]
    relevant = [r for r in rows if r.get("from_node") == node_id]
    by_fp = {}
    for row in relevant:
        fp = row.get("target_fingerprint")
        if not fp:
            continue
        action_kind = row.get("action_kind")
        # 兼容早期 world_act 记录的包装名 act；默认单步 world_act 的实际动作是 click。
        if action_kind == "act":
            action_kind = "click"
        if action_kind not in {"click", "fill", "press", "batch_fill"}:
            continue
        item = by_fp.setdefault(fp, {"success": 0, "total": 0, "to_nodes": set(), "action_kind": action_kind})
        item["total"] += 1
        if row.get("success"):
            item["success"] += 1
            item["to_nodes"].add(row.get("to_node"))
    history_steps = []
    for candidate in live:
        item = by_fp.get(candidate.get("fingerprint"))
        if not item:
            continue
        conflict = len(item["to_nodes"]) > 1
        if item["success"] >= 2 and not conflict:
            history_steps.append({
                "kind": item.get("action_kind") or "click",
                "id": candidate.get("id"),
                "target_fingerprint": candidate.get("fingerprint"),
                "precondition": {"url": state.get("url"), "fingerprint": candidate.get("fingerprint")},
                "success_count": item["success"],
            })
    stale = bool(relevant and not history_steps and live)
    if history_steps:
        return {"source": "history", "confidence": "high", "validated": True,
                "stale": False, "steps": history_steps[:4]}
    if candidates:
        return {"source": "live", "confidence": "medium", "validated": True,
                "stale": stale, "steps": [{"kind": "click", "id": candidates[0].get("id")}]}
    return {"source": "none", "confidence": "low", "validated": False,
            "stale": False, "steps": []}


def _task_enqueue_actions(wid, steps):
    queue = []
    for step in steps[:20]:
        queue.append({"action_id": str(step.get("_action_id") or f"act_{uuid.uuid4().hex[:12]}"),
                      "kind": step.get("kind"), "target": step.get("id"),
                      "status": "queued", "expected_outcome": step.get("expected_outcome"),
                      "precondition": step.get("precondition")})
    _task_update(wid, action_queue=queue)
    return queue


def _task_mark_queue(wid, index, status, **extra):
    w = _world(wid)
    ctx = w.get("task_context") or {}
    queue = list(ctx.get("action_queue") or [])
    if 0 <= index < len(queue):
        item = dict(queue[index])
        item.update(extra)
        item["status"] = status
        queue[index] = item
        _task_update(wid, action_queue=queue)


def _verify_action_precondition(wid, step):
    """执行队列中每一步前验证页面前置条件；失败即停止后续动作。"""
    pre = step.get("precondition") or {}
    if not isinstance(pre, dict):
        raise ValueError("动作前置条件必须是对象")
    if not pre:
        return
    signal = _page_signal_snapshot(wid)
    if pre.get("url") and signal.get("url") != str(pre["url"]):
        raise ValueError("前置条件失效:当前网址已变化")
    if pre.get("url_prefix") and not signal.get("url", "").startswith(str(pre["url_prefix"])):
        raise ValueError("前置条件失效:当前网址不匹配")
    if pre.get("no_dialog") and signal.get("dialogs"):
        raise ValueError("前置条件失效:存在未处理弹窗")
    fingerprint = pre.get("fingerprint") or pre.get("target_fingerprint")
    if fingerprint:
        hits = _evaluate(wid, "(fp) => agentWorld.query.findEntities({fingerprint: fp, maxResults: 2})", str(fingerprint)) or []
        if len(hits) != 1:
            raise ValueError("前置条件失效:目标稳定指纹不存在或不唯一")
    if pre.get("interactive") is not None and step.get("id"):
        target = _resolve_id(wid, step["id"])
        ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target) or {}
        if bool(ent.get("interactive")) != bool(pre["interactive"]):
            raise ValueError("前置条件失效:目标当前不可交互")


def _runtime_context(w):
    """返回轨迹和候选图共用的来源上下文；未知字段保持为空。"""
    return {
        key: w.get(key)
        for key in ("workflow_id", "site_version", "role", "permission_scope",
                    "site_adapter_id", "site_adapter_version")
        if w.get(key)
    }


def _wait_world_ready(page, timeout_ms=15000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            ok = page.evaluate("typeof window.agentWorld !== 'undefined'")
            if ok:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _start_progressive_scan(wid):
    """在浏览器页面内部安排分段扫描；不把同一个 Playwright 对象交给后台线程。"""
    try:
        return _evaluate(
            wid,
            """() => {
                const old = window.__agentWorldProgressiveScan;
                if (old && old.active) return old;
                const scan = {
                    active: true,
                    phase: 'action',
                    revision: 0,
                    fullScan: 'pending',
                    startedAt: Date.now(),
                };
                window.__agentWorldProgressiveScan = scan;
                const run = (phase, full) => {
                    try {
                        if (window.agentWorld && window.agentWorld._runtime) {
                            window.agentWorld._runtime.refreshStatus();
                        }
                    } catch (_) {}
                    scan.phase = phase;
                    scan.revision += 1;
                    if (full) scan.fullScan = 'ready';
                    scan.lastAt = Date.now();
                };
                // 先让当前视口可识别，再补充任务相关区域，最后补齐整页。
                [
                    [0, 'action', false],
                    [80, 'terrain', false],
                    [350, 'terrain', false],
                    [1000, 'stable', true],
                ].forEach(([delay, phase, full]) => setTimeout(() => run(phase, full), delay));
                return scan;
            }""",
        ) or {}
    except Exception:
        return {}


def _scan_state(wid):
    try:
        state = _evaluate(wid, "() => window.__agentWorldProgressiveScan || {}") or {}
    except Exception:
        state = {}
    try:
        revision = int(state.get("revision", 0))
        _task_update(wid, page={"scan_revision": revision})
    except Exception:
        pass
    return state


def _wait_progressive_phase(wid, wanted, timeout_ms):
    """仅在 terrain/stable 策略中等待指定阶段；action 策略不调用此函数。"""
    rank = {"action": 0, "terrain": 1, "stable": 2}
    deadline = time.time() + max(0, int(timeout_ms)) / 1000
    while time.time() < deadline:
        state = _scan_state(wid)
        if rank.get(str(state.get("phase")), 0) >= rank.get(wanted, 0):
            return state
        time.sleep(0.05)
    return _scan_state(wid)


def _known_page_tokens(wid):
    """返回动作前已知页面集合；新标签页不提前纳入，便于动作后发现。"""
    w = _world(wid)
    known = w.get("known_page_tokens") or set()
    if not known and w.get("page") is not None:
        known = {id(w["page"])}
    return set(known)


def _has_new_page(wid, before_tokens):
    """非阻塞检查动作基线之后是否已经出现新页。"""
    try:
        pages = _world(wid).get("context").pages
        return any((not p.is_closed()) and id(p) not in set(before_tokens or ()) for p in pages)
    except Exception:
        return False


def _activate_new_page(wid, before_tokens=None, poll_ms=0):
    """发现并激活动作产生的新标签页/弹出页，返回可写入反馈卡的小摘要。"""
    w = _world(wid)
    context = w.get("context")
    baseline = set(before_tokens) if before_tokens is not None else _known_page_tokens(wid)
    # window.open 的 page 事件与 Playwright action 返回不是严格同步；
    # 先做一次快照，只有调用方明确给出很短的 poll_ms 时才等待“新页出现”，
    # 普通无弹窗动作不承担隐藏固定等待。
    pages = []
    new_pages = []
    deadline = time.time() + max(0, int(poll_ms)) / 1000
    while True:
        try:
            pages = [p for p in context.pages if not p.is_closed()]
        except Exception:
            pages = []
        new_pages = [p for p in pages if id(p) not in baseline]
        if new_pages or time.time() >= deadline:
            break
        time.sleep(0.04)

    # 新页上的监听与注入只能在动作回调之外进行；在 context.on("page") 回调内
    # 调用同步 Playwright API 会阻塞事件派发，严重时表现为 world_act 超时。
    hooks = w.get("page_hooks") or {}
    observed = w.setdefault("observed_page_tokens", set())
    for candidate_page in pages:
        token = id(candidate_page)
        if token in observed:
            continue
        try:
            candidate_page.add_init_script(INJECT_JS)
            candidate_page.on("response", hooks["response"])
            candidate_page.on("console", hooks["console"])
            candidate_page.on("pageerror", hooks["pageerror"])
            # 新页刚出现时可能正处在导航/转链中。这里绝不能同步 evaluate：
            # Playwright 会等待新执行上下文，反而把“立即回执”卡住几十秒。
            # 先无条件记为 pending；真正需要读取页面时，由 _ensure_page_runtime
            # 在 world_wait/world_find 中做有限重试并补注入。
            w.setdefault("runtime_pending_tokens", set()).add(token)
            try:
                pending_url = candidate_page.url[:300]
            except Exception:
                pending_url = ""
            w["page_lifecycle"] = {"state": "loading", "runtime": "pending", "url": pending_url, "updated_at": time.time()}
            observed.add(token)
        except Exception:
            # 页面可能在转链中瞬间关闭；跟踪失败不能阻断原动作后果卡。
            pass
    # 若当前页被外部关闭，回退到仍打开的最后一个页面。
    active = w.get("page")
    try:
        active_closed = active is None or active.is_closed()
    except Exception:
        active_closed = True
    if new_pages:
        candidate = new_pages[-1]
        try:
            candidate.wait_for_load_state("domcontentloaded", timeout=1500)
        except Exception:
            pass
        w["page"] = candidate
        w["url"] = candidate.url
        w["page_lifecycle"] = {"state": "loading", "runtime": "pending", "url": candidate.url[:300], "updated_at": time.time()}
        active = candidate
    elif active_closed and pages:
        active = pages[-1]
        w["page"] = active
        w["url"] = active.url
    # 当前打开页成为后续动作的已知基线；新页信息只报告本次发现的页面。
    w["known_page_tokens"] = {id(p) for p in pages}
    info = []
    for p in new_pages[:8]:
        try:
            info.append({"page_id": f"page_{id(p):x}", "url": p.url[:300], "active": p is active})
        except Exception:
            pass
    return info


def _world_pages_summary(wid):
    """返回当前网页世界的页签摘要，便于确认转链页是否已接管。"""
    w = _world(wid)
    context = w.get("context")
    try:
        pages = [p for p in context.pages if not p.is_closed()]
    except Exception:
        pages = []
    active = w.get("page")
    out = []
    for page in pages:
        try:
            out.append({
                "page_id": f"page_{id(page):x}",
                "url": page.url[:300],
                "active": page is active,
            })
        except Exception:
            pass
    return out


def _ensure_page_runtime(page, attempts=3):
    """确保新接管页已注入 agentWorld；转链页仍在导航时做有限重试。"""
    for _ in range(max(1, int(attempts))):
        try:
            if page.is_closed():
                return False
        except Exception:
            return False
        try:
            if page.evaluate("typeof window.agentWorld !== 'undefined'"):
                return True
        except Exception:
            pass
        try:
            page.add_init_script(INJECT_JS)
        except Exception:
            pass
        try:
            page.evaluate(INJECT_JS)
        except Exception:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=500)
            except Exception:
                pass
            try:
                page.wait_for_timeout(80)
            except Exception:
                pass
    try:
        return bool(page.evaluate("typeof window.agentWorld !== 'undefined'"))
    except Exception:
        return False


def _evaluate(world_id, expr, arg=None, timeout=None):
    w = _world(world_id)
    page = w["page"]
    token = id(page)
    ready_tokens = w.setdefault("runtime_ready_tokens", set())
    if token not in ready_tokens:
        if _ensure_page_runtime(page):
            ready_tokens.add(token)
            w.setdefault("runtime_pending_tokens", set()).discard(token)
    # Playwright evaluate 不支持 timeout 关键字;用 set_default_timeout 控制
    # 导航等待上限(默认 30s,导航竞态下会阻塞动作反馈),用完还原。
    if timeout is not None:
        try:
            page.set_default_timeout(timeout)
        except Exception:
            pass
    try:
        return page.evaluate(expr, arg) if arg is not None else page.evaluate(expr)
    except Exception as first_error:
        # 页面刚完成一次跳转时，原运行时可能随文档销毁；只在这类竞态下补注入并重试。
        message = str(first_error)
        if "agentWorld" not in message and "Execution context" not in message:
            raise
        # 若转链同时产生了另一个标签页，先把活动页切到最新页面，再尝试注入。
        try:
            _activate_new_page(world_id, poll_ms=0)
            page = _world(world_id)["page"]
            token = id(page)
            ready_tokens = _world(world_id).setdefault("runtime_ready_tokens", set())
        except Exception:
            pass
        if not _ensure_page_runtime(page):
            raise
        ready_tokens.add(token)
        w.setdefault("runtime_pending_tokens", set()).discard(token)
        return page.evaluate(expr, arg) if arg is not None else page.evaluate(expr)
    finally:
        if timeout is not None:
            try:
                page.set_default_timeout(30000)
            except Exception:
                pass


def _evaluate_query_retry(world_id, expr, arg=None, attempts=3):
    """查询专用的有限重试。

    转链页可能在一次查询期间完成自动跳转，旧文档会抛出执行上下文错误。
    查询可以短暂重试；动作不使用本函数，避免把写操作变成隐式重复。
    """
    last_error = None
    for index in range(max(1, int(attempts))):
        try:
            return _evaluate(world_id, expr, arg)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            race = ("agentWorld" in message or "Execution context" in message
                    or "Target page, context or browser has been closed" in message
                    or "Cannot read properties of null" in message)
            if not race or index + 1 >= max(1, int(attempts)):
                raise
            time.sleep(0.25)
            try:
                _activate_new_page(world_id, poll_ms=0)
                page = _world(world_id).get("page")
                _ensure_page_runtime(page, attempts=2)
            except Exception:
                pass
    raise last_error


# ── 工具定义 ─────────────────────────────────────────────────
# 阶段 B 收口:对外默认协议 6 个词(弱模型只学这一条环)
#   world_open → world_guide → world_find → world_act → world_outcome → world_close
# 其余 19 个旧工具全部保留(兼容已接入客户端),描述加 [内部/调试] 前缀;
# AGENT_WORLD_LITE=1 时 list_tools 只暴露 6 个, call_tool 拒绝旧工具。


def _ok(data):
    return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


# ── 决策前提监视(环境维护 agent 决策前提)────────────────────────
# 机制来源:实验验证(premise_experiment 3/3 vs 0/3)。
#  - 表单值/勾选状态对 world_entities 不可见,且对 MutationObserver 静默 → 轮询快照
#  - Playwright 同步 API 强线程亲和 → 快检骑在每次工具调用上(不设后台线程)
#  - 通知骑在每次工具返回上(F2:不造新必调动词,agent 无需主动轮询)
#  - 双重校验防瞬时噪声;ack 协议防通知震荡(冷却期会误屏蔽真实失效,已排除)


def _resolve_id(world_id, q):
    """支持名字/强 ID,统一解析为强 ID"""
    r = _evaluate(world_id, "(q) => agentWorld.query.resolve(q)", q, timeout=8000)
    if r and r.get("id"):
        return r["id"]
    if r and r.get("matches"):
        raise ValueError(f"{q!r} 有 {len(r['matches'])} 个候选: {r['matches']},请用 findEntities 精确过滤")
    # 文本兜底:resolve 未命中时,按可见文本做大小写不敏感子串匹配(与内核 findEntities 口径一致)
    texts = _evaluate(world_id, "(q) => agentWorld.query.findEntities({text: q})", q, timeout=8000) or []
    if len(texts) == 1:
        return texts[0]["id"]
    if len(texts) > 1:
        raise ValueError(f"{q!r} 文本匹配到 {len(texts)} 个候选,请用 world_find 精确定位")
    raise ValueError(f"找不到构件: {q!r}")


def _result_payload(result):
    if isinstance(result, dict):
        return result
    for item in result or []:
        if getattr(item, "type", None) != "text":
            continue
        try:
            data = json.loads(item.text)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _page_signal_snapshot(wid, fast=False):
    """读取一份很小的页面整体状态,作为动作反馈的全局基线。

    这里不读取整页结构,只关注导航和覆盖层这类会改变任务路径的信号。
    fast=True:导航已开始(URL 已变),新文档状态由后续查询提供——立即返回轻量
    快照,不 evaluate 等待新文档(实测 GitHub 导航期间 evaluate 可阻塞 ~20s,
    拖垮动作反馈;URL 变化本身就是导航类强证据)。
    """
    w = _world(wid)
    page = w.get("page")
    if fast:
        try:
            current_url = page.url[:300]
        except Exception:
            current_url = ""
        return {
            "url": current_url,
            "title": "",
            "state": "loading",
            "changes_seq": 0,
            "dialogs": [],
            "menus": [],
            "_net_err_cursor": len(w.get("network_errors") or []),
            "_console_err_cursor": len(w.get("console_errors") or []),
            "scan_revision": int((w.get("task_context") or {}).get("page", {}).get("scan_revision", 0)),
        }
    token = id(page) if page is not None else None
    pending = w.setdefault("runtime_pending_tokens", set())
    if token in pending:
        # 新页刚出现时先返回可立即获得的事实；不要为了动作回执再次
        # evaluate 正在导航的页面。后续 world_wait/world_find 会负责完整注入。
        try:
            current_url = page.url[:300]
        except Exception:
            current_url = ""
        # title() 需要读取新文档执行上下文，转链期间可能等待导航；待处理
        # 状态只返回 URL 等无需脚本的事实，标题等运行时信息留给后续查询。
        current_title = ""
        # 转链页可能已经从 /go 继续跳到外部店铺；即使运行时尚未注入，
        # 任务状态中的页面 URL 也必须跟随真实活动页，避免 state 与 task 分裂。
        try:
            _task_update(wid, page={"url": current_url})
            lifecycle = w.get("page_lifecycle") or {}
            lifecycle.update({"state": "loading", "runtime": "pending", "url": current_url, "updated_at": time.time()})
            w["page_lifecycle"] = lifecycle
        except Exception:
            pass
        return {
            "url": current_url,
            "title": current_title,
            "state": "loading",
            "changes_seq": 0,
            "dialogs": [],
            "menus": [],
            "_net_err_cursor": len(w.get("network_errors") or []),
            "_console_err_cursor": len(w.get("console_errors") or []),
            "scan_revision": int((w.get("task_context") or {}).get("page", {}).get("scan_revision", 0)),
        }
    try:
        core = _evaluate(wid, "() => agentWorld.query.getStatus()", timeout=4000) or {}
    except Exception:
        core = {}
    try:
        overlays = _evaluate(
            wid,
            """() => {
                const pick = (role) => agentWorld.query.findEntities({
                    role, inViewport: true, maxResults: 8
                }).map(e => ({ id: e.id, name: e.name, text: e.text, role }));
                return {
                    dialogs: pick('dialog').concat(pick('alertdialog')),
                    menus: pick('menu')
                };
            }""",
            timeout=4000,
        ) or {}
    except Exception:
        overlays = {}
    try:
        form_fields = _evaluate(
            wid,
            """() => [...document.querySelectorAll('input, textarea, [contenteditable=\"true\"]')]
                .map((node, index) => {
                    const el = agentWorld._runtime.world.elements.get(node);
                    const value = node.value !== undefined ? String(node.value || '') : String(node.textContent || '');
                    return {
                        id: el ? el.id : (node.id || ''),
                        name: node.getAttribute('name') || '',
                        type: node.getAttribute('type') || node.tagName.toLowerCase(),
                        role: node.getAttribute('role') || '',
                        placeholder: node.getAttribute('placeholder') || '',
                        filled: value.length > 0
                    };
                }).slice(0, 30)""",
        ) or []
    except Exception:
        form_fields = []
    probes = {}
    for spec in (w.get("site_adapter") or {}).get("state_probes", []) or []:
        if not isinstance(spec, dict):
            continue
        probe_id = spec.get("id")
        query = dict(spec.get("query") or {})
        if not probe_id or not query:
            continue
        if "in_viewport" in query:
            query["inViewport"] = query.pop("in_viewport")
        try:
            matches = _evaluate(
                wid,
                "(f) => agentWorld.query.findEntities(f)",
                query,
            ) or []
            probes[str(probe_id)[:120]] = (
                len(matches) if spec.get("mode") == "count" else bool(matches)
            )
        except Exception:
            probes[str(probe_id)[:120]] = False
    try:
        title = w["page"].title()[:200]
    except Exception:
        title = ""
    page_state = core.get("page", {}) or {}
    world_state = core.get("world", {}) or {}
    # 静默失败监听游标:记录动作前后错误列表长度,用于差分
    net_errors = w.get("network_errors") or []
    console_errors = w.get("console_errors") or []
    try:
        scan_state = _evaluate(wid, "() => window.__agentWorldProgressiveScan || {}", timeout=4000) or {}
        if not scan_state.get("active"):
            # 导航后 init script 可能重建页面世界，重新挂上渐进扫描调度。
            _start_progressive_scan(wid)
            scan_state = _evaluate(wid, "() => window.__agentWorldProgressiveScan || {}", timeout=4000) or {}
        scan_revision = int(scan_state.get("revision", 0))
        _task_update(wid, page={"scan_revision": scan_revision})
    except Exception:
        scan_revision = int((w.get("task_context") or {}).get("page", {}).get("scan_revision", 0))
    return {
        "url": w["page"].url[:300],
        "title": title,
        "state": page_state.get("state", "unknown"),
        "changes_seq": world_state.get("changesSeq", 0),
        "dialogs": overlays.get("dialogs", []) or core.get("dialogs", []) or [],
        "menus": overlays.get("menus", []) or [],
        "form_fields": form_fields,
        "probes": probes,
        "_net_err_cursor": len(net_errors),
        "_console_err_cursor": len(console_errors),
        "scan_revision": scan_revision,
    }


SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
PROFILES_DIR = Path(__file__).parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)


def _safe_profile_dir(profile):
    """外部 profile 名只能映射到 PROFILES_DIR 的直接子目录。

    回归 #14:profile='../profile-path-escape-probe' 曾经 PROFILES_DIR / str(profile)
    逃逸到仓库其他目录(读写两端均无约束)。只允许简单名称
    [A-Za-z0-9_-],拒绝路径分隔符、.. 、绝对路径与空名。
    """
    name = str(profile or "").strip()
    if not name or name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(
            f"非法 profile 名: {profile!r} (只允许字母/数字/连字符/下划线,不得包含路径)"
        )
    return PROFILES_DIR / name


# P0-2 视觉阈值:区域前后帧 RMS 差异超过此值判 visual-effected(5.0,校准见 docs/archive/视觉阈值校准报告.md)
VISUAL_RMS_THRESHOLD = 5.0

if not ALL_IN_ONE.exists():
    raise SystemExit(f"all-in-one.js 不存在: {ALL_IN_ONE}")

with open(ALL_IN_ONE, "r", encoding="utf-8") as f:
    INJECT_JS = f.read()
