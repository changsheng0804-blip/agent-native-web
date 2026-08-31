# -*- coding: utf-8 -*-
"""
Agent World MCP Server
======================
把 agent-runtime-extension 的"世界模型"以 MCP 工具暴露给任何 AI agent。
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
            description="打开一个网页并建立世界模型(注入 agent-runtime)。返回世界 ID 和页面摘要。可并行打开多个世界互不干扰。headful=true 时弹出可见窗口(人工介入点:登录/验证码/真人确认);profile=名称 时使用持久化登录态(同一名称复用)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网址"},
                    "wait_ms": {"type": "number", "description": "额外等待毫秒(动态页面建议 3000-6000)", "default": 3000},
                    "headful": {"type": "boolean", "description": "是否弹出可见窗口(登录/验证码/人工确认场景用)", "default": False},
                    "profile": {"type": "string", "description": "持久化登录态名称(如 login-taobao),同一名称复用 cookie/会话;留空则不持久化"},
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
            description="按编号点击元素(原生 click 事件)。",
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
            description="按编号填入文本(优先 Playwright 原生 fill:自动等待+React 兼容;失败自动降级 JS setter+覆盖层切换)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "text": {"type": "string", "description": "要填入的文本"},
                },
                "required": ["world_id", "id", "text"],
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
            description="等待条件满足:构件出现/消失/文本变化。轮询内部世界模型,操作后验证结果的利器。",
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
    """聚合世界状态卡(内核状态 + 登录态),附带变化高亮"""
    w = _world(wid)
    try:
        core = _evaluate(wid, "() => agentWorld.query.getStatus()")
    except Exception:
        core = {"dialogs": [], "page": {}, "forms": [], "world": {}}
    cur = {
        "auth": _auth_status(wid),
        "dialogs": core.get("dialogs", []),
        "page": core.get("page", {}),
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
    if name == "world_press":
        return _t_world_press(args)
    if name == "world_wait":
        return _t_world_wait(args)
    if name == "world_screenshot":
        return _t_world_screenshot(args)
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
    url = args["url"]
    wait_ms = int(args.get("wait_ms", 3000))
    headful = bool(args.get("headful", False))
    profile = args.get("profile") or None
    pw = _get_pw()
    if profile:
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
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if wait_ms:
        page.wait_for_timeout(wait_ms)
    ready = _wait_world_ready(page)
    if not ready:
        handle.close()
        raise ValueError(f"世界注入失败(页面可能拦截了脚本): {url}")
    wid = _next_world_id
    _next_world_id += 1
    _worlds[wid] = {"handle": handle, "context": context, "page": page, "url": url, "opened_at": time.time(), "profile": profile}
    summary = _evaluate(wid, "agentWorld.query.getPageSummary()")
    return _ok({"world_id": wid, "url": url, "ready": True, "headful": headful, "profile": profile, "summary": summary})


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


def _t_world_changes(args):
    wid = args["world_id"]
    since = int(args.get("since", 0))
    return _ok(_evaluate(wid, "(s) => agentWorld.changes(s)", since))


def _build_locator(w, ent):
    """根据世界模型元素信息构建 Playwright locator(行动层整合)。
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

    # 1. 页面原生 id(精确唯一)
    if attrs.get("id"):
        loc = page.locator(f'[id="{attrs["id"]}"]')
        if _count(loc) == 1:
            return loc
    # 2. placeholder 属性(输入框常见)
    if attrs.get("placeholder"):
        loc = page.locator(f'[placeholder="{attrs["placeholder"]}"]')
        if _count(loc) == 1:
            return loc
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
    # 4. 文本唯一匹配(短文本)
    if text and 1 <= len(text) <= 50:
        loc = page.get_by_text(text, exact=True)
        if _count(loc) == 1:
            return loc
        loc = page.get_by_text(text, exact=False)
        if _count(loc) == 1:
            return loc
    return None


def _refresh_core_status(wid, settle_ms=300):
    """操作后等防抖+渲染,主动刷新内核状态(状态卡反映操作结果)"""
    w = _world(wid)
    time.sleep(settle_ms / 1000)
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }")
    except Exception:
        pass


def _t_world_click(args):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    loc = _build_locator(w, ent)
    if loc:
        try:
            # Playwright locator:自动等待可见/稳定/可点击,错误信息清晰
            loc.click(timeout=10000)
            _refresh_core_status(wid)
            return _ok({"world_id": wid, "clicked": target, "method": "locator"})
        except Exception as e:
            loc_err = f"{type(e).__name__}: {str(e)[:200]}"
    else:
        loc_err = "no-locator"
    # 兜底:坐标鼠标手势(世界模型实时 rect + scrollIntoView)
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
    return _ok({"world_id": wid, "clicked": target, "method": "mouse-gesture", "at": [cx, cy], "locator_note": loc_err})


def _t_world_fill(args):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    text = args["text"]
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    loc = _build_locator(w, ent)
    if loc:
        try:
            # Playwright fill:自动等待 + React 兼容输入 + 清晰错误
            loc.fill(text, timeout=10000)
            _refresh_core_status(wid)
            return _ok({"world_id": wid, "filled": target, "text": text, "method": "locator-fill"})
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
    return _ok({"world_id": wid, "filled": target, "text": text, "target_tag": r.get("tag"), "method": "js-setter", "locator_note": fill_err})


def _t_world_press(args):
    """按编号聚焦并按按键(如 Enter/Escape/Tab)"""
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    key = args["key"]
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    loc = _build_locator(w, ent)
    if loc:
        try:
            loc.press(key, timeout=10000)
            return _ok({"world_id": wid, "pressed": target, "key": key, "method": "locator-press"})
        except Exception as e:
            raise ValueError(f"按键失败: {type(e).__name__}: {str(e)[:200]}")
    # 兜底:JS focus + dispatch keydown/keyup
    ok = _evaluate(
        wid,
        """(args) => {
            const el = agentWorld._runtime.world.elements.get(args.id);
            if (!el) return false;
            const node = el._el.querySelector('input, textarea') || el._el;
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
    return _ok({"world_id": wid, "pressed": target, "key": key, "method": "js-keydown"})


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
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        found = _evaluate(wid, "(f) => agentWorld.query.findEntities(f).length", f)
        if mode == "appear" and found > 0:
            return _ok({"world_id": wid, "matched": True, "mode": mode, "filter": f, "count": found})
        if mode == "disappear" and found == 0:
            return _ok({"world_id": wid, "matched": True, "mode": mode, "filter": f, "count": 0})
        time.sleep(0.3)
    return _ok({"world_id": wid, "matched": False, "mode": mode, "filter": f, "timeout_ms": timeout_ms})


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


def _t_world_close(args):
    wid = args["world_id"]
    w = _worlds.pop(int(wid), None)
    if w:
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