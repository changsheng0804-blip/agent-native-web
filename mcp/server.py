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
  world_change_digest 变化摘要信道(读取压缩后的变化)
  world_evidence 操作证据信道(读取动作前后证据)
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
import json
import math
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageStat

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from playwright.sync_api import sync_playwright

# Playwright 同步 API 强依赖 greenlet 协程上下文，必须在单一固定 OS 工作线程内运行，杜绝多线程竞争切换
_pw_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright_worker")


sys.stdout.reconfigure(encoding="utf-8")

ALL_IN_ONE = Path(__file__).parent.parent / "extension" / "all-in-one.js"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
# P0-2 视觉阈值:区域前后帧 RMS 差异超过此值判 visual-effected。
# 校准 v1(docs/视觉阈值校准报告.md):真静态 0.0 / 邻区动画渗入 ~2.1-2.3 /
# canvas 重绘 ~24.2 / 整块变色 ~33.3,取 5.0(噪声上 2.2x,最弱正例下 1/5)。
# 改动此值必须同步更新 test_visual_evidence.py 与 test_visual_calib.py 的断言。
VISUAL_RMS_THRESHOLD = 5.0
PROFILES_DIR = Path(__file__).parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

if not ALL_IN_ONE.exists():
    raise SystemExit(f"all-in-one.js 不存在: {ALL_IN_ONE}")

with open(ALL_IN_ONE, "r", encoding="utf-8") as f:
    INJECT_JS = f.read()

server = Server("agent-world")

# ── 世界注册表 ────────────────────────────────────────────────
# world_id -> {"browser", "context", "page", "url", "opened_at"}
_worlds = {}
_next_world_id = 1
_playwright = None


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


def _evaluate(world_id, expr, arg=None):
    w = _world(world_id)
    return w["page"].evaluate(expr, arg) if arg is not None else w["page"].evaluate(expr)


# ── 工具定义 ─────────────────────────────────────────────────
# 阶段 B 收口:对外默认协议 6 个词(弱模型只学这一条环)
#   world_open → world_guide → world_find → world_act → world_outcome → world_close
# 其余 19 个旧工具全部保留(兼容已接入客户端),描述加 [内部/调试] 前缀;
# AGENT_WORLD_LITE=1 时 list_tools 只暴露 6 个, call_tool 拒绝旧工具。
CANONICAL_TOOLS = {"world_open", "world_guide", "world_find", "world_act", "world_outcome", "world_close"}
CANONICAL_ORDER = ["world_open", "world_guide", "world_find", "world_act", "world_outcome", "world_close"]


def _lite_mode():
    return os.environ.get("AGENT_WORLD_LITE", "").strip().lower() in ("1", "true", "yes", "on")


@server.list_tools()
async def list_tools():
    tools = [
        types.Tool(
            name="world_open",
            description="打开一个网页并建立原生网页世界(注入 agent-runtime)。返回世界 ID 和页面摘要。可并行打开多个世界互不干扰。headful=true 时弹出可见窗口(人工介入点:登录/验证码/真人确认);profile=名称 时使用持久化登录态(同一名称复用);cdp_url 可连接已有 Chrome 调试端口(如 http://localhost:9222),复用日常已登录浏览器(注意:不会关闭用户浏览器)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网址(使用 cdp_url 时可填当前页地址,与当前页相同则跳过导航;填空则直接注入当前页)"},
                    "wait_ms": {"type": "number", "description": "初始等待毫秒(动态页面建议 3000-6000)", "default": 3000},
                    "stabilize_ms": {"type": "number", "description": "等待世界稳定(状态卡 stable)的最大毫秒,渐进渲染页面自动等", "default": 10000},
                    "headful": {"type": "boolean", "description": "是否弹出可见窗口(登录/验证码/人工确认场景用)", "default": False},
                    "profile": {"type": "string", "description": "持久化登录态名称(如 login-taobao),同一名称复用 cookie/会话;留空则不持久化"},
                    "cdp_url": {"type": "string", "description": "连接已有 Chrome 的 CDP 调试地址(如 http://localhost:9222),复用日常已登录浏览器;与 profile/headless 互斥"},
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="world_entities",
            description="构件清单(图纸构件表):按角色/标签/文本/名字/稳定指纹/空间范围/可交互/视口过滤查询元素,返回编号、名字、坐标、指纹。指纹=跨会话稳定的第二 ID(同站多次进出可快速认路)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer", "description": "世界 ID(world_open 返回)"},
                    "role": {"type": "string", "description": "语义角色,如 button/link/input/combobox/heading/navigation"},
                    "tag": {"type": "string", "description": "HTML 标签,如 a/button/input/div"},
                    "text": {"type": "string", "description": "文本包含(子串匹配)"},
                    "name": {"type": "string", "description": "名字包含(如 round-trip 匹配 combobox.round-trip)"},
                    "fingerprint": {"type": "string", "description": "稳定指纹精确匹配(同站多次进出的认路记忆,从上次 world_entity 详图里取)"},
                    "bounds": {
                        "type": "object",
                        "description": "空间矩形过滤 {x,y,w,h}——与 world_map 返回的 region.bounds 一致。区域钻取:地图拿到某区 bounds 后,只查该区内的构件(中心点落在矩形内)",
                        "properties": {
                            "x": {"type": "number"}, "y": {"type": "number"},
                            "w": {"type": "number"}, "h": {"type": "number"},
                        },
                    },
                    "interactive": {"type": "boolean", "description": "是否可交互"},
                    "in_viewport": {"type": "boolean", "description": "是否在当前视口内"},
                    "max_results": {"type": "integer", "description": "最多返回条数", "default": 100},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_entity",
            description="单个构件详情:编号、名字、稳定指纹、坐标、语义、文本、可交互、邻居(上下左右)、所在区域。指纹是跨会话稳定的第二 ID,用于同站多次进出时快速认路。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号(如 el_89)或可解析的名字"},
                },
                "required": ["world_id", "id"],
            },
        ),
        types.Tool(
            name="world_layers",
            description="图层视图:结构(标签分布)/语义(角色分布)/空间(网格、视口)/交互/名字统计。",
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "integer"}},
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_map",
            description="页面结构导览(地图):按语义地标容器(导航/侧栏/主体/页脚/表单/弹窗/标签栏等)分区,每区给出范围、构件数、可交互入口(带强 ID 和指纹)。适合控制台类复杂页面——agent 看一次地图就知道'哪里有什么、点哪个编号过去',不必翻全部清单。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "max_entries": {"type": "integer", "description": "每区最多列出几个可交互入口", "default": 6},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_resolve",
            description="弱 ID 解析:把名字(如 combobox.round-trip)/强 ID/页面原生 id 解析为稳定编号。页面变化后名字失效时重新解析即可。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "query": {"type": "string", "description": "名字或编号"},
                },
                "required": ["world_id", "query"],
            },
        ),
        types.Tool(
            name="world_changes",
            description="[何时用]调试用——需要逐条核对原始事件序列时用;常规操作请用 world_change_digest(更省)。[何时不用]只想知道有没有变化时不要用(噪声大)。变更流:读取自 since 序号以来的页面变化事件(add/remove/update/visibility),增量续读不重不漏。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的 to 值(游标)", "default": 0},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_state",
            description="[何时用]操作后结果存疑时——看弹窗/表单/登录态/URL 在哪;或新开页面后做一次全局确认。[何时不用]不需要全局状态、只在查单个元素时不要用(用 world_entity)。页面状态信道:只读取当前最新的整体页面状态,包括网址、标题、稳定状态、弹窗/菜单和变化序号;不返回完整页面结构。",
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "integer"}},
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_change_digest",
            description="[何时用]操作后想了解'页面变了什么'的摘要(推荐日常用)——返回数量/重要构件/游标,不返回原始事件。[何时不用]需要完整事件序列时用 world_changes。变化摘要信道:读取自 since 序号以来的压缩变化摘要,只返回数量、重要构件和变化游标,不返回原始事件列表。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的变化序号", "default": 0},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_evidence",
            description="[何时用]调试/验证场景——追溯某次操作的证据链时用;[何时不用]常规流程不要主动调,操作返回值自带 verdict 证据即可。操作证据信道:读取动作前后页面状态、网址、弹窗/菜单变化和结果判断;不保存填入的具体文本内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的证据序号", "default": 0},
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 20},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_guide",
            description="实时任务导览:把页面状态、变化摘要和最近操作证据组合成一份面向当前任务的短导览;只返回相关区域、少量候选入口和下一步,不返回整页地图。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "task": {"type": "string", "description": "当前要完成的任务,尽量用一句话描述"},
                    "change_since": {"type": "integer", "description": "变化摘要上次读取到的序号", "default": 0},
                    "evidence_since": {"type": "integer", "description": "操作证据上次读取到的序号", "default": 0},
                    "max_candidates": {"type": "integer", "description": "最多返回候选入口数", "default": 6},
                },
                "required": ["world_id", "task"],
            },
        ),
        types.Tool(
            name="world_click",
            description="按编号点击元素(原生 click 事件)。带遮挡检测与自动等待，并返回页面整体反馈(URL、页面状态、弹窗/菜单和变化序号);如果页面已跳转或出现覆盖层,优先按整体事实修正局部效果判断。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "visual_evidence": {"type": "boolean", "description": "是否启用视觉 diff 兜底(截取目标区域前后帧做像素比对,捕获纯 CSS 动效/浮层变化)。默认 False 以保持快速;需要视觉证据时开启", "default": False},
                },
                "required": ["world_id", "id"],
            },
        ),
        types.Tool(
            name="world_fill",
            description="按编号填入文本(优先 Playwright 原生 fill/press_sequentially;支持打字间隔模拟触发联想;失败自动降级 JS setter+覆盖层切换)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "text": {"type": "string", "description": "要填入的文本"},
                    "type_delay_ms": {"type": "integer", "description": "逐字打字延迟毫秒(>0 时模拟真实键盘输入,触发自动联想下拉)", "default": 0},
                    "visual_evidence": {"type": "boolean", "description": "是否启用视觉 diff 兜底(默认 False 保持快速)", "default": False},
                },
                "required": ["world_id", "id", "text"],
            },
        ),
        types.Tool(
            name="world_batch_fill",
            description="批量填入多个表单字段(单次 MCP 往返完成多个输入框填写,减少交互延迟;逐字段容错,失败不影响后续)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "fields": {
                        "type": "array",
                        "description": "表单字段列表: [{ id, text, type_delay_ms? }]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "构件编号或名字"},
                                "text": {"type": "string", "description": "填入内容"},
                                "type_delay_ms": {"type": "integer", "description": "逐字打字延迟毫秒", "default": 0},
                            },
                            "required": ["id", "text"],
                        },
                    },
                },
                "required": ["world_id", "fields"],
            },
        ),
        types.Tool(
            name="world_press",
            description="按编号聚焦并按按键(如 Enter/Escape/Tab/ArrowDown),用于提交表单或操作下拉建议。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "key": {"type": "string", "description": "按键名,如 Enter、Escape、Tab、ArrowDown"},
                    "visual_evidence": {"type": "boolean", "description": "是否启用视觉 diff 兜底(默认 False 保持快速)", "default": False},
                },
                "required": ["world_id", "id", "key"],
            },
        ),
        types.Tool(
            name="world_wait",
            description="等待条件满足:构件出现/消失/文本变化。轮询内部原生网页世界,操作后验证结果的利器。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["appear", "disappear"], "description": "appear=等待出现,disappear=等待消失"},
                    "role": {"type": "string", "description": "角色过滤(可选)"},
                    "text": {"type": "string", "description": "文本过滤(可选)"},
                    "name": {"type": "string", "description": "名字过滤(可选)"},
                    "timeout_ms": {"type": "integer", "description": "超时毫秒", "default": 30000},
                },
                "required": ["world_id", "mode"],
            },
        ),
        types.Tool(
            name="world_click_at",
            description="按视口坐标点击(原生网页世界外的元素/iframe 区域兜底,坐标来自截图或视觉)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "x": {"type": "integer", "description": "视口横坐标"},
                    "y": {"type": "integer", "description": "视口纵坐标"},
                },
                "required": ["world_id", "x", "y"],
            },
        ),
        types.Tool(
            name="world_navigate",
            description="在当前世界内导航到新 URL(SPA 跳转/换页,无需关闭重开)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "url": {"type": "string", "description": "要导航的网址"},
                    "wait_ms": {"type": "number", "description": "导航后额外等待毫秒", "default": 2000},
                },
                "required": ["world_id", "url"],
            },
        ),
        types.Tool(
            name="world_eval",
            description="在世界内执行 JS 表达式(调试/特殊查询用,建议只读;返回结果截断保护)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "expression": {"type": "string", "description": "JS 表达式,如 document.title 或 (() => {...})()"},
                },
                "required": ["world_id", "expression"],
            },
        ),
        types.Tool(
            name="world_screenshot",
            description="截图:整页、指定构件区域或带编号标注图(Set-of-Mark)。支持直接返回图片数据(ImageContent)或文件路径,原生多模态模型友好。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号(可选,不填截整页或视口)"},
                    "annotated": {"type": "boolean", "description": "是否绘制带 [el_X] 编号与名称的半透明标注框(Set-of-Mark 模式)", "default": False},
                    "return_base64": {"type": "boolean", "description": "是否直接返回 MCP ImageContent 原生图片数据", "default": True},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_close",
            description="关闭世界,释放浏览器资源。",
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "integer"}},
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_list",
            description="列出所有已打开的世界。",
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── 阶段 B 收口:3 个新工具(默认协议) ──
        types.Tool(
            name="world_find",
            description="默认协议:按条件定位构件(替代 world_entities/world_resolve 的日常用法)。返回 matches[] 与 ambiguous 标记;禁止在本工具内执行任何动作。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "q": {"type": "string", "description": "一句话或弱 ID(名字/强 ID/页面原生 id),走 resolve 解析;未命中时按可见文本/名字子串兜底(大小写不敏感)"},
                    "role": {"type": "string", "description": "语义角色,如 button/link/input/combobox/heading"},
                    "text": {"type": "string", "description": "文本包含(子串匹配)"},
                    "name": {"type": "string", "description": "名字包含(如 round-trip 匹配 combobox.round-trip)"},
                    "interactive": {"type": "boolean", "description": "仅返回可交互构件"},
                    "in_viewport": {"type": "boolean", "description": "仅返回视口内构件"},
                    "max_results": {"type": "integer", "description": "最多返回条数", "default": 20},
                    "verbose": {"type": "boolean", "description": "true 时返回全量深诊断状态卡;默认轻量", "default": False},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_act",
            description="默认协议:唯一行动入口。kind=click|fill|press|batch_fill,返回统一后果卡(page_outcome 五态)。steps 数组可在一个往返内执行多个动作(聚合执行,等价 RFC 的 world_run);任一步 errored 即停止。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "kind": {"type": "string", "description": "动作类型: click|fill|press|batch_fill", "enum": ["click", "fill", "press", "batch_fill"]},
                    "id": {"type": "string", "description": "构件编号(如 el_89)或可解析的名字(world_find 返回的 id)"},
                    "text": {"type": "string", "description": "fill 时填写的文本"},
                    "key": {"type": "string", "description": "press 时的按键(Enter/Escape/Tab...)"},
                    "fields": {"type": "array", "description": "batch_fill 的字段列表 [{\"id\":\"el_6\",\"text\":\"...\"}]", "items": {"type": "object"}},
                    "type_delay_ms": {"type": "number", "description": "逐字打字延迟(触发联想下拉用)", "default": 0},
                    "visual_evidence": {"type": "boolean", "description": "是否截前后帧做视觉 diff 兜底", "default": False},
                    "verbose": {"type": "boolean", "description": "true 时返回全量深诊断状态卡(frames/forms/world 明细);默认轻量(URL/稳定态/登录态/弹窗)", "default": False},
                    "steps": {"type": "array", "description": "聚合执行:多步动作序列 [{kind,id,text|key|fields,...}, ...],任一步 errored 即停", "items": {"type": "object"}},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_outcome",
            description="默认协议:读最近一张统一后果卡(幂等,弱模型'我刚才到底怎样了'的唯一查询)。since 传入 evidence_seq 时,仅当有新动作才返回新卡,否则返回 none 卡。watch_id 为阶段 C(验尸官)预留。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "仅当存在 evidence_seq 大于 since 的新卡时返回它", "default": 0},
                    "verbose": {"type": "boolean", "description": "true 时返回全量深诊断状态卡;默认轻量", "default": False},
                },
                "required": ["world_id"],
            },
        ),
    ]
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
    try:
        # 全部在专用单一 executor 线程执行(Playwright 同步 API 强线程亲和)
        return await asyncio.get_event_loop().run_in_executor(_pw_executor, _impl_with_status, name, arguments)
    except Exception as e:
        traceback.print_exc()
        return [types.TextContent(type="text", text=f"错误: {e}")]


# 动作类工具:统一走 before_signal + 证据记录 + 统一后果卡(阶段 A)
ACTION_NAMES = {"world_click", "world_click_at", "world_fill", "world_batch_fill", "world_press", "world_navigate"}


def _impl_with_status(name, args):
    if _lite_mode() and name not in CANONICAL_TOOLS:
        raise ValueError(f"AGENT_WORLD_LITE 模式只开放 6 个默认工具({sorted(CANONICAL_TOOLS)});{name} 是内部/调试工具,请勿在 LITE 会话调用")
    wid = args.get("world_id")
    before_signal = None
    if name in ACTION_NAMES and wid is not None:
        try:
            before_signal = _page_signal_snapshot(int(wid))
        except Exception:
            before_signal = None
    try:
        result = _impl(name, args, before_signal)
    except Exception as e:
        # 动作异常路径:返回统一后果卡 page_outcome=errored(结构化返回,不吞错误)
        if name in ACTION_NAMES and wid is not None:
            traceback.print_exc()
            try:
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
    # 阶段 B:动作出口的后果卡缓存,供 world_outcome 幂等读取(world_act 内部已记录证据,这里只缓存卡)
    if name in ACTION_NAMES or name == "world_act":
        try:
            payload = _result_payload(result)
            if payload and payload.get("channel") == "outcome":
                _world(int(wid))["last_outcome_card"] = payload
        except Exception:
            pass
    if name in {"world_state", "world_change_digest", "world_evidence", "world_guide"}:
        return result
    # 瘦身演进:动作类工具 (ACTION_NAMES + world_act) 与查找/查询工具默认采用轻量 status
    # 只有显式 verbose=true 时才附带全量 frames/forms/world 深度诊断,大幅降低 Token 占用
    light = name in ACTION_NAMES or name in ("world_act", "world_find", "world_outcome")
    if light and args.get("verbose"):
        light = False
    return _inject_status(result, wid, light=light)


# ── 网页状态卡(仪表盘)────────────────────────────────────────
AUTH_COOKIE_HINTS = ["passport", "session", "token", "sid", "uid", "unb", "sso", "login", "auth"]


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
    if name == "world_change_digest":
        return _t_world_change_digest(args)
    if name == "world_evidence":
        return _t_world_evidence(args)
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
    if name == "world_click_at":
        return _t_world_click_at(args, before_signal)
    if name == "world_navigate":
        return _t_world_navigate(args, before_signal)
    if name == "world_close":
        return _t_world_close(args)
    if name == "world_list":
        return _t_world_list(args)
    raise ValueError(f"未知工具: {name}")


def _ok(data):
    return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


def _resolve_id(world_id, q):
    """支持名字/强 ID,统一解析为强 ID"""
    r = _evaluate(world_id, "(q) => agentWorld.query.resolve(q)", q)
    if r and r.get("id"):
        return r["id"]
    if r and r.get("matches"):
        raise ValueError(f"{q!r} 有 {len(r['matches'])} 个候选: {r['matches']},请用 findEntities 精确过滤")
    # 文本兜底:resolve 未命中时,按可见文本做大小写不敏感子串匹配(与内核 findEntities 口径一致)
    texts = _evaluate(world_id, "(q) => agentWorld.query.findEntities({text: q})", q) or []
    if len(texts) == 1:
        return texts[0]["id"]
    if len(texts) > 1:
        raise ValueError(f"{q!r} 文本匹配到 {len(texts)} 个候选,请用 world_find 精确定位")
    raise ValueError(f"找不到构件: {q!r}")


def _t_world_open(args):
    global _next_world_id
    url = args["url"] or ""
    wait_ms = int(args.get("wait_ms", 3000))
    headful = bool(args.get("headful", False))
    profile = args.get("profile") or None
    cdp_url = args.get("cdp_url") or None
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

    def _on_response(res):
        try:
            if res.status >= 400:
                snippet = ""
                try:
                    snippet = res.text()[:200]
                except Exception:
                    pass
                net_errors.append({
                    "url": res.url,
                    "status": res.status,
                    "detail": snippet,
                    "time": time.time(),
                })
                if len(net_errors) > 50:
                    net_errors.pop(0)
        except Exception:
            pass

    def _on_console(msg):
        try:
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
            console_errors.append({
                "text": f"Uncaught {type(exc).__name__}: {str(exc)[:300]}",
                "time": time.time(),
            })
            if len(console_errors) > 50:
                console_errors.pop(0)
        except Exception:
            pass

    try:
        page.on("response", _on_response)
        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)
    except Exception:
        pass

    _worlds[wid] = {
        "handle": handle,
        "context": context,
        "page": page,
        "url": page.url,
        "opened_at": time.time(),
        "profile": profile,
        "cdp_url": cdp_url,
        # 操作证据信道只在当前网页世界内短暂保存,不落盘。
        "evidence_seq": 0,
        "evidence_log": [],
        # 世界纪元:world_navigate 成功导航 +1;跨纪元旧 el_N 全部失效
        "epoch": 0,
        "network_errors": net_errors,
        "console_errors": console_errors,
    }
    # 等待世界稳定(分层加载:渐进渲染/懒加载,固定秒数不可靠,以状态卡 stable 为准)
    stabilize_ms = int(args.get("stabilize_ms", 10000))
    deadline = time.time() + stabilize_ms / 1000
    while time.time() < deadline:
        try:
            st = _evaluate(wid, "() => agentWorld.query.getStatus()")
            if st.get("page", {}).get("state") == "stable":
                break
        except Exception:
            pass
        time.sleep(0.5)
    summary = _evaluate(wid, "agentWorld.query.getPageSummary()")
    return _ok({"world_id": wid, "url": page.url, "ready": True, "headful": headful, "profile": profile, "cdp_url": cdp_url, "summary": summary})


def _t_world_entities(args):
    wid = args["world_id"]
    f = {k: v for k, v in args.items() if k not in ("world_id",) and v is not None}
    if "in_viewport" in f:
        f["inViewport"] = f.pop("in_viewport")
    if "max_results" in f:
        f["maxResults"] = f.pop("max_results")
    entities = _evaluate(wid, "(f) => agentWorld.query.findEntities(f)", f)
    return _ok({"world_id": wid, "count": len(entities), "entities": entities})


def _t_world_entity(args):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
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
_IMPORTANT_ROLES = {
    "dialog", "alertdialog", "menu", "form", "button", "input", "combobox",
    "listbox", "option", "link", "navigation", "tab", "tablist", "searchbox",
    "textbox", "select", "details", "summary", "tooltip",
}
# digest 强信号角色(窄口径):只认"几乎必是操作结果"的语义。
# 重型 SPA 整体重渲染时,页面外壳(button/link/navigation)会大量"假新增"刷屏,
# 若把它们标高,真信号(弹窗)会被挤出 highlights(digest 价值评估实测:噪声 29 vs 强信号 5)。
_DIGEST_HIGH_ROLES = {
    "dialog", "alertdialog", "menu", "option", "listbox", "combobox",
    "input", "select", "searchbox", "textbox",
}
# 内容性角色 → 中重要性
_MEDIUM_ROLES = {
    "heading", "list", "listitem", "article", "section", "region",
    "card", "banner", "contentinfo", "main", "complementary",
    # 外壳/重渲染常见角色:digest 出现不一定是操作结果,降为中(仅影响 digest,不影响 effect)
    "button", "link", "navigation", "tab", "tablist", "form", "details", "summary",
}


def _event_importance(evt):
    """单条变更事件的重要性分级(high/medium/low)——供 digest/变更流使用。
    依据:事件类型(结构性 add/remove > update > visibility) × 语义角色。
    注意:high 只给"强信号"角色(_DIGEST_HIGH_ROLES)——重型 SPA 重渲染时
    外壳(button/link/navigation)大量假新增,若标高会把真弹窗挤出 highlights。
    旧事件(内核补 semantic 前记录)缺 semantic 时从 name 前缀推断。
    """
    etype = evt.get("type")
    semantic = evt.get("semantic") or ""
    if not semantic:
        name = evt.get("name") or ""
        semantic = name.split(".")[0] if name else ""
    if etype == "visibility":
        return "low"
    if etype in ("add", "remove"):
        if semantic in _DIGEST_HIGH_ROLES:
            return "high"
        if semantic in _IMPORTANT_ROLES or semantic in _MEDIUM_ROLES:
            return "medium"
        return "medium"  # 新增/移除默认中(结构变化),具体由 digest 归纳
    # update
    if semantic in _DIGEST_HIGH_ROLES:
        return "medium"  # 强信号构件更新值得看
    return "low"


_ROLE_LABEL = {
    "dialog": "弹窗", "alertdialog": "警告弹窗", "menu": "菜单", "button": "按钮",
    "input": "输入框", "combobox": "组合框", "listbox": "列表", "option": "选项",
    "link": "链接", "navigation": "导航", "tab": "标签页", "tablist": "标签栏",
    "searchbox": "搜索框", "textbox": "文本框", "select": "选择器",
    "details": "折叠区", "summary": "折叠标题", "tooltip": "提示",
    "heading": "标题", "list": "列表", "listitem": "列表项", "article": "文章",
    "section": "区块", "region": "区域", "card": "卡片", "banner": "页头",
    "contentinfo": "页脚", "main": "主体", "complementary": "侧栏",
    "form": "表单", "content": "内容", "img": "图片", "video": "视频",
    "table": "表格", "navigation2": "导航",
}


def _change_digest(events):
    """把一批变更事件归纳成结构化语义摘要(CAD 图纸风格)。
    返回 {counts, key}——不写人话句子,只用强 ID 引用:
      counts: 数量骨架(新增/移除/更新/可见性)
      key:    高价值强 ID 引用列表(操作结果的直接证据),每条 {type,id,semantic,name}
              agent 拿到 id(如 el_595)可用 world_entity 查详图(位置/属性/邻居/区域),
              如 CAD 图纸上的 004# 圆孔——编号即一切属性的入口,无需猜。
    强信号口径:_DIGEST_HIGH_ROLES(弹窗/菜单/选项/组合框/输入框等几乎必是操作结果的角色),
    外壳(button/link/navigation)降权避免重型 SPA 重渲染"假新增"刷屏。
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
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }")
    except Exception:
        pass
    return _ok({
        "world_id": wid,
        "channel": "page-state",
        "state": _page_signal_snapshot(wid),
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


def _result_payload(result):
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
    w["evidence_seq"] = int(w.get("evidence_seq", 0)) + 1
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


def _guide_terms(task):
    """从一句任务描述提取少量搜索锚点,不让导览层读取完整页面文本。"""
    stopwords = {
        "请帮我", "帮我", "帮助", "找到", "查找", "查看", "打开", "进入", "点击", "确认",
        "页面", "网页", "网站", "当前", "任务", "并", "和", "的", "一个", "一下", "区域",
        "操作", "完成", "是否", "然后", "之后", "上方", "里面", "这个", "那个",
        "find", "open", "go", "to", "the", "a", "an", "and", "on", "in", "page", "confirm",
    }
    raw = re.findall(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]{2,}", str(task).lower())
    terms = []
    aliases = {
        "拉取请求": "pull requests",
        "合并请求": "pull requests",
        "问题": "issues",
        "筛选": "filter",
        "搜索": "search",
        "发布": "release",
        "标签": "tag",
        "模型": "model",
        "弹窗": "dialog",
    }
    for item in raw:
        for source, alias in aliases.items():
            if source in item and alias not in terms:
                terms.append(alias)
        cleaned = item
        for stop in stopwords:
            cleaned = cleaned.replace(stop, " ")
        parts = re.findall(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]{2,}", cleaned)
        for term in parts:
            if term not in stopwords and len(term) >= 2 and term not in terms:
                terms.append(term)
    expanded = list(terms)
    for term in terms:
        alias = aliases.get(term)
        if alias and alias not in expanded:
            expanded.append(alias)
    # 网页上“筛选”经常由搜索输入框承载,两者应作为同一任务焦点。
    if "filter" in expanded and "search" not in expanded:
        expanded.append("search")
    if "search" in expanded and "filter" not in expanded:
        expanded.append("filter")
    return expanded[:16]


def _t_world_guide(args):
    """把三个页面信道和当前实时结构组合成一份短的任务导览。"""
    wid = args["world_id"]
    task = str(args["task"]).strip()
    if not task:
        raise ValueError("task 不能为空,请用一句话描述当前任务")
    max_candidates = max(1, min(int(args.get("max_candidates", 6)), 12))
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
                return b.match_score - a.match_score || Number(b.interactive) - Number(a.interactive);
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
    if candidates:
        next_action = f"优先检查候选 {candidates[0].get('id')} 的详图,再决定是否执行动作"
    else:
        next_action = "当前页面没有找到直接匹配入口;不要猜测,先扩大到导航/菜单区域或提供更具体目标词"

    return _ok({
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
        "next_action": next_action,
        "unknown": [
            "点击后的新页面结构尚未确认",
            "没有公开链接的按钮去向需要执行后用证据确认",
        ],
        "next_cursors": {
            "change_since": change_digest.get("to", change_since),
            "evidence_since": int(w.get("evidence_seq", evidence_since)),
        },
    })


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


def _refresh_core_status(wid, settle_ms=300):
    """操作后等防抖+渲染,主动刷新内核状态(状态卡反映操作结果)"""
    w = _world(wid)
    time.sleep(settle_ms / 1000)
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }")
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


def _page_signal_snapshot(wid):
    """读取一份很小的页面整体状态,作为动作反馈的全局基线。

    这里不读取整页结构,只关注导航和覆盖层这类会改变任务路径的信号。
    """
    w = _world(wid)
    try:
        core = _evaluate(wid, "() => agentWorld.query.getStatus()") or {}
    except Exception:
        core = {}
    try:
        overlays = _evaluate(
            wid,
            """() => {
                const pick = (role) => agentWorld.query.findEntities({
                    role, inViewport: true, maxResults: 8
                }).map(e => ({ id: e.id, name: e.name, text: e.text }));
                return {
                    dialogs: pick('dialog').concat(pick('alertdialog')),
                    menus: pick('menu')
                };
            }""",
        ) or {}
    except Exception:
        overlays = {}
    try:
        title = w["page"].title()[:200]
    except Exception:
        title = ""
    page_state = core.get("page", {}) or {}
    world_state = core.get("world", {}) or {}
    # 静默失败监听游标:记录动作前后错误列表长度,用于差分
    net_errors = w.get("network_errors") or []
    console_errors = w.get("console_errors") or []
    return {
        "url": w["page"].url[:300],
        "title": title,
        "state": page_state.get("state", "unknown"),
        "changes_seq": world_state.get("changesSeq", 0),
        "dialogs": overlays.get("dialogs", []) or core.get("dialogs", []) or [],
        "menus": overlays.get("menus", []) or [],
        "_net_err_cursor": len(net_errors),
        "_console_err_cursor": len(console_errors),
    }


def _signal_items(signal, key):
    return signal.get(key, []) if isinstance(signal, dict) else []


def _signal_delta(before, after, key):
    """返回信道中新增和消失的覆盖层,只保留小量可读证据。"""
    def item_key(item):
        if not isinstance(item, dict):
            return str(item)
        return (item.get("id"), item.get("name"), item.get("text"))

    before_map = {item_key(x): x for x in _signal_items(before, key)}
    after_map = {item_key(x): x for x in _signal_items(after, key)}
    new_keys = after_map.keys() - before_map.keys()
    gone_keys = before_map.keys() - after_map.keys()
    return {
        "new": [after_map[k] for k in new_keys][:8],
        "gone": [before_map[k] for k in gone_keys][:8],
    }


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
            why_str = f"操作引发后端接口报错: HTTP {err['status']} {err.get('url', '')}"
            if detail_str:
                why_str += f" — {detail_str}"
            return (
                "errored",
                {
                    "type": "network_error",
                    "to_url": None,
                    "errors": [{"url": e["url"], "status": e["status"], "detail": (e.get("detail") or "")[:120]}
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
SOURCE_FACT = "fact"
SOURCE_EVIDENCE = "evidence"
SOURCE_INFERENCE = "inference"
SOURCE_UNTRUSTED = "untrusted"

# 统一后果卡字段 → 来源(白名单,不随页面内容变化;键=实际卡片字段,支持点分路径)
CARD_SOURCE_RULES = {
    "page.before_url": SOURCE_FACT,
    "page.after_url": SOURCE_FACT,
    "page.url_changed": SOURCE_FACT,
    "page.state": SOURCE_FACT,
    "changes_seq": SOURCE_FACT,
    "evidence_seq": SOURCE_FACT,
    "world_epoch": SOURCE_FACT,
    "target.id": SOURCE_FACT,
    "target.fingerprint": SOURCE_FACT,
    "target.name": SOURCE_UNTRUSTED,
    "why": SOURCE_EVIDENCE,
    "effect.verdict": SOURCE_EVIDENCE,
    "effect.observed": SOURCE_EVIDENCE,
    "overlays": SOURCE_EVIDENCE,
    "situation.type": SOURCE_INFERENCE,
    "next.suggested": SOURCE_INFERENCE,
    "recipes": SOURCE_INFERENCE,
    "handoff": SOURCE_INFERENCE,
    "error": SOURCE_EVIDENCE,
}


def _sources_for_card(card):
    """按白名单为卡片字段打来源标签(支持点分路径如 page.url/target.name)。"""
    out = {}
    for path, tag in CARD_SOURCE_RULES.items():
        node = card
        ok = True
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                ok = False
                break
            node = node[part]
        if ok:
            out[path] = tag
    return out


def _anomaly_from_counts(visible_dom, world_count):
    """环境异常纯判定(与 _status 同口径):可见 DOM 远多于世界元素即异常。
    阈值 35%/50 个沿用状态卡实战值(Booking.com 误报教训),改动须两处同步。"""
    try:
        return bool(visible_dom and visible_dom > 50 and (world_count or 0) < visible_dom * 0.35)
    except Exception:
        return False


def _anomaly_check(wid):
    """供小票 page.anomaly 的轻量检测:主 frame 可见元素 vs 世界元素数。
    任何失败默认 False(宁可漏报,不误报)。每次动作约 +2 次 evaluate。"""
    try:
        w = _world(int(wid))
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
    w = _world(int(wid))
    try:
        before = before_signal or _page_signal_snapshot(int(wid))
    except Exception:
        before = {}
    try:
        after = _page_signal_snapshot(int(wid))
    except Exception:
        after = {}
    # 保留旧 feedback/全局纠正逻辑(URL/弹窗覆盖局部判定)
    ret = _finalize_click_result(int(wid), ret, before, after)

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

    # 疑似提交动作判定(challenged 检测的前置):click 目标为 form/submit;
    # press 仅 Enter 且目标在 form 内;其余动作不启用
    submit_trigger = False
    resolved = None
    ent = None
    target_arg = args.get("id")
    if target_arg and action != "world_navigate":
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
    elif page_outcome == "errored" and situation.get("type") == "network_error":
        err_info = (situation.get("errors") or [{}])[0]
        status_code = err_info.get("status", "")
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
    if situation.get("type") in ("network_error", "console_error") and "errors" in situation:
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
                        checked: top.hasAttribute('checked'),
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
STYLE_DIFF_PROPS = ("backgroundColor", "color", "opacity", "visibility",
                    "display", "transform", "borderTopColor")
STYLE_SNAPSHOT_MAX = 40


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
                        checked: n.hasAttribute('checked'),
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
                        checked: n.hasAttribute('checked'),
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


def _target_state_flip(before_state, after_state):
    """目标自身状态是否翻转(状态切换类交互的证据,如 tab 的 aria-selected、
    折叠的 aria-expanded、勾选 checked)。返回 (flipped, what)"""
    if not before_state or not after_state:
        return False, None
    for key in ("ariaSelected", "ariaExpanded", "checked"):
        b = before_state.get(key)
        a = after_state.get(key)
        if b is not None or a is not None:
            if (b or None) != (a or None):
                return True, key
    # className 变化(弱信号,仅当前面三个都无差异时考虑)
    bc = (before_state.get("className") or "").strip()
    ac = (after_state.get("className") or "").strip()
    if bc and ac and bc != ac:
        return True, "className"
    return False, None


def _build_click_effect(before_rows, after_rows, url_changed=False, before_dialogs=None,
                        after_dialogs=None, before_target_state=None, after_target_state=None,
                        disappear_ok=False, fill_verified=False):
    """空间区域 diff → 操作生效报告。
    判定优先级(从强到弱):
      1. fill_verified: 填表值已进入可见输入框 → effected/high(填表专属强证据)
      2. URL 变化 → effected/high(导航/提交类)
      3. 全页出现"新的可见 dialog/menu"(点击前没有、点击后有)→ effected/high
         —— 远距弹窗兜底:弹窗出现在 ±200px 区域外时,靠全页 dialog 扫描识别(F1 修复)
      4. disappear_ok 且"点击前有可见 dialog、点击后没了" → effected/high
         —— 按键关闭弹窗兜底:按 Escape 关弹窗 = 弹窗消失 = 生效
      5. 目标自身状态翻转(aria-selected/aria-expanded/checked/class)→ effected/high
         —— 状态切换类交互兜底:tab/折叠/勾选无新构件,只有目标状态变
      6. 目标区域新增关键构件(dialog/button/menu/option 等)→ effected/high
      7. 区域有变化但无关键构件 → changed/medium
      8. 区域无变化+URL 未变 → no-change
    """
    before_ids = {r[0] for r in before_rows}
    after_ids = {r[0] for r in after_rows}
    new_rows = [r for r in after_rows if r[0] not in before_ids]
    gone_rows = [r for r in before_rows if r[0] not in after_ids]
    key_rows = [r for r in new_rows if r[1] in _IMPORTANT_ROLES]

    observed = []
    for r in key_rows[:8]:
        observed.append({"type": "add", "id": r[0], "semantic": r[1], "name": r[2]})

    # 全页新出现 dialog/menu 兜底(远距弹窗 F1 修复)
    before_d = set((d[0] for d in before_dialogs or []))
    new_dialogs = [d for d in (after_dialogs or []) if d[0] not in before_d]
    # 全页消失的 dialog(按键关闭弹窗兜底)
    after_d = set((d[0] for d in after_dialogs or []))
    gone_dialogs = [d for d in (before_dialogs or []) if d[0] not in after_d]

    if fill_verified:
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": "填表值已进入可见输入框",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if url_changed:
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": "URL 变化(导航/提交类)",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if new_dialogs:
        names = "、".join(f"{_ROLE_LABEL.get(d[1], d[1])} {d[2]}" for d in new_dialogs[:5])
        for d in new_dialogs[:8]:
            if not any(o["id"] == d[0] for o in observed):
                observed.append({"type": "add", "id": d[0], "semantic": d[1], "name": d[2]})
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"页面出现新的弹窗/菜单(可能远离目标): {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if disappear_ok and gone_dialogs:
        names = "、".join(f"{_ROLE_LABEL.get(d[1], d[1])} {d[2]}" for d in gone_dialogs[:5])
        for d in gone_dialogs[:8]:
            observed.append({"type": "remove", "id": d[0], "semantic": d[1], "name": d[2]})
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"弹窗/菜单已关闭: {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    state_flip, state_key = _target_state_flip(before_target_state, after_target_state)
    if state_flip:
        label = {"ariaSelected": "选中态(aria-selected)", "ariaExpanded": "展开态(aria-expanded)",
                 "checked": "勾选(checked)", "className": "样式(className)"}.get(state_key, state_key)
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"目标自身状态变化: {label} 翻转",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if key_rows:
        names = "、".join(f"{_ROLE_LABEL.get(r[1], r[1])} {r[2]}" for r in key_rows[:5])
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"目标区域出现关键构件: {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if new_rows or gone_rows:
        return {
            "verdict": "changed",
            "confidence": "medium",
            "why": "目标区域有变化但无关键交互构件",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    return {
        "verdict": "no-change",
        "confidence": "high",
        "why": "目标区域无变化(点击可能未生效,或效果发生在远处)",
        "observed": [],
        "region_changed": {"new": 0, "gone": 0},
    }


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

    loc = _build_locator(w, ent)
    if loc:
        try:
            # Playwright locator:自动等待可见/稳定/可点击,错误信息清晰
            loc.click(timeout=10000)
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
    page = w["page"]
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
        result = w["page"].evaluate(expr)
    except Exception as e:
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

    entities = []
    if q:
        r = _evaluate(wid, "(q) => agentWorld.query.resolve(q)", str(q)) or {}
        ids = []
        if r.get("id"):
            ids = [r["id"]]
        elif r.get("matches"):
            ids = list(r.get("matches"))[:max_results]
        for i in ids:
            ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", i)
            if ent:
                entities.append(ent)
        # 文本兜底:resolve 未命中时,按可见文本/名字做大小写不敏感子串匹配(与内核 findEntities 口径一致)
        if not entities:
            fallback = _evaluate(wid, "(q) => agentWorld.query.findEntities({text: q})", str(q)) or []
            if not fallback:
                fallback = _evaluate(wid, "(q) => agentWorld.query.findEntities({name: q})", str(q)) or []
            entities = fallback
        # q 解析后仍可叠加过滤器(角色/文本/可交互),过滤候选
        if filters:
            entities = [e for e in entities if _entity_match(e, filters)]
    else:
        entities = _evaluate(wid, "(f) => agentWorld.query.findEntities(f)", filters) or []

    matches = [{
        "id": e.get("id"),
        "name": e.get("name"),
        "semantic": e.get("semantic"),
        "fingerprint": e.get("fingerprint"),
        "bounds": e.get("bounds"),
        "interactive": e.get("interactive"),
        # F2 来源标记:页面自由文本字段(name/text/aria-label/placeholder)默认 untrusted
        "sources": {
            "id": SOURCE_FACT,
            "fingerprint": SOURCE_FACT,
            "bounds": SOURCE_FACT,
            "semantic": SOURCE_FACT,
            "name": SOURCE_UNTRUSTED,
        },
    } for e in entities[:max_results] if e.get("id")]
    interactive_hits = [m for m in matches if m.get("interactive")]
    return _ok({
        "world_id": wid,
        "count": len(matches),
        "ambiguous": len(interactive_hits) > 1,
        "matches": matches,
    })


def _entity_match(e, filters):
    """world_find 的候选后置过滤(小集合内精确过滤,复用内核语义口径)。"""
    if "role" in filters and (e.get("semantic") or "") != filters["role"]:
        return False
    if "tag" in filters and (e.get("tag") or "").lower() != str(filters["tag"]).lower():
        return False
    if "name" in filters and filters["name"] not in (e.get("name") or ""):
        return False
    if "text" in filters and filters["text"] not in (e.get("text") or ""):
        return False
    if "interactive" in filters and bool(e.get("interactive")) != bool(filters["interactive"]):
        return False
    if "inViewport" in filters and bool(e.get("inViewport")) != bool(filters["inViewport"]):
        return False
    return True


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
        cards = []
        for idx, step in enumerate(steps):
            try:
                before = _page_signal_snapshot(wid)
            except Exception:
                before = None
            try:
                res = _act_one(wid, step, before)
                card = _result_payload(res)
            except Exception as e:
                card = _result_payload(_errored_card(wid, f"world_act.step{idx + 1}", step, before, e))
            cards.append(card)
            if card.get("page_outcome") == "errored":
                break
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
        return _ok(last)
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
    })


def _t_world_close(args):
    wid = args["world_id"]
    w = _worlds.pop(int(wid), None)
    if w:
        # CDP 连接:只断开,不关闭用户浏览器,也不导出 profile(会话属于用户日常浏览器)
        if w.get("cdp_url"):
            try:
                w["handle"].close()
            except Exception:
                pass
            return _ok({"world_id": wid, "closed": True, "cdp_disconnected": True})
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
    return _ok({"world_id": wid, "closed": bool(w)})


def _t_world_list(args):
    return _ok({"worlds": [{"world_id": k, "url": v["url"], "opened_at": v["opened_at"]} for k, v in _worlds.items()]})


# ── 入口 ─────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
