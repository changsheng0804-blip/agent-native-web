# -*- coding: utf-8 -*-
"""时间线簇——统一环境侧因果时间线(_tl/_tl_merge_dom/_tl_action)、前提监视(assume/ack/status)、动作证据卡(evidence)、通知注入。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖:aw_core/aw_runtime(aw_core ← aw_runtime ← 本簇)。
"""
import collections, json, threading, time
from aw_core import (
ACTION_EVIDENCE_PRE_S,
ASSUMPTION_INTERVAL_S,
ASSUMPTION_RECHECK_S,
TIMELINE_MAX,
_evidence_norm_url,
_signal_delta,
)
from aw_runtime import (
_evaluate,
_ok,
_page_signal_snapshot,
_result_payload,
_runtime_context,
_world,
_worlds,
)
from task_runtime import (
TraceStore,
build_trace_entry,
new_id,
persistence_enabled,
)
from business_runtime import (
attach_business_runtime,
)


_trace_store = TraceStore()


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
            if it["type"] in ("premise", "requestfailed") or (it["type"] == "response" and it.get("status", 0) >= 400):
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
        elif e["type"] == "requestfailed":
            failures.append({"url": e.get("url"), "error": e.get("error")})
    # 静默失败:窗口内有 4xx/5xx/failed 且无 DOM 变化(L1 盲区自动标注)
    silent = []
    if failures or any(int(s) >= 400 for s in statuses):
        if not dom_counts:
            silent = [{"4xx_5xx": {str(k): v for k, v in statuses.items() if int(k) >= 400},
                       "requestfailed": failures[:3]}]
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
