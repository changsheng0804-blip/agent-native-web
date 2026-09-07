# -*- coding: utf-8 -*-
"""
Agent World MCP Server
======================
把 agent-runtime-extension 的"原生网页世界"以 MCP 工具暴露给任何 AI agent。
心智模型:CAD 图纸 + 网页视频。

工具:
  world_open     打开一个网页世界(并行多开)
  world_entities 构件清单(按角色/文本/名字/交互过滤)
  world_entity   构件详情(编号/名字/坐标/邻居/区域)
  world_layers   图层视图(结构/语义/空间/交互)
  world_map      页面结构导览(地图):语义容器分区 + 各区可交互入口
  world_resolve  弱 ID 解析(名字/强 ID/页面原生 id)
  world_changes  变更流(增量续读,游标)
  world_state    页面状态信道(读取最新整体状态)
  world_business_state 业务状态信道(由显式规则投影)
  world_operation_check 业务操作前置检查(只检查不执行)
  world_task_plan 任务路径规划(从运行时图寻找可复用路径,只规划不执行)
  world_graph_replay_check 回放核对(验证实际轨迹是否符合指定图边)
  world_change_digest 变化摘要信道(读取压缩后的变化)
  world_evidence 操作证据信道(读取动作前后证据)
  world_trace    任务轨迹信道(读取脱敏轨迹)
  world_graph    候选任务运行时图(从轨迹即时生成)
  world_trace_archive 读取已归档任务轨迹
  world_graph_archive 由已归档轨迹生成候选图
  world_graph_assess 评估图的回放与生命周期状态
  world_graph_bundle 合并多个任务实例进行跨会话评估
  world_guide   结合三条信道生成任务导览
  world_click    编号驱动点击 + 页面整体反馈
  world_fill     编号驱动填表
  world_wait     等待构件出现/消失
  world_screenshot 局部/整页截图(视觉兜底)
  world_close    关闭世界
  world_list     列出已打开的世界

运行:python server.py  (stdio 模式,由 MCP 客户端拉起)
"""
import asyncio
import base64
import collections
import hashlib
import json
import math
import os
import re
import sys
import time
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image, ImageChops, ImageDraw, ImageStat

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from playwright.sync_api import sync_playwright

try:
    from business_runtime import (
        attach_business_runtime,
        check_operation,
        normalize_operation_contracts,
        normalize_site_adapter,
        normalize_state_rules,
        project_business_state,
    )
except ImportError:
    from mcp.business_runtime import (
        attach_business_runtime,
        check_operation,
        normalize_operation_contracts,
        normalize_site_adapter,
        normalize_state_rules,
        project_business_state,
    )

try:
    from site_adapter import compare_site_adapters, load_site_adapter_file
except ImportError:
    from mcp.site_adapter import compare_site_adapters, load_site_adapter_file

try:
    from task_runtime import (
        TraceStore,
        build_graph,
        build_trace_entry,
        new_id,
        normalize_page_state,
        persistence_enabled,
        plan_graph,
        state_key,
        validate_replay_step,
    )
except ImportError:  # 允许从仓库根目录以模块方式加载
    from mcp.task_runtime import (
        TraceStore,
        build_graph,
        build_trace_entry,
        new_id,
        normalize_page_state,
        persistence_enabled,
        plan_graph,
        state_key,
        validate_replay_step,
    )

try:
    from aw_tool_schemas import build_tool_definitions
except ImportError:
    from mcp.aw_tool_schemas import build_tool_definitions

try:
    from aw_core import (  # noqa: F401
    CANONICAL_TOOLS,
    CANONICAL_ORDER,
    ACTION_NAMES,
    TRACKED_ACTION_NAMES,
    AUTH_COOKIE_HINTS,
    ASSUMPTION_INTERVAL_S,
    ASSUMPTION_RECHECK_S,
    TIMELINE_MAX,
    ACTION_EVIDENCE_PRE_S,
    _IMPORTANT_ROLES,
    _DIGEST_HIGH_ROLES,
    _MEDIUM_ROLES,
    _ROLE_LABEL,
    SOURCE_FACT,
    SOURCE_EVIDENCE,
    SOURCE_INFERENCE,
    SOURCE_UNTRUSTED,
    CARD_SOURCE_RULES,
    STYLE_DIFF_PROPS,
    STYLE_SNAPSHOT_MAX,
    _lite_mode,
    _same_origin,
    _page_node_identity,
    _evidence_norm_url,
    _signal_items,
    _signal_delta,
    _entity_match,
    _anomaly_from_counts,
    _target_state_flip,
    _event_importance,
    _sources_for_card,
    _guide_terms,
    _build_click_effect,
    )
except ImportError:
    from mcp.aw_core import (  # noqa: F401
    CANONICAL_TOOLS,
    CANONICAL_ORDER,
    ACTION_NAMES,
    TRACKED_ACTION_NAMES,
    AUTH_COOKIE_HINTS,
    ASSUMPTION_INTERVAL_S,
    ASSUMPTION_RECHECK_S,
    TIMELINE_MAX,
    ACTION_EVIDENCE_PRE_S,
    _IMPORTANT_ROLES,
    _DIGEST_HIGH_ROLES,
    _MEDIUM_ROLES,
    _ROLE_LABEL,
    SOURCE_FACT,
    SOURCE_EVIDENCE,
    SOURCE_INFERENCE,
    SOURCE_UNTRUSTED,
    CARD_SOURCE_RULES,
    STYLE_DIFF_PROPS,
    STYLE_SNAPSHOT_MAX,
    _lite_mode,
    _same_origin,
    _page_node_identity,
    _evidence_norm_url,
    _signal_items,
    _signal_delta,
    _entity_match,
    _anomaly_from_counts,
    _target_state_flip,
    _event_importance,
    _sources_for_card,
    _guide_terms,
    _build_click_effect,
    )

# Playwright 同步 API 强依赖 greenlet 协程上下文，必须在单一固定 OS 工作线程内运行，杜绝多线程竞争切换
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

try:
    from aw_runtime import (  # noqa: F401
    ALL_IN_ONE,
    INJECT_JS,
    ROUTE_MEMORY_DIR,
    ROUTE_MEMORY_FILE,
    _activate_new_page,
    _cleanup_pending_actions,
    _ensure_page_runtime,
    _evaluate,
    _evaluate_query_retry,
    _expire_idle_sessions,
    _find_reusable_world,
    _get_pw,
    _has_new_page,
    _known_page_tokens,
    _new_task_context,
    _ok,
    _page_signal_snapshot,
    _pending_actions,
    _pending_actions_lock,
    _playwright,
    _pw_executor,
    _record_route_memory,
    _resolve_id,
    _result_payload,
    _route_hint,
    _route_memory_append,
    _route_memory_cache,
    _route_memory_load,
    _route_memory_lock,
    _runtime_context,
    _scan_state,
    _start_progressive_scan,
    _task_begin_action,
    _task_enqueue_actions,
    _task_finish_action,
    _task_mark_queue,
    _task_public,
    _task_update,
    _touch_world,
    _verify_action_precondition,
    _wait_progressive_phase,
    _wait_world_ready,
    _world,
    _world_pages_summary,
    _worlds,
    )
except ImportError:
    from mcp.aw_runtime import (  # noqa: F401
    ALL_IN_ONE,
    INJECT_JS,
    ROUTE_MEMORY_DIR,
    ROUTE_MEMORY_FILE,
    _activate_new_page,
    _cleanup_pending_actions,
    _ensure_page_runtime,
    _evaluate,
    _evaluate_query_retry,
    _expire_idle_sessions,
    _find_reusable_world,
    _get_pw,
    _has_new_page,
    _known_page_tokens,
    _new_task_context,
    _ok,
    _page_signal_snapshot,
    _pending_actions,
    _pending_actions_lock,
    _playwright,
    _pw_executor,
    _record_route_memory,
    _resolve_id,
    _result_payload,
    _route_hint,
    _route_memory_append,
    _route_memory_cache,
    _route_memory_load,
    _route_memory_lock,
    _runtime_context,
    _scan_state,
    _start_progressive_scan,
    _task_begin_action,
    _task_enqueue_actions,
    _task_finish_action,
    _task_mark_queue,
    _task_public,
    _task_update,
    _touch_world,
    _verify_action_precondition,
    _wait_progressive_phase,
    _wait_world_ready,
    _world,
    _world_pages_summary,
    _worlds,
    )
# P0-2 视觉阈值:区域前后帧 RMS 差异超过此值判 visual-effected。
# 校准 v1(docs/视觉阈值校准报告.md):真静态 0.0 / 邻区动画渗入 ~2.1-2.3 /
# canvas 重绘 ~24.2 / 整块变色 ~33.3,取 5.0(噪声上 2.2x,最弱正例下 1/5)。
# 改动此值必须同步更新 test_visual_evidence.py 与 test_visual_calib.py 的断言。
VISUAL_RMS_THRESHOLD = 5.0
PROFILES_DIR = Path(__file__).parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)
server = Server("agent-world")

# ── 世界注册表 ────────────────────────────────────────────────
# world_id -> {"browser", "context", "page", "url", "opened_at"}
_next_world_id = 1
_trace_store = TraceStore()


@server.list_tools()
async def list_tools():
    tools = build_tool_definitions()
    # 阶段 B 收口:规范 6 词置前(保持协议顺序),旧工具描述加 [内部/调试] 前缀;LITE 模式只暴露 6 词
    if _lite_mode():
        tools = [t for t in tools if t.name in CANONICAL_TOOLS]
    else:
        tools = sorted(tools, key=lambda t: (CANONICAL_ORDER.index(t.name) if t.name in CANONICAL_TOOLS else 99, t.name))
        tools = [
            types.Tool(name=t.name,
                       description=("[内部/调试] " + t.description) if t.name not in CANONICAL_TOOLS else t.description,
                       inputSchema=t.inputSchema,
                       outputSchema=t.outputSchema)
            for t in tools
        ]
    return tools


# ── 工具实现 ─────────────────────────────────────────────────
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    arguments = arguments or {}
    try:
        _cleanup_pending_actions()
        # receipt 查询不再排入 Playwright 队列，避免长动作未完成时连查询也被阻塞。
        if name == "world_outcome" and arguments.get("action_id"):
            action_id = str(arguments.get("action_id"))
            with _pending_actions_lock:
                pending = _pending_actions.get(action_id)
            if pending is None:
                return _ok({"channel": "outcome", "page_outcome": "errored",
                            "action_id": action_id, "confidence": "high", "why": "动作编号不存在或已过期"})
            future = pending["future"]
            wait_ms = max(0, min(int(arguments.get("wait_ms", 0)), 60000))
            if not future.done() and wait_ms:
                try:
                    await asyncio.wait_for(asyncio.shield(future), timeout=wait_ms / 1000)
                except asyncio.TimeoutError:
                    pass
            if not future.done():
                return _ok({"world_id": pending.get("world_id"), "channel": "outcome",
                            "page_outcome": "pending", "action_id": action_id,
                            "confidence": "high", "why": "动作已发出，正在等待页面结果"})
            try:
                result = future.result()
                if result and getattr(result[0], "type", None) == "text":
                    try:
                        payload = json.loads(result[0].text)
                        payload["action_id"] = action_id
                        result = [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))] + list(result[1:])
                    except Exception:
                        pass
                return result
            except Exception as e:
                return _ok({"world_id": pending.get("world_id"), "channel": "outcome",
                            "page_outcome": "errored", "action_id": action_id,
                            "confidence": "high", "why": f"动作执行异常: {e}"})

        wait_policy = str(arguments.get("wait_policy") or "confirmed").strip().lower()
        if wait_policy == "receipt" and (name in ACTION_NAMES or name == "world_act"):
            world_id = arguments.get("world_id")
            if world_id is None:
                return _ok({"channel": "outcome", "page_outcome": "errored",
                            "confidence": "high", "why": "receipt 模式必须提供 world_id"})
            action_id = uuid.uuid4().hex
            action_args = dict(arguments)
            action_args.pop("wait_policy", None)
            action_args["_action_id"] = action_id
            future = asyncio.get_event_loop().run_in_executor(_pw_executor, _impl_with_status, name, action_args)
            with _pending_actions_lock:
                _pending_actions[action_id] = {"future": future, "world_id": int(world_id), "created_at": time.time()}
            return _ok({"world_id": int(world_id), "channel": "outcome", "page_outcome": "pending",
                        "action_id": action_id, "accepted": True, "confidence": "high",
                        "why": "动作已排入同一网页世界的有序执行队列"})
        # 全部在专用单一 executor 线程执行(Playwright 同步 API 强线程亲和)
        return await asyncio.get_event_loop().run_in_executor(_pw_executor, _impl_with_status, name, arguments)
    except Exception as e:
        traceback.print_exc()
        return [types.TextContent(type="text", text=f"错误: {e}")]


# 动作类工具:统一走 before_signal + 证据记录 + 统一后果卡(阶段 A)
def _impl_with_status(name, args):
    if _lite_mode() and name not in CANONICAL_TOOLS:
        raise ValueError(f"AGENT_WORLD_LITE 模式只开放 6 个默认工具({sorted(CANONICAL_TOOLS)});{name} 是内部/调试工具,请勿在 LITE 会话调用")
    wid = args.get("world_id")
    t_action = None
    if wid is not None and int(wid) in _worlds:
        try:
            _touch_world(int(wid))
        except Exception:
            pass
        # 前提快检骑在每次工具调用上(Playwright 线程亲和;失效→挂 pending_notices)
        try:
            _assumption_check(int(wid))
        except Exception:
            pass
    if wid is not None and name in TRACKED_ACTION_NAMES:
        t_action = time.time()  # L3 动作证据窗口起点(动作前 0.5s 起算)
    before_signal = None
    if name in TRACKED_ACTION_NAMES and wid is not None:
        try:
            args["_before_page_tokens"] = list(_known_page_tokens(int(wid)))
        except Exception:
            pass
        try:
            before_signal = _page_signal_snapshot(int(wid))
        except Exception:
            before_signal = None
        try:
            _task_begin_action(int(wid), name, args)
        except Exception:
            pass
        # 统一时间线:动作开始入账 + 合并动作前的 DOM 变更
        try:
            _tl_merge_dom(int(wid))
            _tl_action(int(wid), "start", name, args)
        except Exception:
            pass
    _ws_t0 = None
    try:
        result = _impl(name, args, before_signal)
    except Exception as e:        # 动作异常路径:返回统一后果卡 page_outcome=errored(结构化返回,不吞错误)
        if name in TRACKED_ACTION_NAMES and wid is not None:
            traceback.print_exc()
            try:
                _task_finish_action(int(wid), name, args, None, error=e, before_signal=before_signal)
                return _inject_status(_errored_card(int(wid), name, args, before_signal, e), wid)
            except Exception:
                return _errored_card(int(wid), name, args, before_signal, e)
        raise
    if name in ACTION_NAMES and wid is not None and before_signal is not None:
        try:
            _record_action_evidence(int(wid), name, args, before_signal, result)
        except Exception:
            # 证据记录不能阻断原有动作返回。
            pass
    if name in TRACKED_ACTION_NAMES and wid is not None:
        try:
            _task_finish_action(int(wid), name, args, result, before_signal=before_signal)
        except Exception:
            pass
        # 统一时间线:动作结束入账 + 合并动作后的 DOM 变更(动作窗口闭合)
        try:
            _tl_merge_dom(int(wid))
            _tl_action(int(wid), "end", name, args, result)
        except Exception:
            pass
    # 阶段 B:动作出口的后果卡缓存,供 world_outcome 幂等读取(world_act 内部已记录证据,这里只缓存卡)
    if name in ACTION_NAMES or name == "world_act":
        try:
            payload = _result_payload(result)
            if payload and payload.get("channel") == "outcome":
                _world(int(wid))["last_outcome_card"] = payload
        except Exception:
            pass
    # 独立信道工具(world_state/digest/evidence/guide)自带信道结构,不再附加大 status
    if name in {"world_state", "world_business_state", "world_operation_check", "world_task_plan", "world_graph_replay_check", "world_adapter_compare", "world_change_digest", "world_evidence", "world_trace", "world_graph", "world_trace_archive", "world_graph_archive", "world_graph_assess", "world_graph_bundle", "world_guide"}:
        return result
    # 瘦身演进:默认协议工具(world_act, world_find, world_outcome)默认轻量 status;
    # 失败/存疑态(unchanged/uncertain/challenged/errored)或显式 verbose=true 时自动全量深诊断;
    # 传统动作工具(world_click/fill/batch_fill等)保持既有契约兼容(含 forms 回显)
    light = name in ("world_act", "world_find", "world_outcome")
    if light:
        try:
            payload = _result_payload(result)
            po = (payload or {}).get("page_outcome")
            if args.get("verbose") or po in ("unchanged", "uncertain", "challenged", "errored"):
                light = False
        except Exception:
            light = False
    return _inject_action_evidence(_inject_notices(_inject_status(result, wid, light=light), wid), wid, t_action)


# ── 网页状态卡(仪表盘)────────────────────────────────────────
def _auth_status(wid):
    """登录态检测:双信号交叉(cookie 为主,DOM 特征辅助)"""
    w = _world(wid)
    try:
        cookies = w["context"].cookies()
    except Exception:
        cookies = []
    hits = []
    for c in cookies:
        hay = (c.get("name", "") + " " + c.get("domain", "")).lower()
        if any(h in hay for h in AUTH_COOKIE_HINTS):
            hits.append(c)
    if hits:
        domains = sorted({c["domain"] for c in hits})[:3]
        return {"loggedIn": True, "via": "cookie:" + ",".join(domains)}
    # DOM 特征:登录入口存在与否(辅助信号)
    try:
        login_btns = _evaluate(
            wid,
            "() => agentWorld.query.findEntities({ name: '登录' }).length + agentWorld.query.findEntities({ name: 'login' }).length",
        )
        if login_btns and login_btns > 0:
            return {"loggedIn": False, "via": "dom:login-entry-present"}
    except Exception:
        pass
    return {"loggedIn": False, "via": "no-signal"}


def _status_light(wid):
    """轻量状态卡:URL/稳定态/登录态/弹窗摘要 + 变化高亮。

    Diff-First 载荷(Phase 2):默认协议工具(world_act/find/outcome)默认注入轻量卡,
    不读 frames/forms/world 明细;verbose=true 或失败/存疑态(unchanged/uncertain/
    challenged/errored)时自动升级为全量 _status 深诊断。
    """
    w = _world(wid)
    page = w.get("page")
    if page is not None and id(page) in (w.get("runtime_pending_tokens") or set()):
        # 动作刚打开的新页仍在导航时，状态卡也必须是非阻塞的；不要调用
        # _auth_status/_evaluate 读取尚未建立的执行上下文。
        try:
            current_url = page.url[:120]
        except Exception:
            current_url = ""
        cur = {"light": True, "url": current_url, "state": "loading",
               "auth": {"loggedIn": False, "via": "pending-page"},
               "dialogs": [], "task": _task_public(wid), "changed": {}}
        last = w.get("last_status_light")
        w["last_status_light"] = cur
        if last:
            if last.get("url") != cur.get("url"):
                cur["changed"]["url"] = True
            if last.get("state") != cur.get("state"):
                cur["changed"]["state"] = True
        return cur
    try:
        core = _evaluate(wid, "() => agentWorld.query.getStatus()") or {}
    except Exception:
        core = {}
    cur = {
        "light": True,
        "url": w["page"].url[:120],
        "state": (core.get("page") or {}).get("state", "unknown"),
        "auth": _auth_status(wid),
        "dialogs": core.get("dialogs", []) or [],
        "task": _task_public(wid),
        "changed": {},
    }
    last = w.get("last_status_light")
    w["last_status_light"] = cur
    if last:
        if last.get("url") != cur.get("url"):
            cur["changed"]["url"] = True
        if last.get("state") != cur.get("state"):
            cur["changed"]["state"] = True
        if len(last.get("dialogs") or []) != len(cur.get("dialogs") or []):
            cur["changed"]["dialogs"] = True
    return cur


def _status(wid):
    """聚合世界状态卡(内核状态 + 登录态 + frame 感知 + 环境异常),附带变化高亮"""
    w = _world(wid)
    page = w.get("page")
    if page is not None and id(page) in (w.get("runtime_pending_tokens") or set()):
        try:
            current_url = page.url[:120]
        except Exception:
            current_url = ""
        cur = {"auth": {"loggedIn": False, "via": "pending-page"}, "dialogs": [],
               "page": {"url": current_url, "state": "loading", "scrollY": 0,
                        "totalHeight": 0, "domTotal": 0}, "frames": [], "forms": [],
               "world": {}, "task": _task_public(wid), "changed": {}}
        last = w.get("last_status")
        w["last_status"] = cur
        if last:
            if last.get("page", {}).get("url") != current_url:
                cur["changed"]["page"] = True
        return cur
    try:
        core = _evaluate(wid, "() => agentWorld.query.getStatus()")
    except Exception:
        core = {"dialogs": [], "page": {}, "forms": [], "world": {}}
    page = w["page"]
    # frame 感知:逐层报告(每 frame 独立世界)
    frames = []
    for f in page.frames:
        try:
            if not f.url or f.url.startswith("about:"):
                continue
            fcnt = f.evaluate("document.querySelectorAll('*').length")
            # 可见元素计数采用"原生网页世界 scanner 同口径"(排除装饰标签/小元素),
            # 避免重型 SPA 的合法 DOM 膨胀被误判为 anomaly(实战: Booking.com 误报)
            fvisible = f.evaluate(
                "[...document.querySelectorAll('*')].filter(e => { const t = e.tagName.toLowerCase(); if (['br','hr','script','style','link','meta','noscript','svg','path','g','defs','use'].includes(t)) return false; const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) !== 0 && r.width > 3 && r.height > 3; }).length"
            )
            fready = f.evaluate("typeof window.agentWorld !== 'undefined'")
            frames.append({"url": f.url[:100], "elements": fcnt, "visible": fvisible, "ready": bool(fready)})
        except Exception:
            pass
    # 环境异常检测:稳定后,原生网页世界 vs 可见 DOM(阈值 35%,排除隐藏/装饰元素)
    visible_dom = frames[0].get("visible", 0) if frames else 0
    world_count = core.get("world", {}).get("elements", 0)
    anomaly = _anomaly_from_counts(visible_dom, world_count)
    try:
        page_state = core.get("page", {}).get("state", "unknown")
    except Exception:
        page_state = "unknown"
    cur = {
        "auth": _auth_status(wid),
        "dialogs": core.get("dialogs", []),
        "page": {
            "url": page.url[:120],
            "state": "anomaly" if anomaly else page_state,
            "scrollY": core.get("page", {}).get("scrollY", 0),
            "totalHeight": core.get("page", {}).get("totalHeight", 0),
            "domTotal": visible_dom,
        },
        "frames": frames,
        "forms": core.get("forms", []),
        "world": core.get("world", {}),
        "task": _task_public(wid),
    }
    last = w.get("last_status")
    w["last_status"] = cur
    changed = {}
    if last:
        if last["auth"]["loggedIn"] != cur["auth"]["loggedIn"]:
            changed["auth"] = True
        if len(last["dialogs"]) != len(cur["dialogs"]):
            changed["dialogs"] = True
        if last["page"].get("state") != cur["page"].get("state"):
            changed["page"] = True
        if last["page"].get("url") != cur["page"].get("url"):
            changed["page"] = True
        if len(last["frames"]) != len(cur["frames"]):
            changed["frames"] = True
        if len(last["forms"]) != len(cur["forms"]):
            changed["forms"] = True
    cur["changed"] = changed
    return cur


def _inject_status(result, wid, light=False):
    """给工具返回 JSON 注入状态卡。

    light=True 时注入轻量卡(URL/稳定态/登录态/弹窗摘要,不读 frames/forms/world 明细)。
    Phase 2 Diff-First 载荷:协议 6 词工具默认 light;verbose=true 或失败/存疑态自动全量。
    """
    if wid is None:
        # world_open 的返回里包含新建的 world_id
        for item in result:
            if item.type == "text":
                try:
                    data = json.loads(item.text)
                    if isinstance(data, dict) and data.get("world_id") is not None:
                        wid = data["world_id"]
                        break
                except Exception:
                    pass
    if wid is None:
        return result
    try:
        wid_i = int(wid)
        if wid_i not in _worlds:
            return result
    except Exception:
        return result
    for item in result:
        if item.type == "text":
            try:
                data = json.loads(item.text)
                if isinstance(data, dict):
                    data["status"] = _status_light(wid_i) if light else _status(wid_i)
                    item.text = json.dumps(data, ensure_ascii=False, indent=2)
            except Exception:
                pass
    return result


def _impl(name, args, before_signal=None):
    if name == "world_open":
        return _t_world_open(args)
    if name == "world_entities":
        return _t_world_entities(args)
    if name == "world_entity":
        return _t_world_entity(args)
    if name == "world_layers":
        return _t_world_layers(args)
    if name == "world_map":
        return _t_world_map(args)
    if name == "world_resolve":
        return _t_world_resolve(args)
    if name == "world_changes":
        return _t_world_changes(args)
    if name == "world_state":
        return _t_world_state(args)
    if name == "world_business_state":
        return _t_world_business_state(args)
    if name == "world_operation_check":
        return _t_world_operation_check(args)
    if name == "world_task_plan":
        return _t_world_task_plan(args)
    if name == "world_graph_replay_check":
        return _t_world_graph_replay_check(args)
    if name == "world_adapter_compare":
        return _t_world_adapter_compare(args)
    if name == "world_change_digest":
        return _t_world_change_digest(args)
    if name == "world_evidence":
        return _t_world_evidence(args)
    if name == "world_trace":
        return _t_world_trace(args)
    if name == "world_graph":
        return _t_world_graph(args)
    if name == "world_trace_archive":
        return _t_world_trace_archive(args)
    if name == "world_graph_archive":
        return _t_world_graph_archive(args)
    if name == "world_graph_assess":
        return _t_world_graph_assess(args)
    if name == "world_graph_bundle":
        return _t_world_graph_bundle(args)
    if name == "world_guide":
        return _t_world_guide(args)
    if name == "world_click":
        return _t_world_click(args, before_signal)
    if name == "world_fill":
        return _t_world_fill(args, before_signal)
    if name == "world_batch_fill":
        return _t_world_batch_fill(args, before_signal)
    if name == "world_press":
        return _t_world_press(args, before_signal)
    if name == "world_find":
        return _t_world_find(args)
    if name == "world_act":
        return _t_world_act(args, before_signal)
    if name == "world_outcome":
        return _t_world_outcome(args)
    if name == "world_wait":
        return _t_world_wait(args)
    if name == "world_screenshot":
        return _t_world_screenshot(args)
    if name == "world_eval":
        return _t_world_eval(args)
    if name == "world_assume":
        return _t_world_assume(args)
    if name == "world_ack":
        return _t_world_ack(args)
    if name == "world_status":
        return _t_world_status(args)
    if name == "world_timeline":
        return _t_world_timeline(args)
    if name == "world_click_at":
        return _t_world_click_at(args, before_signal)
    if name == "world_navigate":
        return _t_world_navigate(args, before_signal)
    if name == "world_close":
        return _t_world_close(args)
    if name == "world_list":
        return _t_world_list(args)
    raise ValueError(f"未知工具: {name}")


def _assumption_expr(doc_id, attr):
    """读元素真实状态的 JS 表达式:先按 DOM id,再按内核 id 解析(LLM 可能传 el_N)。"""
    resolve = (f"document.getElementById('{doc_id}') || "
               f"(window.agentWorld && agentWorld._runtime.world.elements.get('{doc_id}') || {{}})._el || {{}}")
    if attr == "checked":
        return f"() => {{ const el = {resolve}; return el.checked === true ? 'true' : 'false'; }}"
    return f"() => {{ const el = {resolve}; return (el.value != null && el.value !== '') ? el.value : (el.textContent || ''); }}"


def _assumption_check(wid):
    """工具调用间隙的前提快检(遵守 Playwright 线程亲和,在 executor 线程内执行)。

    每个假设按 ASSUMPTION_INTERVAL_S 节流;发现偏离 → 0.3s 后二次校验 → 挂 pending_notices。
    """
    try:
        w = _worlds.get(int(wid))
        if not w:
            return
        assumptions = w.get("assumptions")
        if not assumptions:
            return
        page = w.get("page")
        if page is None:
            return
        now = time.time()
        with w.setdefault("assumption_lock", threading.RLock()):
            for name, spec in list(assumptions.items()):
                if not spec.get("active", True):
                    continue
                if now - float(spec.get("last_checked", 0)) < ASSUMPTION_INTERVAL_S:
                    continue
                spec["last_checked"] = now
                try:
                    raw = page.evaluate(spec["expr"])
                    current = str(raw).strip() if raw is not None else ""
                    expected = str(spec.get("expect") or "")
                    if current == expected:
                        spec["strikes"] = 0
                        continue
                    # 双重校验:短暂间隔后再读一次,仍偏离才确认失效(防瞬时噪声)
                    time.sleep(ASSUMPTION_RECHECK_S)
                    raw2 = page.evaluate(spec["expr"])
                    current2 = str(raw2).strip() if raw2 is not None else ""
                    if current2 != expected:
                        spec["active"] = False  # 暂停,等 agent ack 恢复
                        notice = {"channel": "premise", "type": "assumption-violated",
                                  "name": name, "expected": expected, "actual": current2,
                                  "at_ms": int(time.time() * 1000)}
                        w.setdefault("pending_notices", []).append(notice)
                        # 统一时间线入账(因果:指向最近一次响应事件)
                        tl_evts = list(w.get("timeline") or [])
                        last_resp = next((e for e in reversed(tl_evts) if e["type"] == "response"), None)
                        _tl(wid, "premise", {
                            "name": name, "expected": expected, "actual": current2,
                            "derived_from": last_resp.get("seq") if last_resp else None,
                            "derived_url": last_resp.get("url") if last_resp else None,
                        })
                except Exception:
                    pass
    except Exception:
        pass


def _inject_notices(result, wid):
    """把待消费的前提失效通知附加到工具返回(骑现有返回,agent 无需额外调用)。"""
    try:
        w = _worlds.get(int(wid))
        if w and w.get("pending_notices"):
            for item in result or []:
                if getattr(item, "type", None) != "text":
                    continue
                try:
                    payload = json.loads(item.text)
                except Exception:
                    continue
                if not isinstance(payload, dict) or "notices" in payload:
                    continue
                with w.setdefault("assumption_lock", threading.RLock()):
                    notices = list(w["pending_notices"])
                    w["pending_notices"].clear()
                if notices:
                    payload["notices"] = notices
                    item.text = json.dumps(payload, ensure_ascii=False, indent=2)
                break
    except Exception:
        pass
    return result


def _t_world_assume(args):
    wid = int(args["world_id"])
    name = str(args["name"])
    doc_id = str(args.get("doc_id") or name)
    attr = str(args.get("attr") or "value")
    expect = str(args.get("expect") or "")
    w = _world(wid)
    expr = _assumption_expr(doc_id, attr)
    with w.setdefault("assumption_lock", threading.RLock()):
        w.setdefault("assumptions", {})[name] = {
            "expr": expr, "expect": expect, "active": True, "last_checked": 0, "strikes": 0}
    return _ok({"world_id": wid, "channel": "premise", "assumed": name, "expr": expr})


def _t_world_ack(args):
    wid = int(args["world_id"])
    name = str(args["name"])
    w = _world(wid)
    with w.setdefault("assumption_lock", threading.RLock()):
        spec = (w.get("assumptions") or {}).get(name)
        if spec:
            expect = args.get("expect")
            if expect is not None:
                spec["expect"] = str(expect)
            spec["active"] = True
            spec["strikes"] = 0
            return _ok({"world_id": wid, "channel": "premise", "resumed": name})
    return _ok({"world_id": wid, "channel": "premise", "resumed": False, "why": f"假设 {name} 不存在"})


def _t_world_status(args):
    wid = int(args["world_id"])
    w = _world(wid)
    with w.setdefault("assumption_lock", threading.RLock()):
        rows = [{"name": k, "active": bool(v.get("active", True)), "expect": v.get("expect")}
                for k, v in (w.get("assumptions") or {}).items()]
    return _ok({"world_id": wid, "channel": "premise", "assumptions": rows})


# ── 统一时间线(环境侧因果时间轴)─────────────────────────────────
# 三层反馈合并到一条 append-only 事件账本,统一 seq 游标:
#   action(动作) / request/response/failed(运行时流) / dom(DOM 变更) /
#   console/pageerror(控制台) / premise(前提失效)
# 设计要点:
#  - 事件带世界时钟 t(ms);seq 是账本内追加顺序(游标),展示按 t 语义解释
#  - DOM 事件懒合并:读取/动作时从内核 changes 拉取批量入账(不实时轮询)
#  - URL 归一化去 query(防 token 泄漏),不存响应体(隐私/体积)
#  - 环形缓冲(600 条),世界关闭即销毁
#  - causal_windows:按 action 事件切段,回答"这个动作引发了什么"(跨层因果)
def _tl(wid, etype, data=None):
    """统一时间线入账。线程安全;监听器回调与工具执行线程都可能调用。"""
    try:
        w = _worlds.get(int(wid))
        if not w:
            return None
        with w.setdefault("tl_lock", threading.RLock()):
            seq = w.get("timeline_seq", 0) + 1
            w["timeline_seq"] = seq
            evt = {"seq": seq, "t": int(time.time() * 1000), "type": etype}
            if data:
                evt.update(data)
            tl = w.setdefault("timeline", collections.deque(maxlen=TIMELINE_MAX))
            tl.append(evt)
            return seq
    except Exception:
        return None


def _tl_merge_dom(wid):
    """懒合并:把内核 DOM 变更流转入统一时间线(按内核时间戳排序批量入账)。

    降噪(实测:GitHub 渐进扫描 + 整页替换可产生 ~600 条/次):
      - 初始快照不入账:world_open 后首次合并只建立 name 集合
      - 批量替换聚合:同批 add+remove > 50 条(导航/刷新/SPA整块替换)
        → 聚合成一条 bulk 事件(结构信号保留,明细不淹没有用事件)
      - update 按"新 name"过滤:渐进扫描逐元素 touch 跳过;
        真实内容变化(价格 800→1200)必然产生新 name → 入账
      - add/remove 小批量(真实结构变化:弹窗/选项/表单)全量入账
    """
    try:
        w = _worlds.get(int(wid))
        if not w or w.get("page") is None:
            return
        since = w.get("tl_dom_since", 0)
        data = _evaluate(wid, "(s) => agentWorld.changes(s)", since)
        events = (data or {}).get("events", []) or []
        if not events:
            return
        with w.setdefault("tl_lock", threading.RLock()):
            w["tl_dom_since"] = (data or {}).get("to", since) or since
            events.sort(key=lambda e: e.get("t") or 0)
            names = w.setdefault("tl_dom_names", set())
            if not w.get("tl_dom_init"):
                for evt in events:
                    if evt.get("type") == "update":
                        n = (evt.get("name") or "")[:60]
                        if n:
                            names.add(n)
                w["tl_dom_init"] = True
                return
            adds = sum(1 for e in events if e.get("type") == "add")
            rems = sum(1 for e in events if e.get("type") == "remove")
            if adds + rems > 50:
                _tl(wid, "dom", {"dtype": "bulk", "add": adds, "remove": rems,
                                 "kt": (events[-1].get("t") if events else 0)})
                return
            for evt in events:
                etype = evt.get("type")
                name = (evt.get("name") or "")[:60]
                if etype == "update":
                    if name in names:
                        continue
                    names.add(name)
                    if len(names) > 2000:
                        names.clear()
                _tl(wid, "dom", {
                    "id": evt.get("id"), "name": name,
                    "semantic": (evt.get("semantic") or "")[:30],
                    "dtype": etype, "kt": evt.get("t"),
                })
    except Exception:
        pass


def _tl_action(wid, phase, name, args, result=None):
    """动作事件入账。phase: start/end。end 附结果摘要(后果卡主标签)。"""
    try:
        data = {"action": name, "phase": phase}
        if args:
            for k in ("id", "text", "url", "kind", "key"):
                if args.get(k) is not None:
                    data[k] = str(args[k])[:60]
        if phase == "end" and result:
            payload = _result_payload(result)
            if payload:
                data["outcome"] = payload.get("page_outcome")
                data["why"] = str(payload.get("why") or "")[:80]
        _tl(int(wid), "action", data)
    except Exception:
        pass


def _timeline_causal_windows(events):
    """因果窗口:按动作 start 事件切段,每段 = 该动作的后果(本动作 start 到下一动作 start)。

    注意:一次动作产生 start/end 两个 action 事件,只以 start 为窗口边界
    (end 之前的事件 = 动作执行期间的后果,归入本窗口;否则一次点击会被切成两段)。
    返回压缩摘要:类型计数 + 状态码 + 关键事件(前提失效/失败/4xx/JSON 接口)。
    """
    windows = []
    cur = None
    for e in events:
        if e["type"] == "action" and e.get("phase") == "start":
            if cur:
                windows.append(cur)
            cur = {"action": e.get("action"), "phase": e.get("phase"),
                   "seq": e["seq"], "items": []}
        elif cur is not None:
            cur["items"].append(e)
    if cur:
        windows.append(cur)
    out = []
    for w in windows:
        if not w["items"]:
            continue
        counts = {}
        statuses = []
        key_items = []
        for it in w["items"]:
            counts[it["type"]] = counts.get(it["type"], 0) + 1
            if it.get("status"):
                statuses.append(it["status"])
            if it["type"] in ("premise", "failed") or (it["type"] == "response" and it.get("status", 0) >= 400):
                key_items.append({"type": it["type"], "url": it.get("url"),
                                  "status": it.get("status"), "name": it.get("name")})
            elif it["type"] == "response" and "json" in (it.get("ctype") or ""):
                key_items.append({"type": "api", "url": it.get("url"), "status": it.get("status")})
        out.append({"action": w["action"], "from_seq": w["seq"],
                    "counts": counts, "statuses": statuses[:8], "key": key_items[:8]})
    return out


def _t_world_timeline(args):
    """统一时间线读取:游标增量 + 因果窗口 + 模式摘要。"""
    wid = int(args["world_id"])
    since = int(args.get("since", 0))
    mode = str(args.get("mode") or "digest").strip().lower()
    w = _world(wid)
    _tl_merge_dom(wid)
    with w.setdefault("tl_lock", threading.RLock()):
        tl = list(w.get("timeline") or [])
        events = [e for e in tl if e["seq"] > since]
    to = events[-1]["seq"] if events else since
    if mode == "raw":
        return _ok({"world_id": wid, "channel": "timeline", "from": since, "to": to,
                    "cursor": to, "events": events[:200]})
    counts = {}
    statuses = {}
    api_hits = []
    dom_counts = {}
    recent_dom = []   # 最近 DOM 变化明细(name 含新文本,如 content.1200 → 语义化摘要用)
    premises = []
    failures = []
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
        if e["type"] == "response":
            s = e.get("status", 0)
            statuses[s] = statuses.get(s, 0) + 1
            if "json" in (e.get("ctype") or ""):
                api_hits.append({"url": e.get("url"), "status": s})
        elif e["type"] == "dom":
            dom_counts[e.get("dtype")] = dom_counts.get(e.get("dtype"), 0) + 1
            recent_dom.append({"name": e.get("name"), "semantic": e.get("semantic"),
                               "dtype": e.get("dtype")})
            recent_dom = recent_dom[-3:]
        elif e["type"] == "premise":
            premises.append({"name": e.get("name"), "expected": e.get("expected"),
                             "actual": e.get("actual"), "derived_from": e.get("derived_from")})
        elif e["type"] == "failed":
            failures.append({"url": e.get("url"), "error": e.get("error")})
    # 静默失败:窗口内有 4xx/5xx/failed 且无 DOM 变化(L1 盲区自动标注)
    silent = []
    if failures or any(int(s) >= 400 for s in statuses):
        if not dom_counts:
            silent = [{"4xx_5xx": {str(k): v for k, v in statuses.items() if int(k) >= 400},
                       "failed": failures[:3]}]
    windows = _timeline_causal_windows(events)
    return _ok({
        "world_id": wid, "channel": "timeline", "from": since, "to": to,
        "cursor": to,
        "events_seen": len(events),
        "counts": counts,
        "statuses": {str(k): v for k, v in statuses.items()},
        "api_hits": api_hits[:10],
        "dom_changes": dom_counts,
        "recent_dom": recent_dom,
        "premises": premises[:8],
        "failures": failures[:5],
        "silent_failures": silent,
        "causal_windows": windows[:10],
    })


# ── L3 动作证据卡(runtime 流 → 结构化动作反馈)──────────────────
# 机制来源:真实站点验证(run_action_evidence:成功导航/重定向登录/输入联想 全通过)。
#  - 动作窗口切片:动作前 0.5s 至动作完成(runtime 事件自带时间戳)
#  - 只存元数据(URL/方法/类型/状态/时序),不读响应体(隐私/体积)
#  - URL 归一化去 query(防 token/敏感参数泄漏到工具返回)
#  - 规则化决策建议(重定向到登录/4xx/无请求/全 2xx/数据接口),不引入 LLM
def _build_action_evidence(wid, t_start, page_outcome=None, effect_verdict=None):
    """从 runtime 流切片构建动作证据卡。"""
    w = _worlds.get(int(wid))
    if not w:
        return None
    events = w.get("runtime_events") or []
    t_end = time.time()
    win = [e for e in events if t_start - ACTION_EVIDENCE_PRE_S <= e["t"] <= t_end]
    reqs = [e for e in win if e["type"] == "request"]
    resps = [e for e in win if e["type"] == "response"]
    fails = [e for e in win if e["type"] == "requestfailed"]
    cons = [e for e in win if e["type"] == "console" and e.get("level") == "error"]
    requests = []
    for r in reqs:
        entry = {"url": _evidence_norm_url(r.get("url")), "method": r.get("method"),
                 "rtype": r.get("rtype"), "at_s": round(r["t"] - t_start, 2)}
        cands = [x for x in resps if x.get("url") == r.get("url") and x["t"] >= r["t"]]
        if cands:
            resp = min(cands, key=lambda x: x["t"])
            entry["status"] = resp.get("status")
            entry["resp_at_s"] = round(resp["t"] - t_start, 2)
            if "json" in str(resp.get("ctype", "")):
                entry["api"] = True
        requests.append(entry)
    card = {
        "window_s": round(t_end - t_start, 1),
        "request_count": len(reqs),
        "requests": requests[:25],
        "redirects": [{"url": r["url"], "status": r["status"]}
                      for r in requests if r.get("status") in (301, 302, 303, 307, 308)],
        "failures": [{"url": _evidence_norm_url(f.get("url")), "error": f.get("error")}
                     for f in fails[:5]],
        "console_errors": [c["text"] for c in cons[:3]],
        "decision": None,
    }
    card["decision"] = _evidence_decision(card, page_outcome=page_outcome, effect_verdict=effect_verdict)
    return card


def _evidence_decision(card, page_outcome=None, effect_verdict=None):
    """规则化决策建议:证据 → agent 下一步行动(与实验脚本一致的口径)。

    融合后果卡判定:请求全 2xx 但页面判定未生效(unchanged/no-change)时,
    请求状态不能作为生效证据(实测:GitHub 后台轮询全 2xx,无副作用点击
    的 decision 曾误报"已生效,继续下一步"——与后果卡 unchanged 直接矛盾)。
    """
    if page_outcome in ("unchanged", "challenged") or effect_verdict in ("no-change", "unknown"):
        return ("页面判定动作未生效/存疑:请求状态不能确认生效"
                "(后台轮询/预取也可能 2xx),先核对目标元素与页面状态再重试")
    doc_urls = [r["url"] for r in card["requests"] if r.get("rtype") == "document"]
    if any("/login" in u for u in doc_urls):
        return (f"被重定向到登录页({[u for u in doc_urls if '/login' in u][0]})"
                f"→ 需要登录态,该动作不能继续,转人工/登录流程")
    if card["failures"]:
        return f"{len(card['failures'])} 个请求失败 → 网络/资源问题,重试前先确认"
    if card["console_errors"]:
        return f"{len(card['console_errors'])} 个控制台错误 → 页面脚本异常"
    statuses = [r.get("status") for r in card["requests"] if r.get("status")]
    if statuses and all(200 <= s < 300 for s in statuses):
        tip = "请求全部 2xx → 动作已生效,继续下一步"
        if any(r.get("api") for r in card["requests"]):
            tip += ";触发了数据接口响应,页面状态已迁移"
        return tip
    if statuses and any(s >= 400 for s in statuses):
        bad = [r["url"] for r in card["requests"] if r.get("status") and r["status"] >= 400]
        return f"{len(bad)} 个 4xx/5xx 请求 → 服务端拒绝,读取错误后修正"
    if not card["requests"]:
        return "窗口内无任何请求 → 点击可能未生效/被拦截/纯前端行为,需 DOM 确认"
    return None


def _inject_action_evidence(result, wid, t_start):
    """把动作证据卡附加到动作工具返回(与后果卡并列,不改变现有字段)。"""
    try:
        if t_start is None:
            return result
        # 融合后果卡判定:动作返回里的 page_outcome/effect.verdict
        page_outcome = None
        effect_verdict = None
        try:
            payload = _result_payload(result)
            if payload:
                page_outcome = payload.get("page_outcome")
                effect_verdict = (payload.get("effect") or {}).get("verdict")
        except Exception:
            pass
        card = _build_action_evidence(wid, t_start, page_outcome=page_outcome,
                                      effect_verdict=effect_verdict)
        if not card:
            return result
        for item in result or []:
            if getattr(item, "type", None) != "text":
                continue
            try:
                payload = json.loads(item.text)
            except Exception:
                continue
            if not isinstance(payload, dict) or "action_evidence" in payload:
                continue
            payload["action_evidence"] = card
            item.text = json.dumps(payload, ensure_ascii=False, indent=2)
            break
    except Exception:
        pass
    return result


def _t_world_open(args):
    global _next_world_id
    _expire_idle_sessions()
    url = args["url"] or ""
    ready_policy = str(args.get("ready_policy") or "action").strip().lower()
    if ready_policy not in ("action", "terrain", "stable"):
        raise ValueError("ready_policy 只能是 action、terrain 或 stable")
    task_id = str(args.get("task_id") or f"task_{uuid.uuid4().hex[:12]}")
    reuse_policy = str(args.get("reuse_policy") or "auto").strip().lower()
    if reuse_policy not in ("auto", "never"):
        raise ValueError("reuse_policy 只能是 auto 或 never")
    idle_ttl_ms = max(0, int(args.get("idle_ttl_ms", 600000)))
    wait_ms = int(args["wait_ms"]) if args.get("wait_ms") is not None else (0 if ready_policy == "action" else 3000)
    stabilize_ms = int(args.get("stabilize_ms", 10000))
    headful = bool(args.get("headful", False))
    profile = args.get("profile") or None
    cdp_url = args.get("cdp_url") or None
    site_adapter_file = str(args.get("site_adapter_file") or "").strip()
    if site_adapter_file and args.get("site_adapter") is not None:
        raise ValueError("site_adapter_file 与 site_adapter 只能二选一")
    site_adapter = (
        load_site_adapter_file(site_adapter_file)
        if site_adapter_file
        else normalize_site_adapter(args.get("site_adapter"))
    )
    reusable = _find_reusable_world(url, task_id, profile, cdp_url, headful, idle_ttl_ms) if reuse_policy == "auto" else None
    if reusable is not None:
        world = _world(reusable)
        reuse_target = world.pop("reuse_target_page", None)
        if reuse_target is not None:
            world["page"] = reuse_target
            world.setdefault("known_page_tokens", set()).add(id(reuse_target))
        idle_ms = int((time.time() - world.get("last_activity", time.time())) * 1000)
        world["last_activity"] = time.time()
        world["reuse_count"] = int(world.get("reuse_count", 0)) + 1
        _task_update(reusable, status="ready", page={"url": world["page"].url})
        if ready_policy in ("terrain", "stable"):
            _wait_progressive_phase(reusable, ready_policy, min(stabilize_ms, 3000) if ready_policy == "terrain" else stabilize_ms)
        summary = _evaluate(reusable, "agentWorld.query.getPageSummary()")
        scan_state = _scan_state(reusable)
        scan_phase = str(scan_state.get("phase"))
        return _ok({"world_id": reusable, "url": world["page"].url, "ready": True,
                    "headful": headful, "profile": profile, "cdp_url": cdp_url,
                    "summary": summary, "task": _task_public(reusable),
                    "readiness": {"action": True, "terrain": "ready" if scan_phase in ("terrain", "stable") else "partial",
                                  "stable": scan_phase == "stable", "full_scan": "ready" if (scan_state.get("fullScan") == "ready" or (ready_policy == "stable" and scan_phase == "stable")) else "pending",
                                  "scan_revision": int(scan_state.get("revision", 0))},
                    "session": {"reused": True, "reuse_type": "existing_world",
                                "idle_ms": idle_ms,
                                "reuse_count": world["reuse_count"]}})
    pw = _get_pw()
    if cdp_url:
        # CDP 挂载:连接已有 Chrome 的调试端口(复用日常登录态/已打开页面)。
        # 注意:这是连接而非启动,world_close 时只断开不关闭用户浏览器。
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        handle = browser
    elif profile:
        profile_dir = PROFILES_DIR / str(profile)
        profile_dir.mkdir(parents=True, exist_ok=True)
        # 持久化上下文:cookie/会话按 profile 名复用
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=not headful,
            viewport={"width": 1440, "height": 900},
        )
        # 恢复上次导出的会话状态(含 session cookie,跨世界保留登录态)
        state_file = profile_dir / "storage_state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if state.get("cookies"):
                    context.clear_cookies()
                    context.add_cookies(state["cookies"])
            except Exception as e:
                print(f"[world] storage state 恢复失败: {e}")
        handle = context
        page = context.pages[0] if context.pages else context.new_page()
    else:
        browser = pw.chromium.launch(headless=not headful)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        handle = browser
    page.add_init_script(INJECT_JS)
    if cdp_url:
        # 已存在的页面 add_init_script 不会立即生效(只对后续导航生效),
        # 若指定 url 且与当前页不同则导航(init 脚本随导航注入),否则手动注入当前页。
        if url and page.url != url:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        else:
            try:
                page.evaluate(INJECT_JS)
            except Exception:
                pass
    else:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if wait_ms:
        page.wait_for_timeout(wait_ms)
    ready = _wait_world_ready(page)
    if not ready:
        # CDP 连接失败时只断开,不关闭用户浏览器
        if cdp_url:
            try:
                browser.close()
            except Exception:
                pass
        else:
            handle.close()
        raise ValueError(f"世界注入失败(页面可能拦截了脚本): {url or '(CDP 当前页)'}")
    wid = _next_world_id
    _next_world_id += 1

    # 轻量网络与控制台静默失败监听(借鉴 Chrome DevTools MCP)
    net_errors = []
    console_errors = []
    # L3 动作证据:全量 runtime 流元数据(环形缓冲,不存响应体)
    runtime_events = []

    def _rt(evt):
        runtime_events.append(evt)
        if len(runtime_events) > 500:
            runtime_events.pop(0)
        # 同步入统一时间线(带世界时钟;URL 归一化)
        try:
            payload = {k: v for k, v in evt.items() if k != "t"}
            if payload.get("url"):
                payload["url"] = _evidence_norm_url(payload["url"])
            _tl(wid, evt["type"], payload)
        except Exception:
            pass

    def _on_response(res):
        try:
            status = int(res.status)
            snippet = ""
            kind = None
            now = time.time()
            url = res.url
            # 全量元数据(动作证据卡原料;普通响应不读 body)
            _rt({"t": now, "type": "response", "url": url, "status": status,
                 "rtype": res.request.resource_type if res.request else None,
                 "ctype": str(res.headers.get("content-type", ""))[:60]})
            if status >= 400:
                kind = "http_error"
                try:
                    snippet = res.text()[:200]
                except Exception:
                    pass
            elif 200 <= status < 300:
                # 保守识别 HTTP 200 的结构化业务错误，仅接受明确的顶层
                # errors 数组或 ok:false/success:false，避免把普通字段名
                # error 当作失败；只检查明显的接口路径，响应正文仍只保留
                # 短摘要并标记为不可信。
                try:
                    response_path = urlsplit(str(res.url)).path.lower()
                    api_hint = (
                        "/api/" in response_path
                        or "graphql" in response_path
                        or "/rpc" in response_path
                        or response_path.endswith(".json")
                    )
                    if not api_hint:
                        return
                    headers = res.headers or {}
                    content_type = str(headers.get("content-type", "")).lower()
                    if "json" in content_type:
                        body = json.loads(res.text()[:4000])
                        messages = []
                        errors = body.get("errors") if isinstance(body, dict) else None
                        if isinstance(errors, list) and errors:
                            for error in errors[:3]:
                                if isinstance(error, dict):
                                    message = error.get("message") or error.get("code")
                                    if message:
                                        messages.append(str(message))
                                elif error:
                                    messages.append(str(error))
                        elif isinstance(body, dict) and (body.get("ok") is False or body.get("success") is False):
                            message = body.get("message") or body.get("error") or body.get("code")
                            if message:
                                messages.append(str(message))
                        if messages:
                            kind = "application_error"
                            snippet = "；".join(messages)[:200]
                except Exception:
                    pass
            if kind:
                net_errors.append({
                    "url": res.url,
                    "status": status,
                    "kind": kind,
                    "detail": snippet,
                    "time": time.time(),
                })
                if len(net_errors) > 50:
                    net_errors.pop(0)
        except Exception:
            pass

    def _on_request(req):
        try:
            _rt({"t": time.time(), "type": "request", "url": req.url,
                 "method": req.method, "rtype": req.resource_type})
        except Exception:
            pass

    def _on_requestfailed(req):
        try:
            _rt({"t": time.time(), "type": "requestfailed", "url": req.url,
                 "error": (req.failure or "")[:120]})
            net_errors.append({
                "url": req.url, "status": 0, "kind": "request_failed",
                "detail": (req.failure or "")[:200], "time": time.time(),
            })
            if len(net_errors) > 50:
                net_errors.pop(0)
        except Exception:
            pass

    def _on_console(msg):
        try:
            _rt({"t": time.time(), "type": "console", "level": msg.type,
                 "text": msg.text[:150]})
            if msg.type == "error":
                console_errors.append({
                    "text": msg.text[:300],
                    "time": time.time(),
                })
                if len(console_errors) > 50:
                    console_errors.pop(0)
        except Exception:
            pass

    def _on_pageerror(exc):
        try:
            _rt({"t": time.time(), "type": "pageerror", "text": f"Uncaught {type(exc).__name__}: {str(exc)[:200]}"})
            console_errors.append({
                "text": f"Uncaught {type(exc).__name__}: {str(exc)[:300]}",
                "time": time.time(),
            })
            if len(console_errors) > 50:
                console_errors.pop(0)
        except Exception:
            pass

    try:
        page.on("request", _on_request)
        page.on("response", _on_response)
        page.on("requestfailed", _on_requestfailed)
        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)
    except Exception:
        pass

    task_id = str(args.get("task_id") or new_id("task"))[:120]
    task_goal = str(args.get("task_goal") or "")[:300]
    trace_id = new_id("trace")
    workflow_id = str(args.get("workflow_id") or site_adapter.get("workflow_id") or "")[:120]
    site_version = str(args.get("site_version") or site_adapter.get("site_version") or "")[:160]
    role = str(args.get("role") or "")[:120]
    permission_scope = str(args.get("permission_scope") or "")[:200]
    try:
        graph_valid_until = int(args["graph_valid_until"]) if args.get("graph_valid_until") is not None else None
    except (TypeError, ValueError):
        graph_valid_until = None
    raw_rules = args.get("business_state_rules")
    if raw_rules is None:
        raw_rules = site_adapter.get("state_rules")
    raw_contracts = args.get("operation_contracts")
    if raw_contracts is None:
        raw_contracts = site_adapter.get("operation_contracts")
    business_state_rules = normalize_state_rules(raw_rules)
    operation_contracts = normalize_operation_contracts(raw_contracts)
    enforce_contracts = bool(args.get("enforce_contracts", False))

    _worlds[wid] = {
        "handle": handle,
        "context": context,
        "page": page,
        "url": page.url,
        "opened_at": time.time(),
        "profile": profile,
        "cdp_url": cdp_url,
        "headful": headful,
        "last_activity": time.time(),
        "idle_ttl_ms": idle_ttl_ms,
        "reuse_count": 0,
        # 操作证据信道只在当前网页世界内短暂保存,不落盘。
        "evidence_seq": 0,
        "evidence_log": [],
        # 第一阶段任务运行时轨迹:只在当前网页世界内保存,由 world_trace/world_graph 读取。
        "task_id": task_id,
        "task_goal": task_goal,
        "trace_id": trace_id,
        "trace_log": [],
        "trace_step_seq": 0,
        "trace_persistence_enabled": persistence_enabled(),
        "workflow_id": workflow_id,
        "site_version": site_version,
        "role": role,
        "permission_scope": permission_scope,
        "graph_valid_until": graph_valid_until,
        "business_state_rules": business_state_rules,
        "operation_contracts": operation_contracts,
        "site_adapter": site_adapter,
        "site_adapter_file": site_adapter_file or None,
        "site_adapter_id": site_adapter.get("adapter_id"),
        "site_adapter_version": site_adapter.get("adapter_version"),
        "enforce_contracts": enforce_contracts,
        # 世界纪元:world_navigate 成功导航 +1;跨纪元旧 el_N 全部失效
        "epoch": 0,
        "network_errors": net_errors,
        "console_errors": console_errors,
        "runtime_events": runtime_events,
        "task_context": _new_task_context(task_id, wid),
        "main_page": page,
        "known_page_tokens": {id(page)},
        "observed_page_tokens": {id(page)},
        "runtime_ready_tokens": {id(page)},
        "runtime_pending_tokens": set(),
        "page_hooks": {"request": _on_request, "response": _on_response,
                       "requestfailed": _on_requestfailed, "console": _on_console,
                       "pageerror": _on_pageerror},
        "page_events": [],
    }
    world = _worlds[wid]

    def _on_new_page(new_page):
        """记录 context 新页；真正切换 active page 在动作后统一完成。"""
        try:
            world.setdefault("page_events", []).append({"page": new_page, "created_at": time.time()})
        except Exception:
            pass

    try:
        context.on("page", _on_new_page)
    except Exception:
        pass
    _task_update(wid, status="ready", page={"url": page.url, "epoch": 0})
    # 浏览器内分段扫描立即开始；action 不等待，terrain/stable 按策略等待。
    _start_progressive_scan(wid)
    stable_ready = False
    if ready_policy == "stable":
        deadline = time.time() + stabilize_ms / 1000
        while time.time() < deadline:
            try:
                st = _evaluate(wid, "() => agentWorld.query.getStatus()")
                if st.get("page", {}).get("state") == "stable":
                    stable_ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
    elif ready_policy == "terrain":
        _wait_progressive_phase(wid, "terrain", min(stabilize_ms, 3000))
    # 初始 DOM 快照在 open 时点建立(避免懒合并把 open 后、首次读取前的真实变化吞掉)
    _tl_merge_dom(wid)
    summary = _evaluate(wid, "agentWorld.query.getPageSummary()")
    scan_state = _scan_state(wid)
    if not stable_ready and ready_policy == "stable":
        stable_ready = str(scan_state.get("phase")) == "stable"
    terrain_ready = str(scan_state.get("phase")) in ("terrain", "stable")
    full_scan = "ready" if (str(scan_state.get("fullScan")) == "ready" or (ready_policy == "stable" and stable_ready)) else ("timed_out" if ready_policy == "stable" and not stable_ready else "pending")
    return _ok({"world_id": wid, "url": page.url, "ready": True, "headful": headful, "profile": profile, "cdp_url": cdp_url, "summary": summary,
                "task": _task_public(wid),
                "readiness": {"action": True, "terrain": "ready" if terrain_ready else "partial",
                              "stable": bool(stable_ready), "full_scan": full_scan,
                              "scan_revision": int(scan_state.get("revision", 0))},
                "session": {"reused": False, "reuse_type": "new", "idle_ms": 0, "reuse_count": 0},
                "task_id": task_id, "trace_id": trace_id,
                "trace_persistence_enabled": persistence_enabled(),
                "workflow_id": workflow_id, "site_version": site_version,
                "role": role, "permission_scope": permission_scope,
                "graph_valid_until": graph_valid_until,
                "site_adapter_id": site_adapter.get("adapter_id") or None,
                "site_adapter_version": site_adapter.get("adapter_version") or None,
                "site_adapter_file": site_adapter_file or None,
                "business_state_rule_count": len(business_state_rules),
                "operation_contract_count": len(operation_contracts),
                "enforce_contracts": enforce_contracts})


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


def _record_action_evidence(wid, action, args, before, result):
    """把动作前后的小状态摘要写入当前 world 的证据信道。

    不保存 world_fill/world_batch_fill 的具体文本,只保存目标和页面结果。
    """
    w = _world(wid)
    after = _page_signal_snapshot(wid)
    payload = _result_payload(result)
    effect = payload.get("effect") or {}
    url_changed = before.get("url") != after.get("url")
    title_changed = before.get("title") != after.get("title")
    dialog_delta = _signal_delta(before, after, "dialogs")
    menu_delta = _signal_delta(before, after, "menus")
    new_overlays = dialog_delta["new"] + menu_delta["new"]
    gone_overlays = dialog_delta["gone"] + menu_delta["gone"]
    target = args.get("id")
    if action == "world_batch_fill":
        target = [field.get("id") for field in args.get("fields", [])]
    elif action == "world_navigate":
        target = str(args.get("url", ""))[:300]
    transition = {
        "url_changed": url_changed,
        "title_changed": title_changed,
        "new_overlays": new_overlays[:8],
        "gone_overlays": gone_overlays[:8],
        "changes_seq_changed": after.get("changes_seq", 0) != before.get("changes_seq", 0),
        "changes_seq_advanced": after.get("changes_seq", 0) > before.get("changes_seq", 0),
    }
    verdict = effect.get("verdict")
    confidence = effect.get("confidence")
    why = effect.get("why")
    if not verdict:
        if url_changed or new_overlays:
            verdict, confidence = "effected", "high"
            why = "页面整体出现导航或新的弹窗/菜单"
        else:
            verdict, confidence = "no-change", "high"
            why = "未观察到页面整体导航或新的弹窗/菜单"
    # 静默失败也属于这次动作的证据。动作后页面可能完全没有错误文字，
    # 但浏览器监听器已经记录了新增的 HTTP 4xx/5xx 或 console.error；
    # 保存短摘要，供下一次 world_guide 的 focused runtime_signals 回传。
    network_errors = []
    console_errors = []
    try:
        net_list = w.get("network_errors") or []
        con_list = w.get("console_errors") or []
        net_from = int(before.get("_net_err_cursor", len(net_list)))
        con_from = int(before.get("_console_err_cursor", len(con_list)))
        network_errors = [
            {
                "signal": "network_application_error" if item.get("kind") == "application_error" else "network_error",
                "source": "browser",
                "status": item.get("status"),
                "url": str(item.get("url") or "")[:300],
                "detail": str(item.get("detail") or "")[:160],
                "untrusted": True,
            }
            for item in net_list[net_from:][:5]
        ]
        console_errors = [
            {
                "signal": "console_error",
                "source": "browser",
                "text": str(item.get("text") or "")[:300],
                "untrusted": True,
            }
            for item in con_list[con_from:][:5]
        ]
    except Exception:
        network_errors = []
        console_errors = []
    w["evidence_seq"] = int(w.get("evidence_seq", 0)) + 1
    if args.get("task_id"):
        w["task_id"] = str(args.get("task_id"))[:120]
    w["trace_step_seq"] = int(w.get("trace_step_seq", 0)) + 1
    trace_entry = None
    try:
        trace_entry = build_trace_entry(
            trace_id=str(w.get("trace_id") or new_id("trace")),
            task_id=str(w.get("task_id") or new_id("task")),
            step_index=int(w["trace_step_seq"]),
            action=action,
            args=args,
            before=before,
            after=after,
            payload=payload,
            evidence_seq=int(w["evidence_seq"]),
            world_epoch=int(w.get("epoch", 0)),
            context=_runtime_context(w),
        )
        attach_business_runtime(
            trace_entry,
            w.get("business_state_rules"),
            w.get("operation_contracts"),
        )
        w.setdefault("trace_log", []).append(trace_entry)
        if len(w["trace_log"]) > 200:
            del w["trace_log"][:-200]
    except Exception:
        # 轨迹是附加信道，不能影响既有操作证据的记录和返回。
        pass
    if persistence_enabled() and trace_entry is not None:
        try:
            _trace_store.append(str(w.get("task_id") or ""), trace_entry)
        except Exception:
            # 本地归档失败不能阻断当前动作；world_trace 仍可读取内存轨迹。
            pass
    entry = {
        "evidence_seq": w["evidence_seq"],
        "channel": "operation-evidence",
        "action": action,
        "target": target,
        "recorded_at": int(time.time() * 1000),
        "before": before,
        "after": after,
        "transition": transition,
        "verdict": verdict,
        "confidence": confidence,
        "why": why,
        "runtime_signals": network_errors + console_errors,
        "task_id": w.get("task_id"),
        "trace_id": w.get("trace_id"),
        "trace_step": int(w["trace_step_seq"]),
    }
    log = w.setdefault("evidence_log", [])
    log.append(entry)
    if len(log) > 100:
        del log[:-100]


def _t_world_evidence(args):
    """操作证据信道:按独立证据序号增量读取动作结果。"""
    wid = args["world_id"]
    since = int(args.get("since", 0))
    limit = max(1, min(int(args.get("limit", 20)), 100))
    w = _world(wid)
    all_items = [x for x in w.get("evidence_log", []) if x.get("evidence_seq", 0) > since]
    items = all_items[:limit]
    next_since = items[-1].get("evidence_seq", since) if items else since
    return _ok({
        "world_id": wid,
        "channel": "operation-evidence",
        "from": since,
        "to": next_since,
        "latest": int(w.get("evidence_seq", 0)),
        "has_more": len(all_items) > len(items),
        "evidence": items,
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


def _expand_candidates(wid, max_results=8):
    """识别可能需要先展开的可交互入口，仅提供提示，不自动点击。"""
    try:
        entities = _evaluate_query_retry(
            wid, "(f) => agentWorld.query.findEntities(f)",
            {"interactive": True, "maxResults": 500}, attempts=2
        ) or []
    except Exception:
        return []
    patterns = re.compile(r"(选择商品|包含\s*\d+\s*件商品|展开|更多|加载|分类|商品|产品|show|load|more|product|item)", re.I)
    scored = []
    for e in entities:
        text = str(e.get("text") or "")
        name = str(e.get("name") or "")
        hay = f"{name} {text}"
        if not patterns.search(hay):
            continue
        score = 0
        if re.search(r"(选择商品|包含\s*\d+\s*件商品|展开|加载|show|load)", hay, re.I):
            score += 3
        if re.search(r"(商品|产品|product|item)", hay, re.I):
            score += 2
        scored.append({
            "id": e.get("id"), "name": e.get("name"), "text": text[:180],
            "semantic": e.get("semantic"), "fingerprint": e.get("fingerprint"),
            "bounds": e.get("bounds"), "interactive": bool(e.get("interactive")),
            "expand_score": score,
        })
    scored.sort(key=lambda x: x.get("expand_score", 0), reverse=True)
    return scored[:max(1, int(max_results))]


def _focused_view(wid, terms, max_candidates=6, frontier_depth=1, expand="none"):
    """构建任务条件化的局部视野(战争迷雾)。

    这里只读取当前已经暴露的 DOM/语义/表单关系,不点击、不提交、不导航。
    结构上把结果拆成 focus(当前焦点)、frontier(可达候选)、blockers(阻断)
    和 collapsed_noise(折叠噪声),不把未知的 JavaScript 去向伪装成确定路径。
    """
    started = time.perf_counter()
    # MVP 只实现当前页面的一跳结构透视,保留字段便于后续扩展但不虚报二跳结果。
    frontier_depth = 1
    expand = str(expand or "none").strip().lower()
    if expand not in {"none", "nearby", "region", "all"}:
        expand = "none"
    try:
        max_candidates = max(1, min(int(max_candidates), 12))
    except Exception:
        max_candidates = 6

    try:
        payload = _evaluate(
            wid,
            r"""(arg) => {
            const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, '');
            const terms = (arg.terms || []).map(norm).filter(Boolean);
            const regionRoles = new Set([
                'navigation', 'banner', 'main', 'contentinfo', 'complementary',
                'form', 'dialog', 'tablist', 'menu', 'aside', 'section',
                'article', 'search', 'region'
            ]);
            const actionIntent = terms.some(t => /(退款|标记|权限|开启|开通|发布|提交|保存|删除|移除|授权|refund|mark|permission|enable|publish|submit|save|delete|authorize)/i.test(t));
            const riskRe = /(删除|移除|付款|支付|购买|发布|提交|保存|确认|授权|登录|验证|delete|remove|pay|purchase|publish|submit|save|confirm|authorize|login|verify)/i;
            const all = [...(agentWorld._runtime?.world?.elements?.values?.() || [])];
            const byNode = new Map(all.filter(e => e && e._el).map(e => [e._el, e]));

            const isVisible = (node) => {
                if (!node) return '';
                try {
                    const r = node.getBoundingClientRect();
                    const s = getComputedStyle(node);
                    return !!r.width && !!r.height && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                } catch (_) { return ''; }
            };
            const visibleText = (node) => isVisible(node) ? String(node.textContent || '').trim().slice(0, 180) : '';
            const regionOf = (node) => {
                let cur = node;
                let fallback = null;
                const overlayRoles = new Set(['dialog', 'alertdialog', 'menu']);
                for (let i = 0; i < 12 && cur; i++, cur = cur.parentElement) {
                    const known = byNode.get(cur);
                    if (known && (regionRoles.has(known.semantic) || regionRoles.has(known.tag))) {
                        const candidate = {
                            id: known.id,
                            name: known.name || known.text || known.semantic,
                            semantic: known.semantic || known.tag,
                            bounds: known.bounds || null
                        };
                        if (overlayRoles.has(candidate.semantic)) return candidate;
                        if (!fallback) fallback = candidate;
                    }
                }
                if (fallback) return fallback;
                return { id: 'region:unknown', name: '未命名区域', semantic: 'unknown', bounds: null };
            };
            const rows = [];
            for (const e of all) {
                if (!e || !e._el || !e.interactive) continue;
                const n = e._el;
                const tag = String(e.tag || n.tagName || '').toLowerCase();
                const attrs = e.attributes || {};
                const href = n.getAttribute('href') || attrs.href || '';
                const controls = n.getAttribute('aria-controls') || '';
                const form = n.closest ? n.closest('form') : null;
                const formAction = form ? (form.getAttribute('action') || form.action || '') : '';
                const type = String(n.getAttribute('type') || '').toLowerCase();
                const inputType = type || (tag === 'select' ? 'select-one' : '');
                const currentValue = ['input', 'textarea', 'select'].includes(tag) ? String(n.value || '') : '';
                const placeholder = n.getAttribute('placeholder') || '';
                const disabled = !!(n.disabled || n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true');
                const requiredMissing = !!(n.required && !String(n.value || '').trim());
                const invalid = n.getAttribute('aria-invalid') === 'true';
                const visible = isVisible(n);
                const region = regionOf(n);
                const ariaRole = String(n.getAttribute('role') || '').toLowerCase();
                const insideRow = !!(n.closest && n.closest('tr'));
                let actionRole = 'unknown-action';
                if (ariaRole === 'menuitem') actionRole = 'menu-item';
                else if (['input', 'textarea', 'select'].includes(tag)) actionRole = 'form-input';
                else if (tag === 'button' && ['dialog', 'alertdialog'].includes(region.semantic)
                    && /(确认|确定|保存|提交|删除|退款|发布|授权|confirm|save|submit|delete|refund|publish|authorize)/i.test([e.name, e.text].join(' '))) actionRole = 'confirm-action';
                else if (tag === 'button' && insideRow) actionRole = 'row-action';
                else if (tag === 'button' && ariaRole === 'tab') actionRole = 'tab';
                else if (tag === 'a' && insideRow) actionRole = 'object-link';
                else if (tag === 'a' && href) actionRole = 'navigation-link';
                else if (tag === 'button') actionRole = 'action-button';
                const ownHay = norm([
                    e.name, e.text, e.semantic, href, controls, formAction
                ].join(' '));
                const regionHay = norm([region.name, region.semantic].join(' '));
                const elementMatched = terms.filter(t => ownHay.includes(t));
                const regionMatched = terms.filter(t => regionHay.includes(t));
                const matched = [...new Set([...elementMatched, ...regionMatched])];
                const searchField = tag === 'input'
                    && ['search', 'text'].includes(inputType)
                    && terms.some(t => ['search', 'filter'].includes(t));
                const relation = href
                    ? 'direct-link-confirmed'
                    : (formAction && (type === 'submit' || tag === 'button' || tag === 'input')
                        ? 'form-target-confirmed'
                        : (controls
                            ? 'controlled-state-inferred'
                            : ((tag === 'button' && n.getAttribute('role') === 'tab') || tag === 'summary' || tag === 'details'
                                ? 'local-state-inferred'
                                : 'destination-unknown')));
                const confidence = relation.endsWith('confirmed')
                    ? 'confirmed'
                    : (relation.endsWith('inferred') ? 'inferred' : 'unknown');
                const pathScore = href ? 5 : (formAction ? 4 : (controls ? 3 : (relation === 'local-state-inferred' ? 2 : 0)));
                const taskScore = elementMatched.reduce((sum, t) => sum + Math.min(10, t.length) + 3, 0)
                    + regionMatched.reduce((sum, t) => sum + Math.min(6, t.length), 0)
                    + (searchField ? 8 : 0);
                const risk = riskRe.test([e.name, e.text, href, controls, formAction].join(' '));
                const actionScore = actionIntent && ['button', 'input', 'select', 'summary'].includes(tag) ? 4 : 0;
                const score = taskScore + pathScore + actionScore + (e.inViewport ? 1 : 0) + (risk ? 2 : 0);
                rows.push({
                    id: e.id,
                    name: e.name,
                    text: e.text,
                    semantic: e.semantic,
                    tag,
                    inputType,
                    value: currentValue,
                    placeholder,
                    searchField,
                    interactive: true,
                    inViewport: !!e.inViewport,
                    visible,
                    bounds: e.bounds || null,
                    fingerprint: e.fingerprint,
                    href: href || null,
                    formAction: formAction || null,
                    ariaControls: controls || null,
                    actionRole,
                    relation,
                    confidence,
                    elementMatched,
                    regionMatched,
                    matchedTerms: matched,
                    pathScore,
                    taskScore,
                    score,
                    risk,
                    disabled,
                    requiredMissing,
                    invalid,
                    region
                });
            }

            const alerts = [...document.querySelectorAll('[role="alert"], [aria-live="assertive"], [aria-invalid="true"]')]
                .map(n => ({
                    id: n.id || null,
                    text: visibleText(n),
                    role: n.getAttribute('role') || 'alert',
                    source: 'page',
                    untrusted: true
                }))
                .filter(x => x.text)
                .slice(0, 12);
            return {
                rows,
                alerts,
                totals: {
                    elements: all.length,
                    interactive: rows.length,
                    visibleInteractive: rows.filter(x => x.inViewport).length
                }
            };
            }""",
            {"terms": list(terms or [])},
        ) or {}
    except Exception as exc:
        # 渐进扫描/导航尚未就绪时不让 focused 导览把整条任务链打崩;
        # 返回空焦点并明确 unknown,由调用方按需重试或扩大视野。
        payload = {
            "rows": [],
            "alerts": [],
            "totals": {},
            "error": f"focused-view-unavailable:{type(exc).__name__}",
        }

    rows = payload.get("rows") or []
    alerts = payload.get("alerts") or []
    rows.sort(key=lambda r: (-int(r.get("score", 0)), -int(r.get("pathScore", 0)), str(r.get("id", ""))))

    def _region_key(row):
        region = row.get("region") or {}
        return region.get("id") or f"{region.get('semantic', 'unknown')}:{region.get('name', '')}"

    def _public(row, kind):
        public = {
            "id": row.get("id"),
            "name": row.get("name"),
            "text": row.get("text"),
            "semantic": row.get("semantic"),
            "tag": row.get("tag"),
            "interactive": bool(row.get("interactive")),
            "in_viewport": bool(row.get("inViewport")),
            "visible": bool(row.get("visible", True)),
            "bounds": row.get("bounds"),
            "fingerprint": row.get("fingerprint"),
            "href": row.get("href"),
            "form_action": row.get("formAction"),
            "aria_controls": row.get("ariaControls"),
            "action_role": row.get("actionRole", "unknown-action"),
            "relation": row.get("relation"),
            "confidence": row.get("confidence", "unknown"),
            "element_terms": row.get("elementMatched") or [],
            "region_terms": row.get("regionMatched") or [],
            "matched_terms": row.get("matchedTerms") or [],
            "region": row.get("region") or {},
            "disabled": bool(row.get("disabled")),
            "required_missing": bool(row.get("requiredMissing")),
            "invalid": bool(row.get("invalid")),
            "risk": bool(row.get("risk")),
            "evidence": "live-structure",
            "view_kind": kind,
        }
        # 仅向表单控件暴露控制类型和值，避免把 null 元数据复制到每个链接/按钮。
        if row.get("actionRole") == "form-input":
            public.update({
                "input_type": row.get("inputType"),
                "value": row.get("value"),
                "placeholder": row.get("placeholder"),
                "search_field": bool(row.get("searchField")),
            })
        return public

    # 当前焦点优先保留元素本身命中或具备明确路径的入口;
    # 仅因区域标题命中、但没有动作/路径关系的装饰按钮不进入焦点。
    focus_pool = [
        r for r in rows
        if r.get("visible", True) and (r.get("elementMatched") or (r.get("pathScore", 0) > 0 and r.get("matchedTerms")))
    ]
    if not focus_pool:
        focus_pool = [r for r in rows if r.get("visible", True) and r.get("pathScore", 0) > 0]
    # 页面出现错误/警告时，附近的重试、重新加载和恢复入口必须穿透噪声过滤，
    # 即使按钮文字与任务目标没有直接词面匹配。
    recovery_pool = [
        r for r in rows
        if r.get("visible", True)
        and re.search(r"(重试|再试|重新加载|刷新|retry|reload)", " ".join([str(r.get("name") or ""), str(r.get("text") or "")]), re.I)
    ] if alerts else []
    if recovery_pool:
        recovery_ids = {r.get("id") for r in recovery_pool}
        focus_pool = recovery_pool + [r for r in focus_pool if r.get("id") not in recovery_ids]
    # 活动菜单/抽屉/确认弹窗是当前视野的强边界:即使按钮文本没有直接命中任务词,
    # 也必须把其中的可见操作保留,否则 Agent 看见弹窗却找不到“确认/保存”按钮。
    active_overlay = [
        r for r in rows
        if r.get("visible", True)
        and (r.get("region") or {}).get("semantic") in {"dialog", "menu"}
    ]
    if active_overlay:
        existing = {r.get("id") for r in active_overlay}
        focus_pool = active_overlay + [r for r in focus_pool if r.get("id") not in existing]
    focus = focus_pool[:max_candidates]
    focus_ids = {r.get("id") for r in focus}
    focus_regions = {_region_key(r) for r in focus}

    blockers = []
    blocker_ids = set()
    for row in rows:
        reasons = []
        if not row.get("visible", True):
            continue
        if row.get("disabled"):
            reasons.append("element-disabled")
        if row.get("requiredMissing"):
            reasons.append("required-field-missing")
        if row.get("invalid"):
            reasons.append("aria-invalid")
        if reasons:
            item = _public(row, "blocker")
            item["reasons"] = reasons
            blockers.append(item)
            blocker_ids.add(row.get("id"))
    for alert in alerts:
        item = dict(alert)
        item["view_kind"] = "runtime-signal"
        blockers.append(item)

    def _frontier_allowed(row):
        if expand == "all":
            return True
        if row.get("elementMatched"):
            return True
        if _region_key(row) in focus_regions:
            # 侧栏/全局导航中一个入口命中任务词时,不要把同一整栏的所有
            # 直链复制到 frontier;主体表格/弹窗区域才允许保留附近路径。
            if (row.get("region") or {}).get("semantic") in {
                "navigation", "complementary", "banner", "contentinfo"
            }:
                return False
            return row.get("pathScore", 0) > 0
        if expand in {"region", "all"} and row.get("pathScore", 0) > 0:
            return True
        # 直接链接/表单是页面明确暴露的候选边,但仍受数量上限约束。
        if row.get("pathScore", 0) >= 4 and row.get("region", {}).get("semantic") in {
            "navigation", "menu", "tablist", "main", "unknown"
        }:
            # 默认窄视野不把整条全局导航当作候选噪声;只有附近/区域展开,
            # 或当前没有任何焦点时,才把这些直接链接作为探索边界。
            return expand in {"nearby", "region", "all"} or not focus
        return False

    frontier_pool = [r for r in rows if r.get("visible", True) and r.get("id") not in focus_ids and _frontier_allowed(r)]
    if expand == "none":
        frontier_limit = max_candidates
    elif expand == "nearby":
        frontier_limit = max_candidates * 2
    elif expand == "region":
        frontier_limit = max_candidates * 4
    else:
        frontier_limit = len(rows)
    frontier = frontier_pool[:frontier_limit]
    frontier_ids = {r.get("id") for r in frontier}

    selected_ids = focus_ids | frontier_ids | blocker_ids
    collapsed_rows = [r for r in rows if r.get("visible", True) and r.get("id") not in selected_ids]
    collapsed_by_region = {}
    for row in collapsed_rows:
        region = row.get("region") or {}
        key = _region_key(row)
        bucket = collapsed_by_region.setdefault(key, {
            "id": region.get("id"),
            "name": region.get("name"),
            "semantic": region.get("semantic"),
            "count": 0,
        })
        bucket["count"] += 1

    runtime_signals = []
    for alert in alerts:
        runtime_signals.append(dict(alert))
    for row in rows:
        if row.get("visible", True) and row.get("risk") and row.get("id") not in blocker_ids:
            item = _public(row, "runtime-signal")
            item["signal"] = "risk-sensitive-action"
            runtime_signals.append(item)
    if payload.get("error"):
        runtime_signals.append({
            "signal": "focused-view-unavailable",
            "source": "runtime",
            "message": "当前页面结构尚未就绪,请重试或扩大观察范围",
            "error": payload.get("error"),
            "view_kind": "runtime-signal",
        })

    focus_public = [_public(row, "focus") for row in focus]
    frontier_public = [_public(row, "frontier") for row in frontier]
    blocker_public = blockers[:max_candidates * 2 + 12]
    signal_public = runtime_signals[:max_candidates * 2 + 12]

    def _short_item(item):
        short = {
            "id": item.get("id"),
            "text": item.get("text"),
            "name": item.get("name"),
            "semantic": item.get("semantic"),
            "action_role": item.get("action_role", "unknown-action"),
            "relation": item.get("relation"),
            "confidence": item.get("confidence"),
            "disabled": bool(item.get("disabled")),
            "required_missing": bool(item.get("required_missing")),
            "href": item.get("href"),
            "aria_controls": item.get("aria_controls"),
            "matched_terms": item.get("matched_terms") or [],
            "region": {
                key: (item.get("region") or {}).get(key)
                for key in ("id", "name", "semantic")
                if (item.get("region") or {}).get(key) is not None
            },
        }
        if item.get("action_role") == "form-input":
            short.update({
                "input_type": item.get("input_type"),
                "value": item.get("value"),
                "placeholder": item.get("placeholder"),
                "search_field": bool(item.get("search_field")),
            })
        return short

    def _pick_score(item):
        role = item.get("action_role") or "unknown-action"
        role_score = {
            "form-input": 7,
            "confirm-action": 6,
            "menu-item": 6,
            "row-action": 5,
            "action-button": 4,
            "tab": 2,
            "object-link": 1,
            "navigation-link": 0,
        }.get(role, 0)
        if role == "form-input":
            if item.get("search_field") or item.get("input_type") == "search":
                role_score += 5
            if item.get("semantic") == "listbox" and str(item.get("value") or "") in {"全部", "全部状态", "all"}:
                role_score -= 4
        if re.search(r"(重试|再试|重新加载|刷新|retry|reload)", " ".join([str(item.get("name") or ""), str(item.get("text") or "")]), re.I):
            role_score += 10
        if (item.get("region") or {}).get("semantic") in {"menu", "dialog", "alertdialog"}:
            # 活动弹层是当前操作边界，优先于弹层外的搜索框、分页器等候选。
            role_score += 10
        if item.get("disabled"):
            role_score -= 8
        if item.get("required_missing"):
            role_score += 4
        return role_score

    actionable = [item for item in focus_public + frontier_public if not item.get("disabled")]
    required = [item for item in blocker_public if item.get("required_missing") and not item.get("disabled")]
    recommendation_pool = required + actionable
    recommendation_pool.sort(key=lambda item: (-_pick_score(item), -len(item.get("matched_terms") or []), str(item.get("id") or "")))
    recommended_next = _short_item(recommendation_pool[0]) if recommendation_pool else None
    alternative_items = []
    if recommended_next:
        for item in recommendation_pool[1:]:
            short = _short_item(item)
            if short.get("id") != recommended_next.get("id"):
                alternative_items.append(short)
            if len(alternative_items) >= 3:
                break
    if recommended_next:
        role_label = {
            "form-input": "先填写前置条件",
            "row-action": "优先使用行级操作入口",
            "menu-item": "优先使用当前菜单动作",
            "confirm-action": "检查风险后再确认",
            "object-link": "打开对象详情",
            "navigation-link": "进入导航路径",
        }.get(recommended_next.get("action_role"), "检查候选入口")
        next_action = f"{role_label}: {recommended_next.get('text') or recommended_next.get('name') or recommended_next.get('id')}"
    else:
        next_action = "当前没有可靠的可执行候选;先扩大视野或读取阻断信号"

    return {
        "focus": focus_public,
        "frontier": frontier_public,
        "blockers": blocker_public,
        "runtime_signals": signal_public,
        "recommended_next": recommended_next,
        "alternatives": alternative_items,
        "next_action": next_action,
        "collapsed_noise": {
            "count": len(collapsed_rows),
            "regions": sorted(collapsed_by_region.values(), key=lambda x: (-x["count"], str(x.get("name") or ""))),
            "expandable": bool(collapsed_rows),
            "expand_options": ["nearby", "region", "all"] if collapsed_rows else [],
        },
        "view_meta": {
            "mode": "focused",
            "depth": frontier_depth,
            "expand": expand,
            "generated_ms": round((time.perf_counter() - started) * 1000, 2),
            "source": "live-structure",
            "totals": payload.get("totals") or {},
            "degraded": bool(payload.get("error")),
        },
    }


def _compact_focused_view(view):
    """把 focused 视野编码成适合模型上下文的短包,保留行动所需字段。"""
    def compact_item(item):
        region = item.get("region") or {}
        return {
            key: item.get(key)
            for key in (
                "id", "name", "text", "semantic", "tag", "interactive", "in_viewport",
                "input_type", "value", "placeholder", "search_field",
                "href", "form_action", "aria_controls", "action_role", "relation", "confidence",
                "matched_terms", "disabled", "required_missing", "invalid", "risk", "view_kind",
            )
            if key in item
        } | ({"region": {key: region.get(key) for key in ("id", "name", "semantic") if region.get(key) is not None}} if region else {})

    def compact_signal(item):
        if not isinstance(item, dict):
            return item
        return {
            key: item.get(key)
            for key in (
                "id", "name", "text", "role", "source", "signal", "status",
                "url", "detail", "message", "kind", "untrusted", "view_kind",
            )
            if key in item
        }

    collapsed = view.get("collapsed_noise") or {}
    meta = view.get("view_meta") or {}
    compact = dict(view)
    compact["focus"] = [compact_item(item) for item in view.get("focus", [])]
    compact["frontier"] = [compact_item(item) for item in view.get("frontier", [])]
    compact["blockers"] = [compact_signal(item) for item in view.get("blockers", [])]
    compact["runtime_signals"] = [compact_signal(item) for item in view.get("runtime_signals", [])]
    compact["collapsed_noise"] = {
        "count": collapsed.get("count", 0),
        "regions": [
            {key: item.get(key) for key in ("id", "name", "semantic", "count") if key in item}
            for item in collapsed.get("regions", [])
        ],
        "expandable": bool(collapsed.get("expandable")),
        "expand_options": collapsed.get("expand_options", []),
    }
    compact["view_meta"] = {
        key: meta.get(key)
        for key in ("mode", "depth", "expand", "generated_ms", "source", "degraded", "totals")
        if key in meta
    }
    compact["view_meta"]["detail"] = "compact"
    return compact


def _t_world_guide(args):
    """把三个页面信道和当前实时结构组合成一份短的任务导览。"""
    wid = args["world_id"]
    task = str(args["task"]).strip()
    if not task:
        raise ValueError("task 不能为空,请用一句话描述当前任务")
    max_candidates = max(1, min(int(args.get("max_candidates", 6)), 12))
    view_mode = str(args.get("view_mode", "full") or "full").strip().lower()
    if view_mode not in {"full", "focused"}:
        raise ValueError("view_mode 只能是 full 或 focused")
    # MVP 只执行一跳的无副作用结构分析。
    frontier_depth = 1
    expand = str(args.get("expand", "none") or "none").strip().lower()
    if expand not in {"none", "nearby", "region", "all"}:
        raise ValueError("expand 只能是 none、nearby、region 或 all")
    view_detail = str(args.get("view_detail", "full") or "full").strip().lower()
    if view_detail not in {"full", "compact"}:
        raise ValueError("view_detail 只能是 full 或 compact")
    change_since = int(args.get("change_since", 0))
    evidence_since = int(args.get("evidence_since", 0))
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }")
    except Exception:
        pass

    state = _page_signal_snapshot(wid)
    change_digest = _result_payload(_t_world_change_digest({
        "world_id": wid,
        "since": change_since,
    }))
    w = _world(wid)
    recent_evidence_raw = [
        x for x in w.get("evidence_log", [])
        if x.get("evidence_seq", 0) > evidence_since
    ][-5:]
    # 导览只带最近证据的短摘要;需要动作前后完整状态时再读 world_evidence。
    recent_evidence = []
    for item in recent_evidence_raw:
        after = item.get("after") or {}
        transition = item.get("transition") or {}
        recent_evidence.append({
            "evidence_seq": item.get("evidence_seq"),
            "action": item.get("action"),
            "target": item.get("target"),
            "verdict": item.get("verdict"),
            "confidence": item.get("confidence"),
            "why": item.get("why"),
            "runtime_signals": [
                dict(signal) for signal in (item.get("runtime_signals") or [])[:8]
                if isinstance(signal, dict)
            ],
            "after": {
                "url": after.get("url"),
                "title": after.get("title"),
                "state": after.get("state"),
            },
            "transition": {
                "url_changed": transition.get("url_changed"),
                "new_overlays": transition.get("new_overlays", [])[:8],
                "gone_overlays": transition.get("gone_overlays", [])[:8],
                "changes_seq_changed": transition.get("changes_seq_changed"),
            },
        })
    terms = _guide_terms(task)
    raw_candidates = _evaluate(
        wid,
        """(arg) => {
            const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, '');
            const terms = (arg.terms || []).map(norm).filter(Boolean);
            const map = agentWorld.query.map(8);
            const rows = [];
            const seen = new Set();
            const push = (e, rg) => {
                if (!e || seen.has(e.id)) return;
                const href = e.attributes && e.attributes.href;
                const hay = norm([
                    rg.name, rg.semantic, e.name, e.text, e.semantic, href
                ].join(' '));
                const matched = [];
                let score = 0;
                    for (const term of terms) {
                        if (term && hay.includes(term)) {
                            matched.push(term);
                            score += Math.min(10, term.length) + (href ? 2 : 0);
                            // 任务明确寻找筛选/搜索时,优先实际控件和搜索区域,
                            // 避免页面标题或列表内容淹没任务入口。
                            if (term === 'filter' || term === 'search') {
                                if (rg.semantic === 'search') score += 12;
                                if (['input', 'searchbox', 'textbox', 'select'].includes(e.semantic)) score += 10;
                                if (e.semantic === 'navigation' && /filter/i.test(String(e.text || ''))) score += 5;
                            }
                        }
                    }
                if (!score) return;
                seen.add(e.id);
                rows.push({
                    id: e.id,
                    name: e.name,
                    text: e.text,
                    semantic: e.semantic,
                    interactive: !!e.interactive,
                    inViewport: !!e.inViewport,
                    bounds: e.bounds,
                    fingerprint: e.fingerprint,
                    href: href || null,
                    region: { semantic: rg.semantic, name: rg.name, bounds: rg.bounds },
                    matched,
                    match_score: score
                });
            };
            for (const block of (map.regions || [])) {
                const rg = block.region || {};
                for (const entry of (block.entries || [])) {
                    const e = agentWorld.query.getEntity(entry.id);
                    push(e, rg);
                }
            }
            // 地图只列每区的少量入口;任务目标可能在未列出的入口中,这里仅作页面内语义兜底。
            for (const brief of agentWorld.query.findEntities({ interactive: true, maxResults: 1000 })) {
                if (seen.has(brief.id)) continue;
                const e = agentWorld.query.getEntity(brief.id) || brief;
                push(e, { semantic: e.region || 'unknown', name: 'live-entity', bounds: null });
            }
            rows.sort((a, b) => {
                // 同分时:命中任务词数多者优先(避免 Notifications 这类无关元素
                // 与 star 命中者并列 4 分时靠原始顺序抢到 first → next_action 误导)
                return b.match_score - a.match_score
                    || (b.matched || []).length - (a.matched || []).length
                    || Number(b.interactive) - Number(a.interactive);
            });
            return rows.slice(0, arg.max);
        }""",
        {"terms": terms, "max": max_candidates},
    ) or []

    candidates = []
    for item in raw_candidates:
        candidate = {
            "id": item.get("id"),
            "name": item.get("name"),
            "text": item.get("text"),
            "semantic": item.get("semantic"),
            "interactive": item.get("interactive"),
            "in_viewport": item.get("inViewport"),
            "bounds": item.get("bounds"),
            "fingerprint": item.get("fingerprint"),
            "matched_terms": item.get("matched", []),
            "match_score": item.get("match_score", 0),
            "region": item.get("region"),
            "evidence": "live-structure",
        }
        if item.get("href"):
            candidate["href"] = item["href"]
            candidate["relation"] = "direct-link-confirmed"
        else:
            candidate["relation"] = "target-found-destination-unconfirmed"
        candidates.append(candidate)

    regions = []
    seen_regions = set()
    for candidate in candidates:
        rg = candidate.get("region") or {}
        key = (rg.get("semantic"), rg.get("name"))
        if key in seen_regions:
            continue
        seen_regions.add(key)
        regions.append({
            "semantic": rg.get("semantic"),
            "name": rg.get("name"),
            "bounds": rg.get("bounds"),
            "reason": "包含与当前任务匹配的实时入口",
        })

    direct_routes = [
        {
            "from": state.get("url"),
            "to": c.get("href"),
            "via": c.get("id"),
            "status": "confirmed",
        }
        for c in candidates if c.get("href")
    ]
    route_hint = _route_hint(wid, state, candidates)
    expand_candidates = _expand_candidates(wid)
    if candidates:
        top = candidates[0]
        # next_action 保底:只指向"可交互且命中任务词"的候选;
        # 同分时无关元素曾排第一(实测 Notifications 抢走 Star 任务的 first),误导 harness 点错。
        if top.get("interactive") and top.get("matched_terms"):
            next_action = f"优先检查候选 {top.get('id')} 的详图,再决定是否执行动作"
        else:
            next_action = ("候选区分度不足:最高分候选未命中任务词,请先确认目标元素"
                           "(可用 world_entities 按文本过滤)再执行动作")
    else:
        next_action = "当前页面没有找到直接匹配入口;不要猜测,先扩大到导航/菜单区域或提供更具体目标词"

    focused_view = None
    if view_mode == "focused":
        focused_view = _focused_view(
            wid,
            terms,
            max_candidates=max_candidates,
            frontier_depth=frontier_depth,
            expand=expand,
        )
        # 活动弹窗/菜单属于运行时信号,即使与任务词不匹配也必须保留。
        for signal_kind in ("dialogs", "menus"):
            for item in state.get(signal_kind, []) or []:
                focused_view["runtime_signals"].append({
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "text": item.get("text"),
                    "signal": signal_kind,
                    "source": "page-state",
                    "untrusted": True,
                    "view_kind": "runtime-signal",
                })
        # 页面没有错误文字时，动作证据中的浏览器网络/控制台错误仍必须
        # 穿透战争迷雾；这些文本来自页面运行时，标记为不可信数据，不能当指令。
        for evidence in recent_evidence:
            for signal in evidence.get("runtime_signals") or []:
                signal = dict(signal)
                signal.setdefault("view_kind", "runtime-signal")
                focused_view["runtime_signals"].append(signal)
        focused_view["runtime_signals"] = focused_view["runtime_signals"][:max_candidates * 2 + 12]
        if view_detail == "compact":
            focused_view = _compact_focused_view(focused_view)

    try:
        node_id, _ = _page_node_identity(state)
        _task_update(wid, page={"url": state.get("url"), "node": node_id,
                                "epoch": int(w.get("epoch", 0))},
                     route={"mode": "mcp", "source": route_hint.get("source"),
                            "validated": bool(route_hint.get("validated")),
                            "stale": bool(route_hint.get("stale"))})
    except Exception:
        pass

    result = {
        "world_id": wid,
        "channel": "task-guide",
        "task": task,
        "terms": terms,
        "state": {
            "url": state.get("url"),
            "title": state.get("title"),
            "status": state.get("state"),
            "dialogs": state.get("dialogs", []),
            "menus": state.get("menus", []),
        },
        "change_digest": change_digest,
        "recent_evidence": recent_evidence,
        "relevant_regions": regions[:6],
        "candidates": candidates,
        "routes": direct_routes[:max_candidates],
        "route_hint": route_hint,
        "expand_candidates": expand_candidates,
        "next_action": next_action,
        "unknown": [
            "点击后的新页面结构尚未确认",
            "没有公开链接的按钮去向需要执行后用证据确认",
        ],
        "next_cursors": {
            "change_since": change_digest.get("to", change_since),
            "evidence_since": int(w.get("evidence_seq", evidence_since)),
        },
        "task_state": _task_public(wid),
    }
    if focused_view is not None:
        result.update(focused_view)
        # focused 模式不再重复携带 full 导览的候选/路由大块;否则“战争迷雾”
        # 在服务端生成了窄视野,却在传输层又把整张旧地图带回去。
        for field in ("relevant_regions", "candidates", "routes", "route_hint", "expand_candidates"):
            result.pop(field, None)
        if view_detail == "compact":
            digest = result.get("change_digest") or {}
            result["change_digest"] = {
                key: digest.get(key)
                for key in ("from", "to", "changed", "events_seen", "counts", "importance_counts", "key")
                if key in digest
            }
            result.pop("task_state", None)
            result.pop("next_cursors", None)
        result["view_mode"] = view_mode
        result["frontier_depth"] = frontier_depth
        result["expand"] = expand
        result["view_detail"] = view_detail
        result["unknown"] = list(result.get("unknown") or []) + [
            "没有明确 href、表单目标或 aria-controls 的 JavaScript 按钮去向仍需执行后确认",
            "候选边界只表示当前页面可观察的可能路径,不代表服务器结果已确定",
        ]
    return _ok(result)


def _build_locator(w, ent):
    """根据原生网页世界元素信息构建 Playwright locator(行动层整合)。
    优先级:页面原生 id > placeholder 属性 > ARIA role+可访问名 > 文本。找不到返回 None。
    """
    page = w["page"]
    attrs = ent.get("attributes") or {}
    text = (ent.get("text") or "").strip()
    semantic = ent.get("semantic") or ""
    acc_name = (attrs.get("ariaLabel") or "").strip() or (attrs.get("placeholder") or "").strip() or text

    def _count(loc):
        try:
            return loc.count()
        except Exception:
            return 0

    # 1. 页面原生 id(精确唯一;多匹配时优先可见的那个——SPA 常保留隐藏副本)
    if attrs.get("id"):
        loc = page.locator(f'[id="{attrs["id"]}"]')
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    # 2. placeholder 属性(输入框常见)
    if attrs.get("placeholder"):
        loc = page.locator(f'[placeholder="{attrs["placeholder"]}"]')
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    # 3. ARIA role + 可访问名(Playwright 语义定位)
    pw_roles = {
        "button": "button", "link": "link", "input": "textbox",
        "combobox": "combobox", "listbox": "listbox", "option": "option",
        "tab": "tab", "tablist": "tablist", "heading": "heading",
        "navigation": "navigation", "search": "searchbox", "dialog": "dialog",
    }
    if semantic in pw_roles and acc_name and len(acc_name) <= 80:
        loc = page.get_by_role(pw_roles[semantic], name=acc_name, exact=False)
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    # 3a. 已知卡片样式时锁定顶层容器。仅用 role+长文本可能在动态站点中
    # 命中重叠的可访问子树；card-shine 是 DOM 的结构事实，适合优先定位外层卡。
    class_name = str(attrs.get("className") or "")
    if semantic == "link" and "card-shine" in class_name and text:
        try:
            title_hint = text[:60]
            loc = page.locator('[role="link"].card-shine').filter(has_text=title_hint)
            if _count(loc) == 1:
                return loc
            if _count(loc) > 1:
                loc_vis = loc.filter(visible=True)
                if _count(loc_vis) == 1:
                    return loc_vis
        except Exception:
            pass
    # 3b. 长文本语义定位:聚合站常用 role=link 的整张卡片承载点击，
    # 但卡片文本超过 Playwright accessible-name 的短文本限制且没有 href。
    # 用卡片自身的可见文本过滤 role，避免动态列表重排后退回易漂移的坐标点击。
    if semantic in pw_roles and text:
        text_hint = text[:80]
        try:
            loc = page.get_by_role(pw_roles[semantic]).filter(has_text=text_hint)
            if _count(loc) == 1:
                return loc
            if _count(loc) > 1:
                loc_vis = loc.filter(visible=True)
                if _count(loc_vis) == 1:
                    return loc_vis
        except Exception:
            pass
    # 4. 文本唯一匹配(短文本)
    if text and 1 <= len(text) <= 50:
        loc = page.get_by_text(text, exact=True)
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
        loc = page.get_by_text(text, exact=False)
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    return None


def _click_locator_nowait(locator, timeout=10000):
    """发出点击但不等待目标页面完成导航。

    外部转链站点可能加载很慢；点击是否发出与外部页面何时稳定是两个不同
    的事实。优先使用 Playwright 的 no_wait_after，旧版本不支持时兼容回退。
    """
    try:
        locator.click(timeout=timeout, no_wait_after=True)
    except TypeError:
        locator.click(timeout=timeout)


def _refresh_core_status(wid, settle_ms=300):
    """操作后等防抖+渲染,主动刷新内核状态(状态卡反映操作结果)

    导航竞态保护:点击触发导航后旧文档执行上下文随即销毁,此时 evaluate
    会阻塞等待新文档(实测 GitHub 导航最长阻塞 ~30s,动作反馈被拖慢)。
    刷新旧文档本身无意义——3s 短超时,失败即放弃,后果卡由 URL 事实判定。
    """
    w = _world(wid)
    time.sleep(settle_ms / 1000)
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }", timeout=3000)
    except Exception:
        pass


def _fill_visible(wid, text):
    """验证目标文本是否已落入页面某个"可见且未被覆盖"的输入框。

    背景:SPA 对话框(如 Google Flights)点击输入框后会新建一个可见输入框副本,
    原输入框被覆盖。Playwright locator.fill() 只要求元素可见可编辑、不检测遮挡,
    会把值填进被覆盖的旧框而"静默成功"。此验证用 elementFromPoint 排除被覆盖框。
    """
    try:
        ok = _evaluate(
            wid,
            """(text) => {
                const nodes = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(n => {
                    const r = n.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) return false;
                    const s = getComputedStyle(n);
                    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
                    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                    const top = document.elementFromPoint(cx, cy);
                    // 顶层是自己或包含自己 = 未被覆盖
                    return top === n || n.contains(top) || (top && top.contains(n));
                });
                return nodes.some(n => ((n.value || '') + ' ' + (n.textContent || '')).includes(text));
            }""",
            text,
        )
        return bool(ok)
    except Exception:
        return False


def _is_submit_trigger(wid, target_id, key=None):
    """点击/按键目标是否为"疑似提交动作"触发元素(form 关联 / type=submit)。

    page_outcome 的 challenged 检测只在疑似提交动作后启用,避免普通点击
    (点广告/点链接/点装饰)被页面里常驻的 fixed 遮罩 iframe(如支付组件、地图)
    误触挑战判定。对应 Claude 评审的 submit_trigger 精确化建议。
    key 非 None 时仅 Enter 视为提交类按键。
    """
    if key is not None and str(key).lower() not in ("enter",):
        return False
    try:
        res = _evaluate(
            wid,
            """(id) => {
                const el = agentWorld._runtime.world.elements.get(id);
                if (!el || !el._el) return false;
                const n = el._el;
                // 1. 自身是 submit 类型
                const tag = n.tagName.toLowerCase();
                if (tag === 'button' || tag === 'input') {
                    const t = (n.getAttribute('type') || '').toLowerCase();
                    if (t === 'submit') return true;
                }
                // 2. 位于 form 内(button 默认行为=提交)
                if (n.closest && n.closest('form')) return true;
                // 3. input 按 Enter 隐式提交
                if (tag === 'input') return true;
                return false;
            }""",
            target_id,
        )
        return bool(res)
    except Exception:
        return False


def _challenge_detection(wid):
    """结构化挑战检测:新出现的"固定全屏遮罩 + iframe 子元素"。

    特征①(强信号,对应 8.4 评审修正):
      存在 position:fixed(覆盖大部分视口)的容器,且内部有可见 iframe 子元素。
    不依赖任何 CAPTCHA 提供商 URL 白名单(提供商换 CDN/代理/第一方域名);②③
    (sandbox/焦点捕获)不做为 challenged 依据,仅作 uncertain 的辅助证据。
    返回 None(无挑战)或 {type, confidence, evidence[]}。
    """
    try:
        raw = _evaluate(
            wid,
            """() => {
                const vw = window.innerWidth, vh = window.innerHeight;
                const fixed = [];
                // 找所有 fixed 定位容器(排除纯装饰:面积需覆盖大部分视口)
                for (const el of document.querySelectorAll('*')) {
                    const s = getComputedStyle(el);
                    if (s.position !== 'fixed') continue;
                    const r = el.getBoundingClientRect();
                    const area = r.width * r.height;
                    if (area < vw * vh * 0.25) continue;  // 小于 25% 视口不算全屏遮罩
                    // 容器内是否有可见 iframe
                    const ifr = el.querySelector('iframe');
                    if (ifr) {
                        const ir = ifr.getBoundingClientRect();
                        if (ir.width < 50 || ir.height < 50) continue;
                        fixed.push({
                            w: Math.round(r.width), h: Math.round(r.height),
                            ifrW: Math.round(ir.width), ifrH: Math.round(ir.height),
                            ifrSrc: (ifr.src || '').slice(0, 120),
                            bg: (s.backgroundColor || ''),
                        });
                        break;  // 一个就够
                    }
                }
                return JSON.stringify(fixed.slice(0, 2));
            }""",
        )
        data = json.loads(raw) if isinstance(raw, str) else raw
        if data:
            f = data[0]
            return {
                "type": "modal_iframe_challenge",
                "confidence": "medium",
                "evidence": [
                    f"新出现 fixed 全屏遮罩(约 {f['w']}x{f['h']}px)内含 iframe({f['ifrW']}x{f['ifrH']}px)",
                    f"iframe 来源: {f['ifrSrc'] or '(同源/空白)'}",
                ],
            }
        return None
    except Exception:
        return None


def _build_page_outcome(wid, before_signal, after_signal, submit_trigger=False, effect=None,
                        overlays_changed=False, check_errors=False):
    """把动作前后信号 + 局部判定合成统一后果卡主标签(五态,平铺字符串)。

    progressed  effect.verdict ∈ {effected, visual-effected}(导航/弹窗/状态翻转/填表验证/视觉)
    challenged 疑似提交动作后出现 fixed 全屏遮罩+iframe(CAPTCHA/二次验证)→ 停止上报人工
    errored    表单错误信号(role=alert / aria-invalid,仅表单类动作后检测)→ 读错误修正重试
              或网络/控制台静默失败(HTTP 4xx/5xx / console.error)→ 即使 DOM 无变化也能精准归因
    uncertain  effect.verdict == changed(有变化但性质不明)→ 最多补一次 world_state
    uncertain  effect.verdict == unknown(证据管线缺席,生效未知)→ 复核一次,绝不当失败
    unchanged  其余(没有有效变化)→ 按失败路径处理

    设计:合并在 server 端完成,agent 收到的不是四条独立信号而是预合成主标签。
    返回 (page_outcome, situation, confidence, why)。
    """
    # 浏览器级网络错误页不是“已完成导航”。如果只依据 URL 发生变化，
    # chrome-error://chromewebdata/ 会被误报成 progressed，进而让上层继续
    # 读取错误页或沿着错误路线执行。先用页面 URL 事实拦截这一类结果。
    after_url_fact = str((after_signal or {}).get("url") or "")
    if after_url_fact.startswith("chrome-error://"):
        return (
            "errored",
            {"type": "browser_network_error", "to_url": None,
             "errors": [{"url": after_url_fact}]},
            "high",
            f"浏览器打开目标页面失败: {after_url_fact[:160]}",
        )
    url_changed = before_signal.get("url") != after_signal.get("url")
    effect = effect or {}
    verdict = effect.get("verdict")

    # 1. challenged:仅疑似提交动作后启用挑战检测(避免常驻遮罩组件误报)
    if submit_trigger:
        challenge = _challenge_detection(wid)
        if challenge:
            why = "页面被挑战遮罩/验证墙拦截: " + " ".join(challenge.get("evidence", []))[:200]
            return (
                "challenged",
                {"type": challenge.get("type", "challenge"), "to_url": None,
                 "evidence": challenge.get("evidence", [])},
                challenge.get("confidence", "medium"),
                why,
            )

    # 2. errored:表单错误信号(role=alert / aria-invalid,仅表单类动作后检测,
    #    避免普通点击被页面常驻错误提示元素误判)
    if check_errors:
        try:
            err_signal = _evaluate(
                wid,
                """() => {
                    const alerts = [...document.querySelectorAll('[role="alert"], [aria-live="assertive"], .gl-field-error, [aria-invalid="true"]')]
                        .filter(e => {
                            const s = getComputedStyle(e);
                            const r = e.getBoundingClientRect();
                            if (s.display === 'none' || s.visibility === 'hidden') return false;
                            return r.width > 0 && r.height > 0;
                        })
                        .map(e => ({ tag: e.tagName.toLowerCase(), text: (e.textContent || '').trim().slice(0, 80) }))
                        .filter(x => x.text && x.text.length > 1)
                        .slice(0, 5);
                    return JSON.stringify(alerts);
                }""",
            )
            alerts = json.loads(err_signal) if isinstance(err_signal, str) else (err_signal or [])
            if alerts:
                names = "、".join(a.get("text", "")[:40] for a in alerts[:3])
                return (
                    "errored",
                    {"type": "form_validation_error", "to_url": None, "errors": alerts},
                    "high",
                    f"检测到 {len(alerts)} 个错误信号元素: {names}",
                )
        except Exception:
            pass

    # 3. effect 判定(局部判定 + 全局纠正之后)
    if verdict in ("effected", "visual-effected"):
        situation_type = "navigation" if url_changed else \
            ("overlay" if overlays_changed else \
             ("form" if "填表值" in (effect.get("why") or "") else \
              ("state-flip" if "状态变化" in (effect.get("why") or "") else \
               ("visual" if verdict == "visual-effected" or "视觉" in (effect.get("why") or "") else "none"))))
        return (
            "progressed",
            {"type": situation_type, "to_url": after_signal.get("url") if url_changed else None},
            effect.get("confidence") or "high",
            effect.get("why") or "操作已生效",
        )
    if verdict == "changed":
        return (
            "uncertain",
            {"type": "none", "to_url": None},
            effect.get("confidence") or "medium",
            effect.get("why") or "有变化但无法确认是否生效",
        )
    if verdict == "unknown":
        # P1:证据缺席→ uncertain(复核一次),绝不映射为 unchanged(未知不是失败)。
        return (
            "uncertain",
            {"type": "none", "to_url": None},
            effect.get("confidence") or "low",
            effect.get("why") or "证据缺失,生效未知",
        )

    # 4. 静默失败气泡(借鉴 Chrome DevTools MCP):
    #    DOM 无变化 → 但如果动作窗口内捕获到了 HTTP 4xx/5xx 或 console.error,
    #    升级为 errored 并注入真实报错原因,彻底消灭"不明 unchanged"盲目重试死循环。
    try:
        w = _world(wid)
        net_list = w.get("network_errors") or []
        con_list = w.get("console_errors") or []
        before_net = before_signal.get("_net_err_cursor", len(net_list))
        before_con = before_signal.get("_console_err_cursor", len(con_list))
        new_net = net_list[before_net:]
        new_con = con_list[before_con:]

        if new_net:
            err = new_net[0]
            detail = err.get("detail") or ""
            detail_str = detail[:120] if detail else ""
            application_error = err.get("kind") == "application_error"
            why_prefix = "操作引发后端业务错误" if application_error else "操作引发后端接口报错"
            why_str = f"{why_prefix}: HTTP {err['status']} {err.get('url', '')}"
            if detail_str:
                why_str += f" — {detail_str}"
            return (
                "errored",
                {
                    "type": "network_application_error" if application_error else "network_error",
                    "to_url": None,
                    "errors": [{"url": e["url"], "status": e["status"], "kind": e.get("kind"), "detail": (e.get("detail") or "")[:120]}
                               for e in new_net[:5]],
                },
                "high",
                why_str,
            )
        if new_con:
            texts = [c["text"] for c in new_con[:3]]
            return (
                "errored",
                {
                    "type": "console_error",
                    "to_url": None,
                    "errors": [{"text": c["text"]} for c in new_con[:5]],
                },
                "medium",
                f"操作引发前端控制台异常: {texts[0][:120]}",
            )
    except Exception:
        pass

    return (
        "unchanged",
        {"type": "none", "to_url": None},
        effect.get("confidence") or "high",
        effect.get("why") or "未观察到任何生效证据",
    )



def _finalize_click_result(wid, ret, before_signal, after_signal=None):
    """把局部点击结果和页面整体信号合并成最小闭环反馈。

    URL 变化和新弹窗是强证据,即使点击目标附近没有变化,也不能报告 no-change。
    after_signal 可由调用方传入(统一后果卡复用同一份快照,避免重复 evaluate)。
    """
    if after_signal is None:
        after_signal = _page_signal_snapshot(wid)
    url_changed = before_signal.get("url") != after_signal.get("url")
    title_changed = before_signal.get("title") != after_signal.get("title")
    dialog_delta = _signal_delta(before_signal, after_signal, "dialogs")
    menu_delta = _signal_delta(before_signal, after_signal, "menus")
    new_overlays = dialog_delta["new"] + menu_delta["new"]
    gone_overlays = dialog_delta["gone"] + menu_delta["gone"]
    feedback = {
        "source": "global-page-state",
        "page": {
            "before_url": before_signal.get("url"),
            "after_url": after_signal.get("url"),
            "url_changed": url_changed,
            "before_title": before_signal.get("title"),
            "after_title": after_signal.get("title"),
            "title_changed": title_changed,
            "before_state": before_signal.get("state"),
            "after_state": after_signal.get("state"),
        },
        "overlays": {
            "new": new_overlays[:8],
            "gone": gone_overlays[:8],
            "changed": bool(new_overlays or gone_overlays),
        },
        "changes_seq": {
            "before": before_signal.get("changes_seq", 0),
            "after": after_signal.get("changes_seq", 0),
        },
    }
    ret["feedback"] = feedback

    effect = ret.get("effect")
    if effect:
        effect["global"] = {
            "url_changed": url_changed,
            "new_overlays": new_overlays[:8],
            "title_changed": title_changed,
        }

    # 全局页面事实优先于目标局部区域判断,纠正导航/弹窗的误报。
    if url_changed:
        if effect and effect.get("verdict") != "effected":
            effect["local_verdict"] = effect.get("verdict")
            effect["local_why"] = effect.get("why")
        if not effect:
            effect = {"observed": [], "region_changed": {"new": 0, "gone": 0}}
            ret["effect"] = effect
        effect.update({
            "verdict": "effected",
            "confidence": "high",
            "why": f"页面整体发生导航: URL 从 {before_signal.get('url')} 变为 {after_signal.get('url')}",
        })
    elif new_overlays and (not effect or effect.get("verdict") != "effected"):
        if effect:
            effect["local_verdict"] = effect.get("verdict")
            effect["local_why"] = effect.get("why")
        else:
            effect = {"observed": [], "region_changed": {"new": 0, "gone": 0}}
            ret["effect"] = effect
        names = "、".join((x.get("name") or x.get("text") or x.get("id", "")) for x in new_overlays[:4])
        effect.update({
            "verdict": "effected",
            "confidence": "high",
            "why": f"页面整体出现新的弹窗/菜单: {names}",
        })
    return ret


# ── 统一后果卡(阶段 A:所有动作的同一出口)──────────────────────
# page_outcome 五态:progressed | challenged | errored | uncertain | unchanged
# 主标签为平铺字符串(弱模型只读 page_outcome 一个键),卡片其余字段为证据与契约。

# ── 来源标记(F2 安全收口)──────────────────────────────────────
# 每条返回的字段来源四分类(规则写死,不让模型猜):
#   fact       页面客观事实(URL/el id/bounds/aria 状态/changes_seq)
#   evidence   本次动作前后差分证据(observed/verdict/visual_diff_score)
#   inference  服务端/导览推断(guide.candidates/next.suggested/匹配分)
#   untrusted  页面自由文本(text/name/aria-label/placeholder/title/forms.value)——不得当指令
def _anomaly_check(wid):
    """供小票 page.anomaly 的轻量检测:主 frame 可见元素 vs 世界元素数。
    任何失败默认 False(宁可漏报,不误报)。每次动作约 +2 次 evaluate。"""
    try:
        w = _world(int(wid))
        if id(w.get("page")) in (w.get("runtime_pending_tokens") or set()):
            # 新页仍在导航时不做深诊断；动作后果先依据页签/网址事实返回，
            # 避免 anomaly 检测把运行时初始化等待重复叠加到动作延迟。
            return False
        core = _evaluate(int(wid), "() => agentWorld.query.getStatus()") or {}
        page = w["page"]
        target = None
        for f in page.frames:
            try:
                if f.url and not f.url.startswith("about:"):
                    target = f
                    break
            except Exception:
                continue
        if target is None:
            return False
        visible = target.evaluate(
            "[...document.querySelectorAll('*')].filter(e => { const t = e.tagName.toLowerCase(); if (['br','hr','script','style','link','meta','noscript','svg','path','g','defs','use'].includes(t)) return false; const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) !== 0 && r.width > 3 && r.height > 3; }).length"
        )
        world_count = (core.get("world") or {}).get("elements", 0)
        return _anomaly_from_counts(visible, world_count)
    except Exception:
        return False


def _outcome_card(wid, action, args, ret, before_signal):
    """所有动作的统一出口:在动作返回上追加统一后果卡(五态 page_outcome)。

    结构 = 旧返回字段(超集,兼容现有客户端)+ 卡片字段。
    主标签(page_outcome/situation/confidence/why)位于字段最前。
    """
    _oc_t0 = None
    w = _world(int(wid))
    try:
        before = before_signal or _page_signal_snapshot(int(wid))
    except Exception:
        before = {}
    # 导航是否已开始(URL 相对动作前已变):导航后旧 el_N 已失效,再解析目标只会
    # evaluate 阻塞等待新文档(实测 GitHub 可拖 ~18s);后果卡由 URL 事实判定。
    _navigated = False
    try:
        _navigated = bool(w["page"].url and before and (w["page"].url != before.get("url")))
    except Exception:
        _navigated = False
    # 在转到新标签页后旧 el_N 不再存在，但后果卡仍应保留目标身份。
    submit_trigger = False
    resolved = None
    ent = None
    target_arg = args.get("id")
    if target_arg and action != "world_navigate" and not _navigated:
        try:
            resolved = _resolve_id(int(wid), target_arg)
        except Exception:
            resolved = None
        if resolved:
            try:
                ent = _evaluate(int(wid), "(id) => agentWorld.query.getEntity(id)", resolved)
            except Exception:
                ent = None
    if action == "world_click" and resolved:
        submit_trigger = _is_submit_trigger(int(wid), resolved)
    elif action == "world_press" and resolved:
        submit_trigger = _is_submit_trigger(int(wid), resolved, args.get("key"))
    # 只有“无 href 的可交互链接/卡片”有较高概率通过 JavaScript window.open，
    # 对这类目标给一个很短的发现窗口；普通按钮/填写动作不增加等待。
    page_poll_ms = 0
    if action in ("world_click", "world_press") and isinstance(ent, dict):
        attrs = ent.get("attributes") or {}
        if (ent.get("semantic") == "link" and not attrs.get("href")) or str(ent.get("name") or "").startswith("link."):
                # window.open 的 page 事件可能晚于点击返回；卡片专属窗口稍微
                # 放宽到 800ms，确保动作后果卡先于后续 world_state 看见新页。
                page_poll_ms = 800
    new_pages = _activate_new_page(int(wid), args.get("_before_page_tokens"), poll_ms=page_poll_ms)
    try:
        # 导航已开始(URL 相对动作前已变)时用轻量 after 快照:不等新文档 evaluate
        # (GitHub 实测导航期间 evaluate 可阻塞 ~20s,动作反馈被拖慢;URL 变化是导航强证据)
        fast_after = False
        try:
            fast_after = (w["page"].url != (before or {}).get("url"))
        except Exception:
            fast_after = False
        after = _page_signal_snapshot(int(wid), fast=fast_after)
    except Exception:
        after = {}
    # 保留旧 feedback/全局纠正逻辑(URL/弹窗覆盖局部判定)
    ret = _finalize_click_result(int(wid), ret, before, after)
    if new_pages:
        ret.setdefault("feedback", {})["pages"] = {
            "new": new_pages,
            "active_url": after.get("url"),
            "active_page": next((x.get("page_id") for x in new_pages if x.get("active")), None),
        }

    fb = ret.get("feedback") or {}
    page_fb = fb.get("page") or {}
    ov_fb = fb.get("overlays") or {}
    url_changed = bool(page_fb.get("url_changed"))
    new_overlays = ov_fb.get("new") or []
    gone_overlays = ov_fb.get("gone") or []
    effect = ret.get("effect") or {}
    # P1:证据管线缺席兜底。快照缺失等竞态下 effect 为空,以往会一路掉进 unchanged(把未知判成失败);
    # 现合成 unknown 证据,由 _build_page_outcome 映射为 uncertain(复核一次,而非当失败)。
    if not isinstance(effect, dict) or not effect.get("verdict"):
        effect = {"verdict": "unknown", "confidence": "low",
                  "why": "证据管线未返回 effect(快照缺失或采集中断),生效未知",
                  "observed": []}
        ret["effect"] = effect

    check_errors = action in ("world_fill", "world_batch_fill", "world_press") or submit_trigger
    page_outcome, situation, confidence, why = _build_page_outcome(
        int(wid), before, after,
        submit_trigger=submit_trigger, effect=effect,
        overlays_changed=bool(new_overlays or gone_overlays),
        check_errors=check_errors,
    )

    # Phase 3 遮挡归因:unchanged + 目标被遮挡 → why 并入归因,消除"含糊的 unchanged"
    occlusion = ret.get("occlusion")
    if page_outcome == "unchanged" and occlusion and occlusion.get("covered"):
        occl_hint = ret.get("obscured_note") or ""
        if occl_hint:
            why = f"{why};{occl_hint}"
        by = occlusion.get("covered_by") or {}
        situation = {"type": "occluded", "to_url": None,
                     "covered_by": by, "at": occlusion.get("at") or [],
                     "action": occlusion.get("action")}

    # 目标身份(最佳努力;导航后旧 el_N 全部失效,target.id 置空,只留 URL)
    if action == "world_navigate":
        target = {"id": None, "name": str(args.get("url", ""))[:300], "fingerprint": None}
    else:
        target = {
            "id": (ent or {}).get("id"),
            "name": (ent or {}).get("name"),
            "fingerprint": (ent or {}).get("fingerprint") if ent else None,
        }

    # 导览失效判定:全局事实变了,旧导览不可信
    guide_stale = bool(url_changed or new_overlays or gone_overlays or page_fb.get("title_changed"))
    # P2a:page.anomaly 接真信号(与状态卡同口径的轻量检测),异常安全默认 False。
    try:
        _anomaly = _anomaly_check(int(wid))
    except Exception:
        _anomaly = False

    # 阶段 C: 自愈处方 (recipes) 与 人机交接 (handoff) 协议
    handoff = None
    recipes = []
    if page_outcome == "challenged":
        handoff = {
            "required": True,
            "type": "human_challenge",
            "reason": why,
            "suggested": "页面触发人机验证或固定遮罩,请通知用户在可见窗口协助完成",
            "resume_condition": "challenge_cleared",
        }
        next_suggested = "页面被挑战遮罩/验证墙拦截,请暂停自动推进并转交人工处理"
    elif page_outcome == "unchanged":
        # 探测是否受活动弹窗/遮罩阻挡
        dialogs = (after.get("dialogs") if after else None) or []
        if not dialogs:
            try:
                core_status = _evaluate(int(wid), "() => agentWorld.query.getStatus()") or {}
                dialogs = core_status.get("dialogs") or []
            except Exception:
                dialogs = []
        if dialogs:
            d_id = dialogs[0].get("id") or target_arg
            d_name = dialogs[0].get("name") or d_id or "活动弹窗"
            recipes = [
                {"action": "world_act", "kind": "press", "id": d_id, "key": "Escape", "why": f"当前存在未关闭的活动弹窗({d_name}),优先按 Escape 退出"},
                {"action": "world_find", "q": "关闭", "why": "寻找弹窗内的关闭按钮并点击"},
            ]
            next_suggested = f"检测到存在活动弹窗({d_name}),当前操作未生效可能受其阻挡,建议按 Escape 或先关闭弹窗"
        else:
            # R4:纯 unchanged 也必须给机器可读恢复出口(换目标/重导览),不得留空
            next_suggested = ("重新调用 world_guide" if guide_stale
                              else "当前操作未生效,换目标或重新调用 world_guide(同一目标不得重复硬点)")
    elif page_outcome == "errored" and situation.get("type") in {"network_error", "network_application_error"}:
        err_info = (situation.get("errors") or [{}])[0]
        status_code = err_info.get("status", "")
        if situation.get("type") == "network_application_error":
            next_suggested = f"操作引发后端业务错误(HTTP {status_code}),请根据业务错误修正参数或换路径,无需原地盲目重复提交"
        else:
            next_suggested = f"操作引发后端接口报错(HTTP {status_code}),请根据报错修正参数或换路径,无需原地盲目重复提交"
    elif page_outcome == "errored" and situation.get("type") == "console_error":
        next_suggested = "操作引发前端控制台异常,请检查输入合法性或重新导览"
    else:
        next_suggested = "重新调用 world_guide" if guide_stale else None

    card_data = {
        "world_id": int(wid),
        "channel": "outcome",
        "page_outcome": page_outcome,
        "situation": situation,
        "confidence": confidence,
        "why": why,
        "target": target,
        "action": {"kind": action.split("_", 1)[1], "via": "self"},
        "page": {
            "before_url": before.get("url"),
            "after_url": after.get("url") or before.get("url"),
            "url_changed": url_changed,
            "state": after.get("state", "unknown"),
            "anomaly": _anomaly,
        },
        "overlays": {"new": new_overlays[:8], "gone": gone_overlays[:8]},
        "next": {
            "guide_stale": guide_stale,
            "suggested": next_suggested,
            "candidates": (occlusion.get("candidates") or []) if (occlusion and occlusion.get("covered")) else [],
        },
        "evidence_seq": int(w.get("evidence_seq", 0)) + 1,
        "changes_seq": {"before": before.get("changes_seq", 0), "after": after.get("changes_seq", 0)},
        "world_epoch": int(w.get("epoch", 0)),
    }
    if situation.get("type") in ("network_error", "network_application_error", "console_error") and "errors" in situation:
        card_data["errors"] = situation["errors"]
    if handoff:
        card_data["handoff"] = handoff
    if recipes:
        card_data["recipes"] = recipes

    ret.update(card_data)
    # F2 来源标记:白名单字段打标签,页面自由文本(如 target.name)标 untrusted
    ret["sources"] = _sources_for_card(ret)
    return _ok(ret)


def _errored_card(wid, action, args, before_signal, exc):
    """动作执行异常 → 统一后果卡 page_outcome=errored(结构化返回,保留错误信息)。"""
    w = _world(int(wid))
    try:
        before = before_signal or _page_signal_snapshot(int(wid))
    except Exception:
        before = {}
    try:
        after = _page_signal_snapshot(int(wid))
    except Exception:
        after = {}
    # P0-1:errored 卡也消耗一个序号。不 mint 的话,它的序号会与上一张成功卡重复,
    # world_outcome(since=上一序号) 将返回 none,把这张 errored 藏掉(对账黑洞)。
    try:
        after = _page_signal_snapshot(int(wid))
    except Exception:
        after = {}
    # P0-1 续:mint 序号(独立 try,与快照无关)。不 mint 则与上一张成功卡同号,对账黑洞。
    try:
        w["evidence_seq"] = int(w.get("evidence_seq", 0)) + 1
    except Exception:
        pass
    # P2b:异常真信号(与 _outcome_card 同口径),异常安全。
    try:
        _err_anomaly = _anomaly_check(int(wid))
    except Exception:
        _err_anomaly = False
    _err_card = {
        "world_id": int(wid),
        "channel": "outcome",
        "page_outcome": "errored",
        "situation": {"type": "error", "to_url": None},
        "confidence": "high",
        "why": "动作执行抛异常,未获得生效判定(可能已部分生效)",
        "target": {"id": args.get("id"), "name": None, "fingerprint": None},
        "action": {"kind": action.split("_", 1)[1] if action.startswith("world_") else action, "via": "self"},
        # P2b:errored 不是无证据,而是"未评估"。verdict 用 unevaluated(与 unknown/changed 并列第三态),
        # 映射仍为 errored(异常本身即结论),效果字段诚实声明未评估而非留空。
        "effect": {"verdict": "unevaluated", "confidence": "high",
                   "why": "动作抛异常,效果未评估(见 error 字段)", "observed": []},
        "page": {
            "before_url": before.get("url"),
            "after_url": after.get("url") or before.get("url"),
            "url_changed": bool(before.get("url") and before.get("url") != (after.get("url") or before.get("url"))),
            "state": after.get("state", "unknown"),
            "anomaly": _err_anomaly,
        },
        "overlays": {"new": [], "gone": []},
        "sources": {},
        "next": {"guide_stale": False, "suggested": None, "candidates": []},
        "evidence_seq": int(w.get("evidence_seq", 0)),
        "changes_seq": {"before": before.get("changes_seq", 0), "after": after.get("changes_seq", 0)},
        "world_epoch": int(w.get("epoch", 0)),
        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
    }
    # P2b:空壳填实——对真卡打来源标记(error 字段已纳入白名单)。
    try:
        _err_card["sources"] = _sources_for_card(_err_card)
    except Exception:
        pass
    # 异常动作也必须进入轨迹,否则失败分支会从任务图中消失。
    error_trace = None
    try:
        if args.get("task_id"):
            w["task_id"] = str(args.get("task_id"))[:120]
        w["trace_step_seq"] = int(w.get("trace_step_seq", 0)) + 1
        error_trace = build_trace_entry(
            trace_id=str(w.get("trace_id") or new_id("trace")),
            task_id=str(w.get("task_id") or new_id("task")),
            step_index=int(w["trace_step_seq"]),
            action=action,
            args=args,
            before=before,
            after=after,
            payload=_err_card,
            evidence_seq=int(w.get("evidence_seq", 0)),
            world_epoch=int(w.get("epoch", 0)),
            context=_runtime_context(w),
        )
        attach_business_runtime(
            error_trace,
            w.get("business_state_rules"),
            w.get("operation_contracts"),
        )
        w.setdefault("trace_log", []).append(error_trace)
        if len(w["trace_log"]) > 200:
            del w["trace_log"][:-200]
    except Exception:
        pass
    if persistence_enabled() and error_trace is not None:
        try:
            _trace_store.append(str(w.get("task_id") or ""), error_trace)
        except Exception:
            pass
    return _ok(_err_card)


def _region_snapshot_at(wid, x, y):
    """坐标点击的生效证据基线:以 (x,y) 为中心 ±200px 构造区域(不依赖构件编号)。

    返回结构与 _click_region_snapshot 相同(region/rows/dialogs/target),复用同一套证据窗。
    """
    raw = _evaluate(
        wid,
        """(p) => {
            const pad = 200;
            const reg = { x0: p.x - pad, y0: p.y - pad, x1: p.x + pad, y1: p.y + pad };
            const rows = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                const ex = e.bounds.x, ey = e.bounds.y;
                if (ex + e.bounds.w > reg.x0 && ex < reg.x1 && ey + e.bounds.h > reg.y0 && ey < reg.y1) {
                    rows.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            const dialogs = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                if (e.semantic === 'dialog' || e.semantic === 'alertdialog' || e.semantic === 'menu') {
                    if (e.inViewport) dialogs.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            let target = null;
            const top = document.elementFromPoint(p.x, p.y);
            if (top) {
                target = {
                    page_id: (top.getAttribute && top.getAttribute('id')) || '',
                    state: {
                        ariaSelected: top.getAttribute('aria-selected'),
                        ariaExpanded: top.getAttribute('aria-expanded'),
                        checked: typeof top.checked === 'boolean' ? top.checked : top.hasAttribute('checked'),
                        className: (top.className && top.className.baseVal !== undefined ? top.className.baseVal : top.className) || ''
                    }
                };
            }
            return JSON.stringify({ region: reg, rows, dialogs, target });
        }""",
        {"x": x, "y": y},
    )
    if not raw:
        return None
    return json.loads(raw)


# L2 样式快照层:区域元素计算样式属性表。DOM 行 diff 与目标状态都哑火时,
# 先比计算样式(结构化、可解释、免截图),再落到像素兜底(L4)。
def _region_styles(wid, ids):
    """取一批世界构件的计算样式快照 {id: {prop: value}}。失败返回 {}。"""
    uniq = list(dict.fromkeys(ids))[:STYLE_SNAPSHOT_MAX]
    if not uniq:
        return {}
    try:
        raw = _evaluate(wid, """(payload) => {
            const out = {};
            for (const id of payload.ids) {
                const e = agentWorld._runtime.world.elements.get(id);
                if (!e || !e._el || !e._el.isConnected) continue;
                try {
                    const cs = getComputedStyle(e._el);
                    const m = {};
                    for (const p of payload.props) m[p] = cs[p];
                    out[id] = m;
                } catch (err) {}
            }
            return JSON.stringify(out);
        }""", {"ids": uniq, "props": list(STYLE_DIFF_PROPS)})
    except Exception:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _click_region_snapshot(wid, target_id, capture_frame=False):
    """点击前冻结目标空间区域:以目标 bounds 中心 ±CLICK_REGION_PAD 为矩形。
    返回 (region, rows)——region 是固定坐标,点击后 target 可能消失也用它做 diff。
    附带全页可见 dialog/menu 集合(远距弹窗兜底)与目标自身状态(状态切换兜底,如 tab/折叠/勾选)。
    rows: 区域内构件 [id, semantic, name]
    dialogs: 全页可见 dialog/alertdialog/menu 构件 [id, semantic, name]
    target: {page_id, state} —— state={ariaSelected, ariaExpanded, checked, className}
    capture_frame: 是否额外截取区域截图(供视觉 diff 兜底;默认不截,避免每次操作的开销)
    """
    raw = _evaluate(
        wid,
        """(id) => {
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return null;
            const b = el.bounds;
            const pad = 200;
            const reg = { x0: b.x - pad, y0: b.y - pad, x1: b.x + b.w + pad, y1: b.y + b.h + pad };
            const rows = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                const x = e.bounds.x, y = e.bounds.y;
                if (x + e.bounds.w > reg.x0 && x < reg.x1 && y + e.bounds.h > reg.y0 && y < reg.y1) {
                    rows.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            // 全页可见弹窗/菜单(远距弹窗兜底)
            const dialogs = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                if (e.semantic === 'dialog' || e.semantic === 'alertdialog' || e.semantic === 'menu') {
                    if (e.inViewport) dialogs.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            // 目标自身状态(用页面原生 id 寻 DOM,SPA 重建后依然可读)
            let target = null;
            if (el._el) {
                const page_id = (el.attributes && el.attributes.id) || '';
                const n = page_id ? (document.getElementById(page_id) || el._el) : el._el;
                target = {
                    page_id,
                    state: {
                        ariaSelected: n.getAttribute('aria-selected'),
                        ariaExpanded: n.getAttribute('aria-expanded'),
                        checked: typeof n.checked === 'boolean' ? n.checked : n.hasAttribute('checked'),
                        className: (n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className) || ''
                    }
                };
            }
            return JSON.stringify({ region: reg, rows, dialogs, target });
        }""",
        target_id,
    )
    if not raw:
        return None
    data = json.loads(raw)
    if capture_frame:
        try:
            w = _world(wid)
            reg = data["region"]
            frame_path = SCREENSHOT_DIR / f"frame_before_{wid}_{int(time.time()*1000)}.png"
            w["page"].screenshot(
                path=str(frame_path),
                clip={"x": max(0, reg["x0"]), "y": max(0, reg["y0"]), "width": max(10, reg["x1"] - reg["x0"]), "height": max(10, reg["y1"] - reg["y0"])}
            )
            data["frame_path"] = frame_path
            try:
                data["scroll_y"] = _evaluate(wid, "() => window.scrollY") or 0
            except Exception:
                data["scroll_y"] = 0
            # L2:同拍一张区域计算样式快照(目标优先,最多 STYLE_SNAPSHOT_MAX 个)
            try:
                _sids = [target_id] + [r[0] for r in (data.get("rows") or [])]
                data["styles_before"] = _region_styles(wid, _sids)
            except Exception:
                data["styles_before"] = {}
        except Exception:
            pass
    return data


def _click_region_after(wid, region, page_id=None):
    """点击后用冻结区域取当前构件(不依赖目标是否仍存在)。
    附带全页可见 dialog/menu 集合(远距弹窗兜底)与目标自身状态(状态切换兜底)。
    """
    raw = _evaluate(
        wid,
        """(arg) => {
            const reg = arg.region, page_id = arg.page_id;
            const rows = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                const x = e.bounds.x, y = e.bounds.y;
                if (x + e.bounds.w > reg.x0 && x < reg.x1 && y + e.bounds.h > reg.y0 && y < reg.y1) {
                    rows.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            const dialogs = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                if (e.semantic === 'dialog' || e.semantic === 'alertdialog' || e.semantic === 'menu') {
                    if (e.inViewport) dialogs.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            // 目标自身状态(用页面原生 id 寻 DOM,SPA 重建后依然可读)
            let target_state = null;
            if (page_id) {
                const n = document.getElementById(page_id);
                if (n) {
                    target_state = {
                        ariaSelected: n.getAttribute('aria-selected'),
                        ariaExpanded: n.getAttribute('aria-expanded'),
                        checked: typeof n.checked === 'boolean' ? n.checked : n.hasAttribute('checked'),
                        className: (n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className) || ''
                    };
                }
            }
            return JSON.stringify({ rows, dialogs, target_state });
        }""",
        {"region": region, "page_id": page_id},
    )
    if not raw:
        return [], [], None
    data = json.loads(raw)
    return data.get("rows", []), data.get("dialogs", []), data.get("target_state")


def _wait_click_effect(wid, snap_before, url_before, max_wait_ms=2500, disappear_ok=False, fill_verified=False):
    """点击后轮询目标区域:有变化即停(不等满),最多 max_wait_ms。
    同时采集全页可见 dialog 集合(远距弹窗/关弹窗兜底)与目标自身状态(状态切换兜底)。
    返回 effect 报告;捕获失败时返回 None(调用方省略字段)。
    """
    if not snap_before:
        return None
    region = snap_before["region"]
    before_rows = snap_before.get("rows", [])
    before_dialogs = snap_before.get("dialogs", [])
    before_target = snap_before.get("target") or {}
    page_id = before_target.get("page_id") or None
    before_target_state = before_target.get("state")
    w = _world(wid)
    deadline = time.time() + max_wait_ms / 1000
    last_rows = None
    last_dialogs = None
    last_target_state = None
    last_seen = 0
    # 证据窗计时埋点(用于评估轮询式证据窗的收益/浪费)
    t_start = time.time()
    poll_count = 0
    first_change_at = None
    stop_reason = "timeout"
    while time.time() < deadline:
        time.sleep(0.2)
        poll_count += 1
        # 导航开始后旧文档的执行上下文会立即销毁；先读取 Playwright 的 URL
        # 事实，避免继续 evaluate 旧 DOM 而等待新页面加载，导致动作反馈被
        # 外部店铺的慢加载拖住。URL 变化本身就是导航类强证据。
        try:
            current_url = w["page"].url
        except Exception:
            current_url = ""
        if current_url and current_url != url_before:
            return {
                "verdict": "effected",
                "confidence": "high",
                "why": "URL 变化(导航/提交类)",
                "observed": [],
                "region_changed": {"new": 0, "gone": 0},
                "evidence": {
                    "polls": poll_count,
                    "total_ms": int((time.time() - t_start) * 1000),
                    "first_change_ms": int((time.time() - t_start) * 1000),
                    "stop": "url-change",
                },
            }
        try:
            rows, dialogs, target_state = _click_region_after(wid, region, page_id)
        except Exception:
            rows, dialogs, target_state = [], [], None
        if (rows, dialogs, target_state) != (last_rows, last_dialogs, last_target_state):
            last_rows, last_dialogs, last_target_state = rows, dialogs, target_state
            last_seen = time.time()
            if first_change_at is None:
                first_change_at = time.time() - t_start
            # 聪明早停:已看到"决定性证据"(弹窗出现/URL变/状态翻转/关键构件/值进框)就直接返回,
            # 不必再等 0.4s 稳定——弹窗都弹出来了,等稳定是白等(对持续变化页收益最大)
            if rows:
                early = _build_click_effect(before_rows, rows, w["page"].url != url_before,
                                            before_dialogs, dialogs,
                                            before_target_state, target_state,
                                            disappear_ok, fill_verified)
                if early["verdict"] == "effected":
                    early["evidence"] = {
                        "polls": poll_count,
                        "total_ms": int((time.time() - t_start) * 1000),
                        "first_change_ms": int((first_change_at or 0) * 1000),
                        "stop": "early-effect",
                    }
                    return early
        # 区域稳定(0.4s 无变化)且距首次观察足够(让重渲染完成)即停
        if rows and (time.time() - last_seen > 0.4) and (time.time() - last_seen < 5):
            stop_reason = "stable"
            break
    total_ms = int((time.time() - t_start) * 1000)
    url_changed = w["page"].url != url_before
    if last_rows is None:
        try:
            last_rows, last_dialogs, last_target_state = _click_region_after(wid, region, page_id)
        except Exception:
            last_rows, last_dialogs, last_target_state = [], [], None
    effect = _build_click_effect(before_rows, last_rows or [], url_changed,
                                 before_dialogs, last_dialogs or [],
                                 before_target_state, last_target_state,
                                 disappear_ok, fill_verified)
    
    # 视觉双轨兜底: 若 DOM 结构无变化(no-change), 取前后局部帧计算 RMS 像素差异, 捕捉纯 CSS 动效/浮层/颜色切换
    if effect.get("verdict") == "no-change" and snap_before.get("frame_path"):
        try:
            # L2 样式层:先比计算样式(结构化、可解释、免截图)。命中则直接生效,不走像素。
            # 只比双端都在的元素;消失/新增归 DOM 侧管,这里跳过。
            _styles_hit = False
            _sb = snap_before.get("styles_before")
            if _sb:
                try:
                    # 波动基线:转菊花这类持续动画每帧都变,必须先排除,
                    # 否则任何含动画邻居的区域都会误报。after 连采两次,之间在变即噪声。
                    _sa1 = _region_styles(wid, list(_sb.keys()))
                    try:
                        time.sleep(0.15)
                    except Exception:
                        pass
                    _sa2 = _region_styles(wid, list(_sb.keys()))
                    _volatile = set()
                    for _sid in _sa1:
                        _m1, _m2 = _sa1.get(_sid) or {}, _sa2.get(_sid) or {}
                        for _p in _m1:
                            if _m1.get(_p) != _m2.get(_p):
                                _volatile.add((_sid, _p))
                    _diffs = []
                    for _sid, _bm in _sb.items():
                        _am = _sa2.get(_sid)
                        if not _am:
                            continue
                        for _p, _bv in _bm.items():
                            if (_sid, _p) in _volatile:
                                continue
                            if _am.get(_p) != _bv:
                                _diffs.append({"id": _sid, "prop": _p,
                                               "before": str(_bv)[:120],
                                               "after": str(_am.get(_p))[:120]})
                            if len(_diffs) >= 8:
                                break
                        if len(_diffs) >= 8:
                            break
                    if _diffs:
                        _first = _diffs[0]
                        effect["verdict"] = "visual-effected"
                        effect["confidence"] = "high"
                        effect["why"] = (f"区域元素计算样式变化({len(_diffs)}处,"
                                         f"如{_first['id']}.{_first['prop']}:"
                                         f"{_first['before']}→{_first['after']})")
                        effect["style_changes"] = _diffs
                        effect["visual_path"] = "style-diff"
                        _styles_hit = True
                except Exception:
                    pass
            # P0-2 scroll-shift 护栏:点击导致页面滚动时,固定坐标区域前后帧必然错位,
            # 此时 RMS 再大也不能判生效(实测滚动 216px 产生 RMS 28.9,淹没真信号)。
            try:
                y_now = _evaluate(wid, "() => window.scrollY") or 0
            except Exception:
                y_now = 0
            y_before = snap_before.get("scroll_y", y_now)
            if _styles_hit:
                pass  # 已命中样式层,跳过像素兜底
            elif abs((y_now or 0) - (y_before or 0)) > 2:
                effect["visual_skipped"] = "scroll-shift"
                effect["why"] = (effect.get("why") or "") + \
                    "(动作前后页面发生滚动,区域前后帧错位,视觉比对作废)"
            else:
                b_path = snap_before["frame_path"]
                a_path = SCREENSHOT_DIR / f"frame_after_{wid}_{int(time.time()*1000)}.png"
                reg = snap_before["region"]
                w["page"].screenshot(
                    path=str(a_path),
                    clip={"x": max(0, reg["x0"]), "y": max(0, reg["y0"]), "width": max(10, reg["x1"] - reg["x0"]), "height": max(10, reg["y1"] - reg["y0"])}
                )
                i1 = Image.open(b_path).convert("RGB")
                i2 = Image.open(a_path).convert("RGB")
                diff = ImageChops.difference(i1, i2)
                stat = ImageStat.Stat(diff)
                diff_rms = math.sqrt(sum(stat.sum2) / (i1.size[0] * i1.size[1] * 3))
                # P0-2:原始分永远记录(阈值校准与审计用),判定走 VISUAL_RMS_THRESHOLD。
                effect["visual_diff_raw"] = round(diff_rms, 2)
                effect["visual_path"] = "pixel"
                if diff_rms > VISUAL_RMS_THRESHOLD:
                    effect["verdict"] = "visual-effected"
                    effect["confidence"] = "high"
                    effect["why"] = f"检测到目标区域发生显著视觉状态或浮层变化 (RMS={round(diff_rms, 2)})"
                    effect["visual_diff_score"] = round(diff_rms, 2)
        except Exception:
            pass

    effect["evidence"] = {
        "polls": poll_count,
        "total_ms": total_ms,
        "first_change_ms": int((first_change_at or 0) * 1000),
        "stop": stop_reason,
    }
    return effect


def _occlusion_probe(wid, target_id=None, x=None, y=None):
    """遮挡归因探测(Phase 3):elementFromPoint 单点检查。

    目标模式(target_id):检查元素中心点是否被上层元素遮挡。
    坐标模式(x/y,click_at 用):报告坐标处顶层元素,遮罩类(role=dialog/menu 或
    backdrop/overlay/modal class)标记 covered。
    返回 None 或 {covered, covered_by:{tag,role,id,cls}, at:[x,y], target_tag}。
    本函数只检测与归因,不改变任何动作行为。
    """
    try:
        data = _evaluate(
            wid,
            """(arg) => {
                let el = null, cx = null, cy = null;
                if (arg.id !== null && arg.id !== undefined && arg.id !== '') {
                    el = agentWorld._runtime.world.elements.get(arg.id);
                    if (!el || !el._el) return null;
                    el._el.scrollIntoView({ block: 'center', inline: 'center' });
                    const r = el._el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return { covered: false, hidden: true };
                    cx = Math.round(r.x + r.width / 2); cy = Math.round(r.y + r.height / 2);
                } else {
                    cx = Math.round(arg.x); cy = Math.round(arg.y);
                }
                const top = document.elementFromPoint(cx, cy);
                if (!top) return { covered: false };
                const tag = top.tagName.toLowerCase();
                const cls = (top.className && typeof top.className === 'string')
                    ? top.className.split(/\\s+/).slice(0, 3).join(' ') : '';
                const role = top.getAttribute('role') || '';
                let covered;
                if (arg.id !== null && arg.id !== undefined && arg.id !== '') {
                    covered = Boolean(el && top !== el._el && !el._el.contains(top));
                } else {
                    covered = ['dialog', 'alertdialog', 'menu'].includes(role)
                        || /backdrop|overlay|modal/i.test(cls);
                }
                let candidates = [];
                if (covered && top) {
                    try {
                        const btns = top.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]');
                        for (let b of Array.from(btns).slice(0, 3)) {
                            const bText = (b.innerText || b.getAttribute('aria-label') || b.getAttribute('title') || b.value || '').trim();
                            const bId = b.getAttribute('id') || '';
                            candidates.push({
                                tag: b.tagName.toLowerCase(),
                                id: bId,
                                text: bText.slice(0, 40),
                                reason: '遮挡层内部可交互入口(可尝试点击以关闭或完成验证)'
                            });
                        }
                    } catch (e) {}
                }
                return {
                    covered: covered,
                    covered_by: { tag, role, id: top.id || '', cls },
                    at: [cx, cy],
                    target_tag: el && el._el ? el._el.tagName.toLowerCase() : null,
                    candidates: candidates,
                };
            }""",
            {"id": target_id, "x": x, "y": y},
        )
        if not data or data.get("hidden"):
            return None
        return data
    except Exception:
        return None


def _occlusion_attach(ret, probe):
    """把遮挡归因挂到动作返回:结构化 occlusion 字段 + 兼容旧 obscured_note。"""
    if not probe or not probe.get("covered"):
        return
    by = probe.get("covered_by") or {}
    label = f"<{by.get('tag') or '?'}"
    if by.get("role"):
        label += f" role={by['role']}"
    if by.get("id"):
        label += f" id={by['id']}"
    if by.get("cls"):
        label += f" class={by['cls']}"
    label += ">"
    at = probe.get("at") or []
    if by.get("role") in ("dialog", "alertdialog", "menu"):
        action = "先关闭/操作该弹窗,再重试动作"
    else:
        action = "先处理该遮挡元素,再重试动作"
    ret["occlusion"] = {
        "covered": True,
        "covered_by": by,
        "at": at,
        "action": action,
    }
    if probe.get("candidates"):
        ret["occlusion"]["candidates"] = probe["candidates"]
    ret["obscured_note"] = f"目标被 {label} 遮挡于 {at}:{action}"


def _t_world_click(args, before_signal=None):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")

    # 视觉证据开关:显式要求时才截前后帧(视觉 diff 兜底),避免每次操作的开销
    visual_evidence = bool(args.get("visual_evidence", False))

    # 点击前:冻结目标空间区域(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target, capture_frame=visual_evidence)
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    url_before = before_signal["url"]

    # 遮挡归因:检查元素中心点是否被上层弹窗/遮罩层挡住(结构化 covered_by/at/action,不改变点击行为)
    occl_probe = _occlusion_probe(wid, target_id=target)

    attrs = ent.get("attributes") or {}
    href = str(attrs.get("href") or "")
    # 外部转链入口已经有真实 href 时，直接发起导航比在可能已换文档的
    # 转链页上等待 locator.click 更可靠；wait_until=commit 只等导航开始，
    # 后续结果仍由统一后果卡和 URL 事实确认。
    try:
        parsed_href = urlsplit(href)
    except Exception:
        parsed_href = None
    if (ent.get("semantic") == "link" and parsed_href and parsed_href.scheme in ("http", "https")
            and parsed_href.netloc and "nodebits.xyz" not in parsed_href.netloc):
        try:
            w["page"].goto(href, wait_until="commit", timeout=5000)
        except Exception:
            # 外部站点可能连接超时，但 URL/导航请求已经发出；交给后果卡
            # 判断当前页面事实，不再回退到旧页的重复点击。
            pass
        ret = {"world_id": wid, "clicked": target, "method": "external-navigation", "href": href}
        _occlusion_attach(ret, occl_probe)
        effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1200)
        if effect:
            ret["effect"] = effect
        return _outcome_card(wid, "world_click", args, ret, before_signal)
    card_like = (ent.get("semantic") == "link" and not attrs.get("href")
                 and "card-shine" in str(attrs.get("className") or ""))
    if card_like:
        baseline = args.get("_before_page_tokens") or _known_page_tokens(wid)
        # 动态聚合页可能先把卡片绘制出来，再绑定 React 点击事件；只在
        # 动作前状态仍为 loading 时短等，避免“看得见但点不动”。普通稳定页
        # 不增加等待，且超过上限仍继续一次点击并由后果卡判定。
        if (before_signal or {}).get("state") == "loading":
            ready_deadline = time.time() + 1.2
            while time.time() < ready_deadline:
                try:
                    if _page_signal_snapshot(wid).get("state") == "stable":
                        break
                except Exception:
                    pass
                time.sleep(0.1)
        # 动态列表会在查询与点击之间重绘；先按当前文本重新解析顶层卡片，
        # 避免直接使用已经脱离文档的旧 DOM 引用。
        card_loc = _build_locator(w, ent)
        if card_loc:
            try:
                # 使用真实鼠标点击让前端事件委托有机会完成绑定；locator 会在
                # 动作时重新解析当前节点，且本分支只触发一次点击。
                _click_locator_nowait(card_loc, timeout=5000)
                _refresh_core_status(wid)
                card_ret = {"world_id": wid, "clicked": target, "method": "card-locator"}
                _occlusion_attach(card_ret, occl_probe)
                card_effect = _wait_click_effect(wid, snap_before, url_before)
                if card_effect:
                    card_ret["effect"] = card_effect
                card_outcome = _outcome_card(wid, "world_click", args, card_ret, before_signal)
                # 定位点击已成功发出后，无论证据窗是否立即看到跳转，都不再
                # 用旧 DOM 进行第二次点击；否则动态转链可能打开两个店铺页。
                return card_outcome
            except Exception as exc:
                # 点击期间页面可能已经成功打开新标签页，但后续的旧页证据
                # 读取因重绘/导航抛错。只要检测到新页或 URL 已变化，就不要
                # 再拿旧卡片编号走鼠标兜底；直接用统一出口收口为导航结果。
                try:
                    navigated = _has_new_page(wid, baseline) or (w["page"].url != url_before)
                except Exception:
                    navigated = False
                if navigated:
                    ret = {"world_id": wid, "clicked": target, "method": "card-locator", "warning": str(exc)[:200]}
                    try:
                        return _outcome_card(wid, "world_click", args, ret, before_signal)
                    except Exception:
                        pass
        # 这类卡片没有可访问网址，且动态重绘会让坐标/长文本 locator 漂移；
        # 直接对运行时已确认的外层 DOM 节点触发 React 点击处理器。
        # 仅限无 href 的 card-shine，普通按钮/链接仍走下方 Playwright 路径。
        try:
            dom_clicked = _evaluate(wid, """(id) => {
                const e = agentWorld._runtime.world.elements.get(id);
                if (!e || !e._el || !e._el.isConnected) return false;
                e._el.click();
                return true;
            }""", target)
            if not dom_clicked:
                raise RuntimeError("卡片已被动态重绘，旧构件编号失效")
            _refresh_core_status(wid)
            ret = {"world_id": wid, "clicked": target, "method": "dom-card-click"}
            _occlusion_attach(ret, occl_probe)
            effect = _wait_click_effect(wid, snap_before, url_before)
            if effect:
                ret["effect"] = effect
            # DOM 点击已经真实发出后，不再退回 locator/鼠标再次点击：动态转链
            # 可能只是尚未把新页暴露给 Playwright，重复点击会造成双开、长等待，
            # 甚至把第二家店的结果误归因给第一家。统一交给后果卡判定；若暂时
            # 没有跳转，返回 unchanged/uncertain，由上层停止并重新探索。
            return _outcome_card(wid, "world_click", args, ret, before_signal)
        except Exception:
            # DOM click 失败时继续走原有安全降级。
            pass

    loc = _build_locator(w, ent)
    if loc:
        try:
            # Playwright locator:自动等待可见/稳定/可点击,错误信息清晰
            _click_locator_nowait(loc, timeout=10000)
            _refresh_core_status(wid)
            ret = {"world_id": wid, "clicked": target, "method": "locator"}
            _occlusion_attach(ret, occl_probe)
            effect = _wait_click_effect(wid, snap_before, url_before)
            if effect:
                ret["effect"] = effect
            return _outcome_card(wid, "world_click", args, ret, before_signal)
        except Exception as e:
            loc_err = f"{type(e).__name__}: {str(e)[:200]}"
    else:
        loc_err = "no-locator"
    # 兜底:坐标鼠标手势(原生网页世界实时 rect + scrollIntoView)
    rect = _evaluate(
        wid,
        """(id) => {
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return null;
            el._el.scrollIntoView({ block: 'center', inline: 'center' });
            const r = el._el.getBoundingClientRect();
            return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
        }""",
        target,
    )
    if not rect or rect["w"] <= 0 or rect["h"] <= 0:
        raise ValueError(f"构件不可见或不存在: {args['id']} (locator: {loc_err})")
    cx = rect["x"] + rect["w"] // 2
    cy = rect["y"] + rect["h"] // 2
    w["page"].mouse.move(cx, cy)
    w["page"].mouse.down()
    w["page"].mouse.up()
    _refresh_core_status(wid)
    ret = {"world_id": wid, "clicked": target, "method": "mouse-gesture", "at": [cx, cy], "locator_note": loc_err}
    _occlusion_attach(ret, occl_probe)
    effect = _wait_click_effect(wid, snap_before, url_before)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_click", args, ret, before_signal)


def _t_world_fill(args, before_signal=None):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    text = args["text"]
    type_delay_ms = int(args.get("type_delay_ms", 0))
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    visual_evidence = bool(args.get("visual_evidence", False))
    # 填表前:冻结目标空间区域(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target, capture_frame=visual_evidence)
    url_before = w["page"].url
    # 遮挡归因(Phase 3):填表目标被上层元素挡住时结构化报告(不改变行为)
    occl_probe = _occlusion_probe(wid, target_id=target)
    loc = _build_locator(w, ent)
    if loc:
        try:
            if type_delay_ms > 0:
                # 逐字打字:模拟真实键盘输入,触发受控组件/自动联想下拉
                # 缺陷修复(2026-09-02 弱模型验证):press_sequentially 不清空现有值,
                # 二次输入会追加污染("ap"→"apapple")。先 fill("") 清空(React 兼容),
                # 再逐字打字——等价于真人"先清空再输入"。
                try:
                    loc.fill("", timeout=5000)
                except Exception:
                    pass  # 元素本身为空或 fill 清空失败时继续(打字仍可追加)
                loc.press_sequentially(text, delay=type_delay_ms, timeout=10000)
            else:
                # Playwright fill:自动等待 + React 兼容输入 + 清晰错误
                loc.fill(text, timeout=10000)
            # 关键验证:locator 不检测遮挡,SPA 会把值填进被覆盖的旧输入框而"静默成功"。
            # 未在可见输入框验证到文本 → 判定失败,降级到 js-setter(自带覆盖层切换)。
            filled_ok = _fill_visible(wid, text)
            if filled_ok:
                _refresh_core_status(wid)
                method = "locator-sequential-type" if type_delay_ms > 0 else "locator-fill"
                ret = {"world_id": wid, "filled": target, "text": text, "method": method}
                _occlusion_attach(ret, occl_probe)
                effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1500, fill_verified=True)
                if effect:
                    ret["effect"] = effect
                return _outcome_card(wid, "world_fill", args, ret, before_signal)
            fill_err = "fill 后未在可见输入框验证到文本(可能被 SPA 覆盖层拦截)"
        except Exception as e:
            fill_err = f"{type(e).__name__}: {str(e)[:200]}"
    else:
        fill_err = "no-locator"
    # 兜底:JS setter(React 受控组件 + 覆盖层自动切换)
    r = _evaluate(
        wid,
        """(args) => {
            const id = args.id, text = args.text;
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return { ok: false, reason: 'not-found' };
            let node = el._el;
            if (node.tagName !== 'INPUT' && node.tagName !== 'TEXTAREA') {
                node = node.querySelector('input, textarea, [contenteditable="true"]');
                if (!node) return { ok: false, reason: 'no-fillable-child', tag: el._el.tagName };
            }
            // 若目标被上层元素覆盖(如 SPA 的激活态输入框副本),切换到实际可见层
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                const cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2;
                const top = document.elementFromPoint(cx, cy);
                if (top && top !== node && !node.contains(top)) {
                    const topFill = (top.tagName === 'INPUT' || top.tagName === 'TEXTAREA')
                        ? top : top.querySelector('input, textarea, [contenteditable="true"]');
                    if (topFill) node = topFill;
                }
            }
            node.focus();
            const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(node, text);
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
            return { ok: true, tag: node.tagName };
        }""",
        {"id": target, "text": text},
    )
    if not r.get("ok"):
        raise ValueError(f"fill 失败: {r} (locator: {fill_err})")
    _refresh_core_status(wid)
    ret = {"world_id": wid, "filled": target, "text": text, "target_tag": r.get("tag"), "method": "js-setter", "locator_note": fill_err}
    _occlusion_attach(ret, occl_probe)
    # js-setter 兜底路径同样验证"值是否进入可见输入框"作为生效证据
    filled_ok = _fill_visible(wid, text)
    effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1500, fill_verified=filled_ok)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_fill", args, ret, before_signal)


def _t_world_batch_fill(args, before_signal=None):
    """批量填入表单字段:单次 MCP 往返完成多个输入框填写。
    逐字段容错:单个字段失败记录 error 并继续,不中断整个批次。
    返回:旧结构(batch_count/ok_count/results)+ 统一后果卡(聚合判定)。
    """
    wid = args["world_id"]
    fields = args.get("fields") or []
    if not fields:
        raise ValueError("fields 列表不能为空")
    results = []
    for f in fields:
        fid = f.get("id")
        if not fid:
            results.append({"id": None, "ok": False, "error": "缺少 id"})
            continue
        try:
            sub_args = {
                "world_id": wid,
                "id": fid,
                "text": f.get("text", ""),
                "type_delay_ms": int(f.get("type_delay_ms", 0)),
            }
            res = _t_world_fill(sub_args)
            if res and res[0].type == "text":
                data = json.loads(res[0].text)
                results.append({"id": fid, "target": data.get("filled"), "method": data.get("method"), "ok": True})
        except Exception as e:
            results.append({"id": fid, "ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}"})
    _refresh_core_status(wid)
    ok_count = sum(1 for r in results if r.get("ok"))
    ret = {"world_id": wid, "batch_count": len(results), "ok_count": ok_count, "results": results}

    # 统一后果卡(聚合判定):全过→progressed;部分过→uncertain;全败→errored
    w = _world(wid)
    try:
        before = before_signal or _page_signal_snapshot(wid)
    except Exception:
        before = {}
    try:
        after = _page_signal_snapshot(wid)
    except Exception:
        after = {}
    if ok_count == len(results) and len(results) > 0:
        po, conf, wh = "progressed", "high", f"批量填入 {ok_count}/{len(results)} 字段全部成功"
    elif ok_count > 0:
        po, conf, wh = "uncertain", "medium", f"批量填入部分成功({ok_count}/{len(results)} 个字段)"
    else:
        po, conf, wh = "errored", "high", "批量填入全部失败"
    ret.update({
        "channel": "outcome",
        "page_outcome": po,
        "situation": {"type": "form" if po == "progressed" else "none", "to_url": None},
        "confidence": conf,
        "why": wh,
        "target": {"id": [f.get("id") for f in fields], "name": None, "fingerprint": None},
        "action": {"kind": "batch_fill", "via": "self"},
        "effect": {"verdict": "effected" if po == "progressed" else "no-change", "observed": [], "region_changed": {"new": 0, "gone": 0}},
        "page": {
            "before_url": before.get("url"),
            "after_url": after.get("url") or before.get("url"),
            "url_changed": bool(before.get("url") and before.get("url") != (after.get("url") or before.get("url"))),
            "state": after.get("state", "unknown"),
            "anomaly": False,
        },
        "overlays": {"new": [], "gone": []},
        "sources": {},
        "next": {"guide_stale": False, "suggested": None, "candidates": []},
        "evidence_seq": int(w.get("evidence_seq", 0)) + 1,
        "changes_seq": {"before": before.get("changes_seq", 0), "after": after.get("changes_seq", 0)},
        "world_epoch": int(w.get("epoch", 0)),
    })
    # P2a/P2b 同口径(本卡绕过 _outcome_card,在此补齐):异常真信号 + 来源标记填实。
    try:
        ret["page"]["anomaly"] = _anomaly_check(int(wid))
    except Exception:
        pass
    try:
        ret["sources"] = _sources_for_card(ret)
    except Exception:
        pass
    return _ok(ret)


def _t_world_press(args, before_signal=None):
    """按编号聚焦并按按键(如 Enter/Escape/Tab)。返回 effect 生效报告 + 统一后果卡。"""
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    key = args["key"]
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    visual_evidence = bool(args.get("visual_evidence", False))
    # 按键前:冻结目标空间区域 + URL(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target, capture_frame=visual_evidence)
    url_before = w["page"].url
    # 按 key 决定是否开启"弹窗消失"证据(Escape 关弹窗/菜单 = 生效)
    disappear_ok = key.lower() in ("escape", "esc")
    # 遮挡归因(Phase 3):按键目标被上层元素挡住时结构化报告
    occl_probe = _occlusion_probe(wid, target_id=target)
    loc = _build_locator(w, ent)
    if loc:
        try:
            loc.press(key, timeout=10000)
            _refresh_core_status(wid)
            ret = {"world_id": wid, "pressed": target, "key": key, "method": "locator-press"}
            _occlusion_attach(ret, occl_probe)
            effect = _wait_click_effect(wid, snap_before, url_before, disappear_ok=disappear_ok)
            if effect:
                ret["effect"] = effect
            return _outcome_card(wid, "world_press", args, ret, before_signal)
        except Exception as e:
            raise ValueError(f"按键失败: {type(e).__name__}: {str(e)[:200]}")
    # 兜底:JS focus + dispatch keydown/keyup + 真实键盘事件(比纯 JS dispatch 更可靠)
    ok = _evaluate(
        wid,
        """(args) => {
            const el = agentWorld._runtime.world.elements.get(args.id);
            if (!el) return false;
            let node = el._el;
            if (node.tagName !== 'INPUT' && node.tagName !== 'TEXTAREA') {
                const child = node.querySelector('input, textarea, [contenteditable="true"]');
                if (child) node = child;
            }
            node.focus();
            for (const t of ['keydown', 'keyup']) {
                node.dispatchEvent(new KeyboardEvent(t, { key: args.key, bubbles: true }));
            }
            return true;
        }""",
        {"id": target, "key": key},
    )
    if not ok:
        raise ValueError(f"构件不存在: {args['id']}")
    try:
        w["page"].keyboard.press(key)
    except Exception:
        pass
    _refresh_core_status(wid)
    ret = {"world_id": wid, "pressed": target, "key": key, "method": "native-keyboard"}
    _occlusion_attach(ret, occl_probe)
    effect = _wait_click_effect(wid, snap_before, url_before, disappear_ok=disappear_ok)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_press", args, ret, before_signal)


def _t_world_wait(args):
    wid = args["world_id"]
    mode = args["mode"]
    timeout_ms = int(args.get("timeout_ms", 30000))
    f = {}
    if args.get("role"):
        f["role"] = args["role"]
    if args.get("text"):
        f["text"] = args["text"]
    if args.get("name"):
        f["name"] = args["name"]
    w = _world(wid)
    try:
        _activate_new_page(wid, poll_ms=0)
    except Exception:
        pass
    page = w["page"]
    _ensure_page_runtime(page)
    # 事件驱动(替代 0.3s 轮询):内核 waitFor 注册 waiter,
    # MutationObserver flush 命中条件即 resolve;超时由内核 setTimeout 兜底。
    # Playwright evaluate 自动 await Promise;临时放大 page 默认超时,避免
    # timeout_ms > 30s 时被 Playwright 先掐断。
    prev_timeout = 30000
    try:
        page.set_default_timeout(timeout_ms + 3000)
        result = page.evaluate(
            "(a) => agentWorld._runtime.waitFor(a.filter, a.mode, a.timeout_ms)",
            {"filter": f, "mode": mode, "timeout_ms": timeout_ms},
        )
    except Exception as e:
        result = {"matched": False, "mode": mode, "timeout_ms": timeout_ms, "error": str(e)[:120]}
    finally:
        page.set_default_timeout(prev_timeout)
    out = {"world_id": wid, "matched": bool(result.get("matched")), "mode": mode, "filter": f}
    if result.get("matched"):
        out["count"] = result.get("count", 0)
        out["driven"] = "event"
    else:
        out["timeout_ms"] = timeout_ms
        out["driven"] = "timeout"
    return _ok(out)


def _t_world_screenshot(args):
    wid = args["world_id"]
    w = _world(wid)
    annotated = bool(args.get("annotated", False))
    return_base64 = bool(args.get("return_base64", True))
    path = SCREENSHOT_DIR / f"world{wid}_{int(time.time())}.png"
    
    if args.get("id"):
        target = _resolve_id(wid, args["id"])
        ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
        box = ent["bounds"]
        w["page"].screenshot(path=str(path), clip={"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"]})
        desc = f"构件 {target} ({ent['name']})"
    elif annotated:
        # Set-of-Mark 模式: 截取视口并在可交互构件上绘制半透明编号标注框
        raw_path = SCREENSHOT_DIR / f"raw_world{wid}_{int(time.time())}.png"
        w["page"].screenshot(path=str(raw_path), full_page=False)
        ents = _evaluate(wid, "(f) => agentWorld.query.findEntities(f)", {"interactive": True, "inViewport": True}) or []
        
        img = Image.open(raw_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        
        marked = 0
        for ent in ents:
            box = ent.get("bounds", {})
            x, y, bw, bh = box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)
            if bw <= 4 or bh <= 4:
                continue
            eid = ent.get("id", "")
            ename = ent.get("name", "")[:14]
            draw.rectangle([x, y, x + bw, y + bh], outline=(255, 20, 100, 240), width=2)
            label = f"[{eid}] {ename}"
            tag_w = max(36, len(label) * 7 + 6)
            draw.rectangle([x, max(0, y - 16), x + tag_w, max(16, y)], fill=(255, 20, 100, 210))
            draw.text((x + 3, max(0, y - 15)), label, fill=(255, 255, 255, 255))
            marked += 1
            
        combined = Image.alpha_composite(img, overlay).convert("RGB")
        combined.save(path, "PNG")
        desc = f"Set-of-Mark 视口标注图 (标记 {marked} 个可交互构件)"
    else:
        w["page"].screenshot(path=str(path), full_page=True)
        desc = "整页"

    ret_dict = {"world_id": wid, "target": desc, "path": str(path)}
    contents = [types.TextContent(type="text", text=json.dumps(ret_dict, ensure_ascii=False, indent=2))]
    if return_base64 and path.exists():
        with open(path, "rb") as f:
            b64_str = base64.b64encode(f.read()).decode("utf-8")
        contents.append(types.ImageContent(type="image", data=b64_str, mimeType="image/png"))
    return contents


def _t_world_eval(args):
    """世界内 JS 执行(调试/特殊查询,结果截断保护)"""
    wid = args["world_id"]
    expr = args["expression"]
    w = _world(wid)
    # CDP 会话安全闸门:world_eval 是任意 JS,可绕过 visibility 过滤层直接读整页文本,
    # 在 CDP 连接的用户浏览器会话中可能触达登录态/凭据。IPI 攻防实测确认该后门存在,
    # 故 CDP 会话下禁用 world_eval,强制走结构化查询(world_entities/world_entity)。
    if w.get("cdp_url"):
        raise ValueError("world_eval 在 CDP 会话中已禁用(安全边界:任意 JS 可绕过过滤层触达登录会话/隐藏内容);请改用 world_entities/world_entity 结构化查询")
    try:
        result = _evaluate_query_retry(wid, expr, attempts=3)
    except Exception as e:
        # 外部店铺刚导航时 document/body 可能尚未建立；返回统一 pending，
        # 让调用方按 retry_after_ms 重试，而不是收到无法解析的错误文本。
        message = str(e)
        if ("Cannot read properties of null" in message or "Execution context" in message
                or "agentWorld" in message or "Target page, context or browser has been closed" in message):
            try:
                current_url = w["page"].url[:300]
            except Exception:
                current_url = ""
            return _ok({"world_id": wid, "pending": True, "retry_after_ms": 500,
                        "url": current_url, "reason": "页面正在加载，暂时无法读取脚本结果",
                        "error_type": type(e).__name__})
        raise ValueError(f"evaluate 失败: {type(e).__name__}: {str(e)[:200]}")
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        text = str(result)
    if len(text) > 8000:
        text = text[:8000] + f"...(截断,共 {len(text)} 字符)"
    return _ok({"world_id": wid, "result": text})


def _t_world_click_at(args, before_signal=None):
    """视口坐标点击(原生网页世界外元素兜底,坐标来自截图/视觉)。
    以坐标为中心拍区域证据基线,返回统一后果卡。"""
    wid = args["world_id"]
    x = int(args["x"])
    y = int(args["y"])
    w = _world(wid)
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    snap_before = _region_snapshot_at(wid, x, y)
    url_before = w["page"].url
    # 遮挡归因(Phase 3):坐标模式——报告命中点顶层元素,遮罩类(role=dialog/backdrop)标 covered
    occl_probe = _occlusion_probe(wid, x=x, y=y)
    w["page"].mouse.click(x, y)
    _refresh_core_status(wid)
    ret = {"world_id": wid, "clicked_at": [x, y], "method": "mouse-coords"}
    _occlusion_attach(ret, occl_probe)
    effect = _wait_click_effect(wid, snap_before, url_before)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_click_at", args, ret, before_signal)


def _t_world_navigate(args, before_signal=None):
    """世界内导航(无需关闭重开)。返回统一后果卡(navigation)。
    导航后旧 el_N 编号全部失效:target.id 恒为 null,world_epoch +1。"""
    wid = args["world_id"]
    url = args["url"]
    wait_ms = int(args.get("wait_ms", 2000))
    w = _world(wid)
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    w["page"].goto(url, wait_until="domcontentloaded", timeout=60000)
    _wait_world_ready(w["page"])
    if wait_ms:
        w["page"].wait_for_timeout(wait_ms)
    w["epoch"] = int(w.get("epoch", 0)) + 1
    summary = _evaluate(wid, "agentWorld.query.getPageSummary()")
    ret = {"world_id": wid, "url": url, "summary": summary}
    return _outcome_card(wid, "world_navigate", args, ret, before_signal)


# ── 阶段 B 收口:默认协议 3 新工具 ─────────────────────────────
# world_find → 定位构件;world_act → 唯一行动入口(含聚合 steps);
# world_outcome → 幂等读最近一张后果卡。全部复用既有内核与 _outcome_card,不另起炉灶。

ACT_DISPATCH = {
    "click": ("world_click", _t_world_click),
    "fill": ("world_fill", _t_world_fill),
    "press": ("world_press", _t_world_press),
    "batch_fill": ("world_batch_fill", _t_world_batch_fill),
}


def _act_one(wid, step, before_signal):
    """把 world_act 的一个动作步骤分发到既有动作实现(复用统一后果卡)。"""
    kind = step.get("kind")
    if kind not in ACT_DISPATCH:
        raise ValueError(f"world_act 不支持的 kind: {kind!r}(支持 click/fill/press/batch_fill)")
    inner_name, handler = ACT_DISPATCH[kind]
    sub_args = {k: v for k, v in step.items() if k != "kind"}
    sub_args["world_id"] = wid
    result = handler(sub_args, before_signal)
    # 证据记录与既有动作一致(inner_name 入库,保持证据信道语义不变)
    if before_signal is not None:
        try:
            _record_action_evidence(int(wid), inner_name, sub_args, before_signal, result)
        except Exception:
            pass
    return result


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


def _t_world_act(args, before_signal=None):
    """默认协议:唯一行动入口。kind=click|fill|press|batch_fill → 统一后果卡。

    steps 数组 = 聚合执行(等价 RFC 的 world_run):单个 MCP 往返内顺序执行多个动作,
    每步都走 _outcome_card 同一出口;任一步 errored 即停止。返回最后一步的卡 + steps 明细。
    """
    wid = args["world_id"]
    steps = args.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not steps:
            raise ValueError("world_act 的 steps 必须是非空列表")
        if len(steps) > 20:
            raise ValueError("连续动作最多 20 步")
        queued = _task_enqueue_actions(wid, steps)
        cards = []
        for idx, step in enumerate(steps):
            # 聚合动作的任务身份沿用外层；每一步仍可单独声明 operation，避免把
            # “填写”和“提交”错误合并成一个业务操作。
            step = dict(step)
            for metadata_key in ("task_id", "executor"):
                if metadata_key not in step and args.get(metadata_key) is not None:
                    step[metadata_key] = args[metadata_key]
            step["_action_id"] = queued[idx]["action_id"]
            step["_before_page_tokens"] = list(_known_page_tokens(wid))
            _task_mark_queue(wid, idx, "executing", started_at=int(time.time() * 1000))
            try:
                before = _page_signal_snapshot(wid)
            except Exception:
                before = None
            try:
                blocked = _contract_gate(wid, step, before)
                if blocked is not None:
                    card = _result_payload(blocked)
                    cards.append(card)
                    break
                _verify_action_precondition(wid, step)
                res = _act_one(wid, step, before)
                card = _result_payload(res)
            except Exception as e:
                card = _result_payload(_errored_card(wid, f"world_act.step{idx + 1}", step, before, e))
            cards.append(card)
            _record_route_memory(wid, f"world_{step.get('kind', 'act')}", step, before, card)
            expected = step.get("expected_outcome")
            if expected and card.get("page_outcome") != expected:
                card["expected_outcome_mismatch"] = {"expected": expected, "actual": card.get("page_outcome")}
                _task_mark_queue(wid, idx, "stopped", finished_at=int(time.time() * 1000),
                                 page_outcome=card.get("page_outcome"), expected_outcome_mismatch=True)
                break
            # 连续任务的安全闸门：只有实时确认 progressed 才允许进入下一步。
            # unchanged/uncertain/pending/challenged 都意味着当前页面事实尚未
            # 可靠确认，继续写操作可能把上一页结果误归因给当前目标。
            if card.get("page_outcome") != "progressed":
                _task_mark_queue(wid, idx, "stopped", finished_at=int(time.time() * 1000),
                                 page_outcome=card.get("page_outcome"))
                break
            _task_mark_queue(wid, idx, "done", finished_at=int(time.time() * 1000), page_outcome=card.get("page_outcome"))
        last = dict(cards[-1])
        last["steps"] = cards
        last["step_count"] = len(cards)
        last["action"] = {"kind": "act-sequence", "via": "self"}
        last["channel"] = "outcome"
        # P0-1:整单语义。主标签 = 末步卡(任一步 errored 即停,故 errored 必为末步);
        # 另附聚合记账,长任务 FP/FN 以此为准,不再只看末步。
        _outcomes = [c.get("page_outcome") for c in cards]
        _first_bad = next((i for i, o in enumerate(_outcomes) if o != "progressed"), None)
        _seqs = [c.get("evidence_seq") for c in cards
                 if isinstance(c.get("evidence_seq"), int)]
        last["step_outcomes"] = _outcomes
        last["all_progressed"] = _first_bad is None
        last["first_failure_idx"] = _first_bad
        if _seqs:
            last["seq_range"] = {"first": _seqs[0], "last": _seqs[-1]}
        return _ok(last)

    kind = args.get("kind") or "click"
    if kind not in ACT_DISPATCH:
        raise ValueError(f"world_act 不支持的 kind: {kind!r}(支持 click/fill/press/batch_fill)")
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = None
    try:
        blocked = _contract_gate(wid, args, before_signal)
        if blocked is not None:
            return blocked
        _verify_action_precondition(wid, args)
        return _act_one(wid, args, before_signal)
    except Exception as e:
        return _errored_card(wid, f"world_act({kind})", args, before_signal, e)


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
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
