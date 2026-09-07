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

try:
    from aw_runtime import (  # noqa: F401
    SCREENSHOT_DIR,
    PROFILES_DIR,
    VISUAL_RMS_THRESHOLD,
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
    SCREENSHOT_DIR,
    PROFILES_DIR,
    VISUAL_RMS_THRESHOLD,
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

try:
    from aw_status import (  # noqa: F401
_auth_status,
    _inject_status,
    _status,
    _status_light,
    )
except ImportError:
    from mcp.aw_status import (  # noqa: F401
_auth_status,
    _inject_status,
    _status,
    _status_light,
    )

try:
    from aw_timeline import (  # noqa: F401
_assumption_check,
    _assumption_expr,
    _build_action_evidence,
    _evidence_decision,
    _inject_action_evidence,
    _inject_notices,
    _record_action_evidence,
    _t_world_ack,
    _t_world_assume,
    _t_world_evidence,
    _t_world_status,
    _t_world_timeline,
    _timeline_causal_windows,
    _tl,
    _tl_action,
    _tl_merge_dom,
    _trace_store,
    )
except ImportError:
    from mcp.aw_timeline import (  # noqa: F401
_assumption_check,
    _assumption_expr,
    _build_action_evidence,
    _evidence_decision,
    _inject_action_evidence,
    _inject_notices,
    _record_action_evidence,
    _t_world_ack,
    _t_world_assume,
    _t_world_evidence,
    _t_world_status,
    _t_world_timeline,
    _timeline_causal_windows,
    _tl,
    _tl_action,
    _tl_merge_dom,
    _trace_store,
    )

try:
    from aw_outcome import (  # noqa: F401
_anomaly_check,
    _build_page_outcome,
    _challenge_detection,
    _click_region_after,
    _click_region_snapshot,
    _errored_card,
    _finalize_click_result,
    _is_submit_trigger,
    _occlusion_attach,
    _occlusion_probe,
    _outcome_card,
    _region_snapshot_at,
    _region_styles,
    _wait_click_effect,
    )
except ImportError:
    from mcp.aw_outcome import (  # noqa: F401
_anomaly_check,
    _build_page_outcome,
    _challenge_detection,
    _click_region_after,
    _click_region_snapshot,
    _errored_card,
    _finalize_click_result,
    _is_submit_trigger,
    _occlusion_attach,
    _occlusion_probe,
    _outcome_card,
    _region_snapshot_at,
    _region_styles,
    _wait_click_effect,
    )

try:
    from aw_query import (  # noqa: F401
_change_digest,
    _t_world_change_digest,
    _t_world_changes,
    _t_world_close,
    _t_world_entities,
    _t_world_entity,
    _t_world_find,
    _t_world_layers,
    _t_world_list,
    _t_world_map,
    _t_world_outcome,
    _t_world_resolve,
    _t_world_state,
    )
except ImportError:
    from mcp.aw_query import (  # noqa: F401
_change_digest,
    _t_world_change_digest,
    _t_world_changes,
    _t_world_close,
    _t_world_entities,
    _t_world_entity,
    _t_world_find,
    _t_world_layers,
    _t_world_list,
    _t_world_map,
    _t_world_outcome,
    _t_world_resolve,
    _t_world_state,
    )

try:
    from aw_taskgraph import (  # noqa: F401
_business_state_snapshot,
    _contract_gate,
    _graph_trace_source,
    _t_world_adapter_compare,
    _t_world_business_state,
    _t_world_graph,
    _t_world_graph_archive,
    _t_world_graph_assess,
    _t_world_graph_bundle,
    _t_world_graph_replay_check,
    _t_world_operation_check,
    _t_world_task_plan,
    _t_world_trace,
    _t_world_trace_archive,
    )
except ImportError:
    from mcp.aw_taskgraph import (  # noqa: F401
_business_state_snapshot,
    _contract_gate,
    _graph_trace_source,
    _t_world_adapter_compare,
    _t_world_business_state,
    _t_world_graph,
    _t_world_graph_archive,
    _t_world_graph_assess,
    _t_world_graph_bundle,
    _t_world_graph_replay_check,
    _t_world_operation_check,
    _t_world_task_plan,
    _t_world_trace,
    _t_world_trace_archive,
    )

try:
    from aw_actions import (  # noqa: F401
ACT_DISPATCH,
    _act_one,
    _build_locator,
    _click_locator_nowait,
    _fill_visible,
    _refresh_core_status,
    _t_world_act,
    _t_world_batch_fill,
    _t_world_click,
    _t_world_click_at,
    _t_world_eval,
    _t_world_fill,
    _t_world_navigate,
    _t_world_press,
    _t_world_screenshot,
    _t_world_wait,
    )
except ImportError:
    from mcp.aw_actions import (  # noqa: F401
ACT_DISPATCH,
    _act_one,
    _build_locator,
    _click_locator_nowait,
    _fill_visible,
    _refresh_core_status,
    _t_world_act,
    _t_world_batch_fill,
    _t_world_click,
    _t_world_click_at,
    _t_world_eval,
    _t_world_fill,
    _t_world_navigate,
    _t_world_press,
    _t_world_screenshot,
    _t_world_wait,
    )

try:
    from aw_guide import (  # noqa: F401
_compact_focused_view,
    _expand_candidates,
    _focused_view,
    _t_world_guide,
    )
except ImportError:
    from mcp.aw_guide import (  # noqa: F401
_compact_focused_view,
    _expand_candidates,
    _focused_view,
    _t_world_guide,
    )
server = Server("agent-world")

# ── 世界注册表 ────────────────────────────────────────────────
# world_id -> {"browser", "context", "page", "url", "opened_at"}
_next_world_id = 1
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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
