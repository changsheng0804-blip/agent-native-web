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
  world_resolve  弱 ID 解析(名字/强 ID/页面原生 id)
  world_changes  变更流(增量续读,游标)
  world_click    编号驱动点击
  world_fill     编号驱动填表
  world_wait     等待构件出现/消失
  world_screenshot 局部/整页截图(视觉兜底)
  world_close    关闭世界
  world_list     列出已打开的世界

运行:python server.py  (stdio 模式,由 MCP 客户端拉起)
"""
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

ALL_IN_ONE = Path(__file__).parent.parent / "agent-runtime-extension-v1.1-blueprint" / "all-in-one.js"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
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
@server.list_tools()
async def list_tools():
    return [
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
            description="构件清单(图纸构件表):按角色/标签/文本/名字/可交互/视口过滤查询元素,返回编号、名字、坐标。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer", "description": "世界 ID(world_open 返回)"},
                    "role": {"type": "string", "description": "语义角色,如 button/link/input/combobox/heading/navigation"},
                    "tag": {"type": "string", "description": "HTML 标签,如 a/button/input/div"},
                    "text": {"type": "string", "description": "文本包含(子串匹配)"},
                    "name": {"type": "string", "description": "名字包含(如 round-trip 匹配 combobox.round-trip)"},
                    "interactive": {"type": "boolean", "description": "是否可交互"},
                    "in_viewport": {"type": "boolean", "description": "是否在当前视口内"},
                    "max_results": {"type": "integer", "description": "最多返回条数", "default": 100},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_entity",
            description="单个构件详情:编号、名字、坐标、语义、文本、可交互、邻居(上下左右)、所在区域。",
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
            description="变更流(网页视频):读取自 since 序号以来的页面变化事件(add/remove/update/visibility),增量续读不重不漏。",
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
            name="world_click",
            description="按编号点击元素(原生 click 事件)。带遮挡检测与自动等待。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
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
            description="截图:整页或指定构件区域。保存到本地文件,返回文件路径。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号(可选,不填截整页)"},
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
    ]


# ── 工具实现 ─────────────────────────────────────────────────
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        # 全部在 executor 线程执行(Playwright 同步 API 线程亲和)
        return await asyncio.get_event_loop().run_in_executor(None, _impl_with_status, name, arguments)
    except Exception as e:
        traceback.print_exc()
        return [types.TextContent(type="text", text=f"错误: {e}")]


def _impl_with_status(name, args):
    result = _impl(name, args)
    return _inject_status(result, args.get("world_id"))


# ── 世界状态卡(仪表盘)────────────────────────────────────────
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
    anomaly = False
    if visible_dom > 50 and world_count < visible_dom * 0.35:
        anomaly = True
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


def _inject_status(result, wid):
    """给工具返回 JSON 注入状态卡(所有工具统一附带)"""
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
                    data["status"] = _status(wid_i)
                    item.text = json.dumps(data, ensure_ascii=False, indent=2)
            except Exception:
                pass
    return result


def _impl(name, args):
    if name == "world_open":
        return _t_world_open(args)
    if name == "world_entities":
        return _t_world_entities(args)
    if name == "world_entity":
        return _t_world_entity(args)
    if name == "world_layers":
        return _t_world_layers(args)
    if name == "world_resolve":
        return _t_world_resolve(args)
    if name == "world_changes":
        return _t_world_changes(args)
    if name == "world_click":
        return _t_world_click(args)
    if name == "world_fill":
        return _t_world_fill(args)
    if name == "world_batch_fill":
        return _t_world_batch_fill(args)
    if name == "world_press":
        return _t_world_press(args)
    if name == "world_wait":
        return _t_world_wait(args)
    if name == "world_screenshot":
        return _t_world_screenshot(args)
    if name == "world_eval":
        return _t_world_eval(args)
    if name == "world_click_at":
        return _t_world_click_at(args)
    if name == "world_navigate":
        return _t_world_navigate(args)
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
    _worlds[wid] = {"handle": handle, "context": context, "page": page, "url": page.url, "opened_at": time.time(), "profile": profile, "cdp_url": cdp_url}
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
    return _ok(ent)


def _t_world_layers(args):
    wid = args["world_id"]
    return _ok(_evaluate(wid, "agentWorld.query.layers()"))


def _t_world_resolve(args):
    wid = args["world_id"]
    return _ok(_evaluate(wid, "(q) => agentWorld.query.resolve(q)", args["query"]))


# ── 变更可读化(语义摘要 + 重要性加权)──────────────────────────
# 目标:world_changes 返回的不再是"裸事件流",而是带重要性标注 + 人话摘要的结构。
# 这是实时闭环反馈的基础设施:让智能体每轮少读、快速判断"页面发生了什么、值不值得看"。

# 交互/结构性语义角色 → 高重要性(出现/消失通常是操作结果)
_IMPORTANT_ROLES = {
    "dialog", "alertdialog", "menu", "form", "button", "input", "combobox",
    "listbox", "option", "link", "navigation", "tab", "tablist", "searchbox",
    "textbox", "select", "details", "summary", "tooltip",
}
# 内容性角色 → 中重要性
_MEDIUM_ROLES = {
    "heading", "list", "listitem", "article", "section", "region",
    "card", "banner", "contentinfo", "main", "complementary",
}


def _event_importance(evt):
    """单条变更事件的重要性分级(high/medium/low)。
    依据:事件类型(结构性 add/remove > update > visibility) × 语义角色。
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
        if semantic in _IMPORTANT_ROLES:
            return "high"
        if semantic in _MEDIUM_ROLES:
            return "medium"
        return "medium"  # 新增/移除默认中(结构变化),具体由 digest 归纳
    # update
    if semantic in _IMPORTANT_ROLES:
        return "medium"  # 交互构件更新值得看
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
    """把一批变更事件归纳成人话摘要。
    返回 {summary, counts, highlights}——智能体读 summary 就能知道"发生了什么"。
    """
    counts = {"add": 0, "remove": 0, "update": 0, "visibility": 0}
    highlights = []  # high 优先级事件(通常是操作结果的直接证据)
    for evt in events:
        etype = evt.get("type")
        if etype in counts:
            counts[etype] += 1
        if _event_importance(evt) == "high" and etype in ("add", "remove"):
            highlights.append({
                "type": etype,
                "id": evt.get("id"),
                "name": evt.get("name"),
                "semantic": evt.get("semantic"),
            })
    # summary 人话
    parts = []
    if counts["add"]:
        parts.append(f"新增 {counts['add']} 个构件")
    if counts["remove"]:
        parts.append(f"移除 {counts['remove']} 个构件")
    if counts["update"]:
        parts.append(f"更新 {counts['update']} 个构件")
    if counts["visibility"]:
        parts.append(f"可见性变化 {counts['visibility']} 次")
    summary = "、".join(parts) if parts else "无变化"
    # 高价值构件一句话(前 6 个)
    if highlights:
        names = []
        for h in highlights[:6]:
            label = _ROLE_LABEL.get(h.get("semantic"), h.get("semantic") or "构件")
            names.append(f"{label} {h.get('name') or h.get('id')}")
        summary += f"; 关键: {'、'.join(names)}"
    return {"summary": summary, "counts": counts, "highlights": highlights[:6]}


def _t_world_changes(args):
    wid = args["world_id"]
    since = int(args.get("since", 0))
    data = _evaluate(wid, "(s) => agentWorld.changes(s)", since)
    events = data.get("events", [])
    # 逐条附重要性(不新增往返:内核事件已带 name/semantic)
    for evt in events:
        evt["importance"] = _event_importance(evt)
    data["digest"] = _change_digest(events)
    return _ok(data)


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


def _click_region_snapshot(wid, target_id):
    """点击前冻结目标空间区域:以目标 bounds 中心 ±CLICK_REGION_PAD 为矩形。
    返回 (region, rows)——region 是固定坐标,点击后 target 可能消失也用它做 diff。
    附带全页可见 dialog/menu 集合(远距弹窗兜底)与目标自身状态(状态切换兜底,如 tab/折叠/勾选)。
    rows: 区域内构件 [id, semantic, name]
    dialogs: 全页可见 dialog/alertdialog/menu 构件 [id, semantic, name]
    target: {page_id, state} —— state={ariaSelected, ariaExpanded, checked, className}
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
    return json.loads(raw)


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
    while time.time() < deadline:
        time.sleep(0.2)
        try:
            rows, dialogs, target_state = _click_region_after(wid, region, page_id)
        except Exception:
            rows, dialogs, target_state = [], [], None
        if (rows, dialogs, target_state) != (last_rows, last_dialogs, last_target_state):
            last_rows, last_dialogs, last_target_state = rows, dialogs, target_state
            last_seen = time.time()
        # 区域稳定(0.4s 无变化)且距首次观察足够(让重渲染完成)即停
        if rows and (time.time() - last_seen > 0.4) and (time.time() - last_seen < 5):
            break
    url_changed = w["page"].url != url_before
    if last_rows is None:
        try:
            last_rows, last_dialogs, last_target_state = _click_region_after(wid, region, page_id)
        except Exception:
            last_rows, last_dialogs, last_target_state = [], [], None
    return _build_click_effect(before_rows, last_rows or [], url_changed,
                               before_dialogs, last_dialogs or [],
                               before_target_state, last_target_state,
                               disappear_ok, fill_verified)


def _t_world_click(args):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")

    # 点击前:冻结目标空间区域(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target)
    url_before = w["page"].url

    # 遮挡检测:检查元素中心点是否被上层弹窗/遮罩层挡住(信息提示,不改变点击行为)
    hit_info = _evaluate(
        wid,
        """(id) => {
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return null;
            el._el.scrollIntoView({ block: 'center', inline: 'center' });
            const r = el._el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return { visible: false };
            const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
            const top = document.elementFromPoint(cx, cy);
            const obscured = (top && top !== el._el && !el._el.contains(top));
            return {
                visible: true,
                obscured: Boolean(obscured),
                topTag: top ? top.tagName.toLowerCase() : null,
                topRole: top ? (top.getAttribute('role') || '') : null
            };
        }""",
        target,
    )
    obscured_note = None
    if hit_info and hit_info.get("obscured"):
        obscured_note = f"目标上方存在层级: <{hit_info.get('topTag')}> role={hit_info.get('topRole') or 'none'}"

    loc = _build_locator(w, ent)
    if loc:
        try:
            # Playwright locator:自动等待可见/稳定/可点击,错误信息清晰
            loc.click(timeout=10000)
            _refresh_core_status(wid)
            ret = {"world_id": wid, "clicked": target, "method": "locator"}
            if obscured_note:
                ret["obscured_note"] = obscured_note
            effect = _wait_click_effect(wid, snap_before, url_before)
            if effect:
                ret["effect"] = effect
            return _ok(ret)
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
    if obscured_note:
        ret["obscured_note"] = obscured_note
    effect = _wait_click_effect(wid, snap_before, url_before)
    if effect:
        ret["effect"] = effect
    return _ok(ret)


def _t_world_fill(args):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    text = args["text"]
    type_delay_ms = int(args.get("type_delay_ms", 0))
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    # 填表前:冻结目标空间区域(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target)
    url_before = w["page"].url
    loc = _build_locator(w, ent)
    if loc:
        try:
            if type_delay_ms > 0:
                # 逐字打字:模拟真实键盘输入,触发受控组件/自动联想下拉
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
                effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1500, fill_verified=True)
                if effect:
                    ret["effect"] = effect
                return _ok(ret)
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
    # js-setter 兜底路径同样验证"值是否进入可见输入框"作为生效证据
    filled_ok = _fill_visible(wid, text)
    effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1500, fill_verified=filled_ok)
    if effect:
        ret["effect"] = effect
    return _ok(ret)


def _t_world_batch_fill(args):
    """批量填入表单字段:单次 MCP 往返完成多个输入框填写。
    逐字段容错:单个字段失败记录 error 并继续,不中断整个批次。
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
    return _ok({"world_id": wid, "batch_count": len(results), "ok_count": ok_count, "results": results})


def _t_world_press(args):
    """按编号聚焦并按按键(如 Enter/Escape/Tab)。返回 effect 生效报告。"""
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    key = args["key"]
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    # 按键前:冻结目标空间区域 + URL(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target)
    url_before = w["page"].url
    # 按 key 决定是否开启"弹窗消失"证据(Escape 关弹窗/菜单 = 生效)
    disappear_ok = key.lower() in ("escape", "esc")
    loc = _build_locator(w, ent)
    if loc:
        try:
            loc.press(key, timeout=10000)
            _refresh_core_status(wid)
            ret = {"world_id": wid, "pressed": target, "key": key, "method": "locator-press"}
            effect = _wait_click_effect(wid, snap_before, url_before, disappear_ok=disappear_ok)
            if effect:
                ret["effect"] = effect
            return _ok(ret)
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
    effect = _wait_click_effect(wid, snap_before, url_before, disappear_ok=disappear_ok)
    if effect:
        ret["effect"] = effect
    return _ok(ret)


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
    path = SCREENSHOT_DIR / f"world{wid}_{int(time.time())}.png"
    if args.get("id"):
        target = _resolve_id(wid, args["id"])
        ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
        box = ent["bounds"]
        w["page"].screenshot(path=str(path), clip={"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"]})
        desc = f"构件 {target} ({ent['name']})"
    else:
        w["page"].screenshot(path=str(path), full_page=True)
        desc = "整页"
    return _ok({"world_id": wid, "target": desc, "path": str(path)})


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


def _t_world_click_at(args):
    """视口坐标点击(原生网页世界外元素兜底,坐标来自截图/视觉)"""
    wid = args["world_id"]
    x = int(args["x"])
    y = int(args["y"])
    w = _world(wid)
    w["page"].mouse.click(x, y)
    _refresh_core_status(wid)
    return _ok({"world_id": wid, "clicked_at": [x, y], "method": "mouse-coords"})


def _t_world_navigate(args):
    """世界内导航(无需关闭重开)"""
    wid = args["world_id"]
    url = args["url"]
    wait_ms = int(args.get("wait_ms", 2000))
    w = _world(wid)
    w["page"].goto(url, wait_until="domcontentloaded", timeout=60000)
    _wait_world_ready(w["page"])
    if wait_ms:
        w["page"].wait_for_timeout(wait_ms)
    summary = _evaluate(wid, "agentWorld.query.getPageSummary()")
    return _ok({"world_id": wid, "url": url, "summary": summary})


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