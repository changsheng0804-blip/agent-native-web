# -*- coding: utf-8 -*-
"""状态信道簇——_auth_status/_status/_status_light/_inject_status(登录态探测与状态灯)。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖方向:aw_core ← aw_runtime ← 本簇。
"""
import json
try:
    from aw_core import (
    AUTH_COOKIE_HINTS,
    _anomaly_from_counts,
    )
except ImportError:
    from mcp.aw_core import (
    AUTH_COOKIE_HINTS,
    _anomaly_from_counts,
    )
try:
    from aw_runtime import (
    _evaluate,
    _task_public,
    _world,
    _worlds,
    )
except ImportError:
    from mcp.aw_runtime import (
    _evaluate,
    _task_public,
    _world,
    _worlds,
    )


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
