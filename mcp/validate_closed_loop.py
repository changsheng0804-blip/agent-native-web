# -*- coding: utf-8 -*-
"""实时闭环反馈 · 实战验证脚本(配置驱动)
每个场景:打开页面 → 定位目标 → 执行动作 → 读 effect → 用 truth oracle(DOM/URL 直查)判定真值
→ 归类 TP/FP/FN/AM → 汇总矩阵 + 生成 validate_report.md

Truth Oracle 是上帝视角:直接查 DOM/URL,与 effect 的空间区域推断解耦。
分类:
  TP   Truth 生效 ∧ verdict=effected
  TN   Truth 未生效 ∧ verdict=no-change
  FP   Truth 未生效 ∧ verdict=effected  (最危险:agent 误以为成功)
  FN   Truth 生效   ∧ verdict=no-change (agent 误以为失败)
  AM   Truth 生效   ∧ verdict=changed    (低置信,可接受但应改进)
  AMs  Truth 未生效 ∧ verdict=changed    (区域有变化但页面没生效,值得记录)
通过线:TP+TN ≥ 80% 且 FP=0
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# ── Truth oracle 通用表达式 ──────────────────────────────────
TRUTH_DIALOG_VISIBLE = """() => {
    const nodes = document.querySelectorAll('[role="dialog"], [aria-modal="true"]');
    for (const n of nodes) {
        const r = n.getBoundingClientRect();
        const s = getComputedStyle(n);
        if (r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden') return true;
    }
    return false;
}"""

TRUTH_DIALOG_GONE = """() => {
    const nodes = document.querySelectorAll('[role="dialog"], [aria-modal="true"]');
    for (const n of nodes) {
        const r = n.getBoundingClientRect();
        const s = getComputedStyle(n);
        if (r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden') return false;
    }
    return true;
}"""

TRUTH_TAB_B_SELECTED = """() => {
    const t = document.getElementById('tab-b');
    return !!t && t.getAttribute('aria-selected') === 'true';
}"""


def truth_input_has_text(text):
    """填表 truth:任意可见输入框的值包含目标文本"""
    return f"""() => {{
        const t = {json.dumps(text, ensure_ascii=False)};
        const ns = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(n => {{
            const r = n.getBoundingClientRect();
            const s = getComputedStyle(n);
            return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
        }});
        return ns.some(n => (n.value || '').includes(t));
    }}"""

# ── 场景配置 ─────────────────────────────────────────────────
CASES = [
    {
        "name": "gf-passenger(就近弹窗基线)",
        "url": "https://www.google.com/travel/flights",
        "open_wait_ms": 4000,
        "find": {"name": "passenger"},
        "action": "click",
        "action_text": None,
        "truth_mode": "dialog",
        "expect": "effected",
        "note": "基准:弹窗出现在按钮附近,应 TP",
    },
    {
        "name": "far-modal(远距弹窗)",
        "url": (FIXTURES / "far_modal.html").as_uri(),
        "open_wait_ms": 1200,
        "find": {"text": "打开居中弹窗"},
        "action": "click",
        "action_text": None,
        "truth_mode": "dialog",
        "expect": "effected",
        "note": "角落按钮→居中弹窗(离按钮 ~600px),专测 ±200px 是否漏判",
    },
    {
        "name": "tabs(标签切换无弹窗)",
        "url": (FIXTURES / "tabs.html").as_uri(),
        "open_wait_ms": 1200,
        "find": {"name": "tab-b"},
        "action": "click",
        "action_text": None,
        "truth_mode": "tab-b-selected",
        "expect": "effected",
        "note": "无 dialog,SPA 式切换,专测是否误判 effected(FP)或漏判",
    },
    {
        "name": "negative-heading(负例)",
        "url": (FIXTURES / "dyn.html").as_uri(),
        "open_wait_ms": 1000,
        "find": {"text": "动态测试页", "role": "heading"},
        "action": "click",
        "action_text": None,
        "truth_mode": "none",
        "expect": "no-change",
        "note": "点击无副作用标题,应 TN 不误报",
    },
    {
        "name": "baidu-submit(表单提交→URL)",
        "url": "https://www.baidu.com/",
        "open_wait_ms": 3000,
        "find": {"role": "textbox"},
        "action": "fill_submit",
        "action_text": "python 教程",
        "truth_mode": "url_change",
        "expect": "effected",
        "note": "填搜索框+Enter → URL 变化,专测 H3 URL 分支",
    },
    {
        "name": "wiki-link(常规链接跳转)",
        "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "open_wait_ms": 3000,
        "find": {"role": "link", "text": "Guido van Rossum"},
        "action": "click",
        "action_text": None,
        "truth_mode": "url_change",
        "expect": "effected",
        "note": "点正文链接 → URL 变化(导航类)",
    },
    {
        "name": "fill-dyn(填表值进入输入框)",
        "url": (FIXTURES / "dyn.html").as_uri(),
        "open_wait_ms": 1200,
        "find": {"name": "搜索"},
        "action": "fill",
        "action_text": "hello-agent",
        "truth_mode": "input_value",
        "truth_text": "hello-agent",
        "expect": "effected",
        "note": "填表后值应进入可见输入框(fill_verified 强证据)",
    },
    {
        "name": "press-escape(按键关闭弹窗)",
        "url": (FIXTURES / "far_modal.html").as_uri(),
        "open_wait_ms": 1200,
        "setup": {"find": {"text": "打开居中弹窗"}, "action": "click"},
        "find": {"text": "FAR_MODAL_TITLE"},
        "action": "press",
        "action_key": "Escape",
        "truth_mode": "dialog_gone",
        "expect": "effected",
        "note": "先点开弹窗再按 Escape → 弹窗应消失(disappear 信号)",
    },
    {
        "name": "wiki-search(填搜索框触发联想)",
        "url": "https://en.wikipedia.org/wiki/Main_Page",
        "open_wait_ms": 3000,
        "find": {"role": "input"},
        "action": "fill",
        "action_text": "Python programming",
        "truth_mode": "input_value",
        "truth_text": "Python programming",
        "expect": "effected",
        "note": "真站填搜索框 → 值进入 + 联想下拉出现(填表生效报告)",
    },
    {
        "name": "github-repo-tab(仓库标签切换→URL)",
        "url": "https://github.com/git/git",
        "open_wait_ms": 3500,
        "find": {"text": "Issues"},
        "action": "click",
        "action_text": None,
        "truth_mode": "url_change",
        "expect": "effected",
        "note": "GitHub 仓库点 Issues 标签 → URL 变 /issues(导航类)",
    },
    {
        "name": "so-search(填搜索框触发联想)",
        "url": "https://stackoverflow.com/",
        "open_wait_ms": 3000,
        "find": {"role": "textbox"},
        "action": "fill",
        "action_text": "playwright python",
        "truth_mode": "input_value",
        "truth_text": "playwright python",
        "expect": "effected",
        "note": "SO 填搜索框 → 值进入(联想下拉出现为加分信号)",
    },
    {
        "name": "amazon-search(填搜索框)",
        "url": "https://www.amazon.com/",
        "open_wait_ms": 4000,
        "find": {"role": "searchbox"},
        "action": "fill",
        "action_text": "mechanical keyboard",
        "truth_mode": "input_value",
        "truth_text": "mechanical keyboard",
        "expect": "effected",
        "note": "亚马逊重型电商填搜索框 → 值进入(重渲染下 digest 表现)",
    },
    {
        "name": "bbc-headline(点头条链接→URL)",
        "url": "https://www.bbc.com/",
        "open_wait_ms": 4000,
        "find": {"role": "link"},
        "action": "click",
        "action_text": None,
        "truth_mode": "url_change",
        "expect": "effected",
        "note": "BBC 重型新闻页点头条 → URL 变化(多 iframe 下 digest 表现)",
    },
]

# 持续变更 + waitFor 专项(独立段,不计入矩阵)
WAITFOR_CASES = [
    {
        "name": "waitfor-dyn(持续变更下 waitFor)",
        "url": (FIXTURES / "dyn.html").as_uri(),
        "open_wait_ms": 1200,
        "wait_text": "动态项 8",   # dyn.html 生成 "动态项 <n>"(空格,非连字符)
        "timeout_ms": 10000,
    },
]


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


def classify(truth, verdict):
    if truth:
        if verdict == "effected":
            return "TP"
        if verdict == "no-change":
            return "FN"
        return "AM"
    else:
        if verdict == "effected":
            return "FP"
        if verdict == "no-change":
            return "TN"
        return "AMs"


async def truth_check(session, wid, mode, text=None):
    """Truth oracle:返回 (truth, detail)。mode: dialog / dialog_gone / tab-b-selected / url_change / input_value / none"""
    if mode == "none":
        return False, "无变化"
    if mode == "url_change":
        href = await call(session, "world_eval", {"world_id": wid, "expression": "() => location.href"})
        return True, f"url={href.get('result', '')[:120]}"  # 由调用方对比 before/after
    if mode == "dialog":
        r = await call(session, "world_eval", {"world_id": wid, "expression": TRUTH_DIALOG_VISIBLE})
        return bool(json.loads(r.get("result", "false"))), "dialog可见" if json.loads(r.get("result", "false")) else "无可见dialog"
    if mode == "dialog_gone":
        r = await call(session, "world_eval", {"world_id": wid, "expression": TRUTH_DIALOG_GONE})
        return bool(json.loads(r.get("result", "false"))), "无可见dialog" if json.loads(r.get("result", "false")) else "dialog仍可见"
    if mode == "tab-b-selected":
        r = await call(session, "world_eval", {"world_id": wid, "expression": TRUTH_TAB_B_SELECTED})
        return bool(json.loads(r.get("result", "false"))), "tab-b选中" if json.loads(r.get("result", "false")) else "tab-b未选中"
    if mode == "input_value":
        r = await call(session, "world_eval", {"world_id": wid, "expression": truth_input_has_text(text or "")})
        return bool(json.loads(r.get("result", "false"))), f"输入框含值: {text}" if json.loads(r.get("result", "false")) else f"输入框未含值: {text}"
    return False, "?"


async def pick_target(session, wid, find):
    """在原生网页世界中找目标构件:优先 interactive"""
    f = dict(find)
    f["maxResults"] = 30
    r = await call(session, "world_entities", {"world_id": wid, **f})
    ents = r.get("entities", [])
    if not ents:
        return None, None
    for e in ents:
        if e.get("interactive"):
            return e["id"], e["name"]
    return ents[0]["id"], ents[0]["name"]


async def run_case(session, case):
    name = case["name"]
    try:
        r = await call(session, "world_open", {"url": case["url"], "wait_ms": case["open_wait_ms"]})
        wid = r["world_id"]
    except Exception as e:
        return {"name": name, "class": "SKIP", "verdict": None, "confidence": None, "truth": None, "why": f"world_open失败: {type(e).__name__}: {str(e)[:120]}", "action_note": "", "truth_detail": "", "note": case.get("note", "")}

    # 动作前 URL(供 url_change 对比)
    url_before = None
    if case.get("truth_mode") == "url_change":
        try:
            r = await call(session, "world_eval", {"world_id": wid, "expression": "() => location.href"})
            url_before = r.get("result")
        except Exception:
            url_before = None

    # 前置步骤(如先点开弹窗,再测按键关弹窗)
    setup = case.get("setup")
    if setup:
        setup_id, setup_name = await pick_target(session, wid, setup["find"])
        if setup_id:
            if setup["action"] == "click":
                await call(session, "world_click", {"world_id": wid, "id": setup_id})
            await asyncio.sleep(1.0)

    target_id, target_name = await pick_target(session, wid, case["find"])
    if not target_id:
        await call(session, "world_close", {"world_id": wid})
        return {"name": name, "class": "SKIP", "verdict": None, "confidence": None, "truth": None, "why": "找不到目标构件", "action_note": "", "truth_detail": "", "note": case.get("note", "")}

    # 执行动作
    effect = None
    action_note = ""
    try:
        if case["action"] == "click":
            r = await call(session, "world_click", {"world_id": wid, "id": target_id})
            effect = r.get("effect")
        elif case["action"] == "fill":
            r = await call(session, "world_fill", {"world_id": wid, "id": target_id, "text": case["action_text"]})
            effect = r.get("effect")
            action_note = f"fill method={r.get('method')}"
        elif case["action"] == "fill_submit":
            r = await call(session, "world_fill", {"world_id": wid, "id": target_id, "text": case["action_text"]})
            effect = r.get("effect")
            action_note = f"fill method={r.get('method')}"
            await call(session, "world_press", {"world_id": wid, "id": target_id, "key": "Enter"})
        elif case["action"] == "press":
            r = await call(session, "world_press", {"world_id": wid, "id": target_id, "key": case["action_key"]})
            effect = r.get("effect")
            action_note = f"press {case['action_key']}"
    except Exception as e:
        await call(session, "world_close", {"world_id": wid})
        return {"name": name, "class": "SKIP", "verdict": None, "confidence": None, "truth": None, "why": f"动作失败: {type(e).__name__}: {str(e)[:120]}", "action_note": "", "truth_detail": "", "note": case.get("note", "")}

    # 等待渲染(给 effect 的轮询和 truth 一个稳定窗口)
    await asyncio.sleep(1.5)

    # Truth 判定
    truth, truth_detail = await truth_check(session, wid, case["truth_mode"], case.get("truth_text"))
    if case["truth_mode"] == "url_change":
        r = await call(session, "world_eval", {"world_id": wid, "expression": "() => location.href"})
        url_after = r.get("result")
        truth = bool(url_after and url_before and url_after != url_before)
        truth_detail = f"url: {str(url_before)[:60]} → {str(url_after)[:60]}"

    verdict = effect.get("verdict") if effect else None
    confidence = effect.get("confidence") if effect else None
    cls = classify(truth, verdict)
    why = effect.get("why", "") if effect else "(无 effect,看 URL/truth)"

    await call(session, "world_close", {"world_id": wid})
    return {
        "name": name,
        "class": cls,
        "verdict": verdict,
        "confidence": confidence,
        "truth": truth,
        "why": why[:160],
        "action_note": action_note,
        "truth_detail": truth_detail,
        "evidence": (effect or {}).get("evidence"),
        "note": case.get("note", ""),
    }


async def run_waitfor_cases(session):
    print("\n=== waitFor 事件驱动专项 ===")
    for case in WAITFOR_CASES:
        name = case["name"]
        try:
            r = await call(session, "world_open", {"url": case["url"], "wait_ms": case["open_wait_ms"]})
            wid = r["world_id"]
        except Exception as e:
            print(f"[SKIP] {name}: {type(e).__name__}: {str(e)[:100]}")
            continue
        t0 = time.time()
        r = await call(session, "world_wait", {"world_id": wid, "mode": "appear", "text": case["wait_text"], "timeout_ms": case["timeout_ms"]})
        dt = time.time() - t0
        print(f"[{'PASS' if r['matched'] else 'FAIL'}] {name}: matched={r['matched']} driven={r.get('driven')} 耗时={dt:.2f}s")
        await call(session, "world_close", {"world_id": wid})


# ── digest/importance 价值评估(语义摘要 + 重要性加权)────────────
# 强信号语义:出现/消失几乎必是操作结果(弹窗/菜单/选项)
_SIGNAL_ROLES = {"dialog", "alertdialog", "menu", "option", "listbox", "combobox"}
# 弱信号语义:常被重型 SPA 整体重渲染"假新增"刷屏(页面外壳)
_CHROME_ROLES = {"button", "link", "navigation", "tab", "banner", "contentinfo", "content", "img", "nav"}


def _digest_assess(events, digest):
    """评估 digest/importance 价值:返回指标字典。
    - events_total: 变更事件总数(压缩前的量)
    - key_count: digest.key 数量(强 ID 引用数,即给 agent 的关键线索数)
    - json_len: digest 序列化体积(压缩后的量,不再用 summary 字符——已改结构化 key)
    - compression: events_total / json_len(每字节压多少条事件)
    - key: 强 ID 引用列表
    - signal_hits: key 中强信号语义数(操作结果直接证据)
    - chrome_hits: key 中弱信号/外壳语义数(重渲染噪声)
    """
    counts = digest.get("counts", {})
    events_total = len(events)
    key = digest.get("key") or []
    import json as _json
    json_len = len(_json.dumps(digest, ensure_ascii=False))
    signal_hits = [k for k in key if k.get("semantic") in _SIGNAL_ROLES]
    chrome_hits = [k for k in key if k.get("semantic") in _CHROME_ROLES and k.get("semantic") not in _SIGNAL_ROLES]
    return {
        "events_total": events_total,
        "key_count": len(key),
        "json_len": json_len,
        "compression": round(events_total / max(1, json_len), 2),
        "key": [f"{k.get('semantic')}.{k.get('name','')}@{k.get('id')}" for k in key[:6]],
        "signal_hits": len(signal_hits),
        "chrome_hits": len(chrome_hits),
        "counts": counts,
    }


async def run_digest_cases(session):
    """多真站 digest 体检:每个场景执行动作后,读变更流 digest 评估价值"""
    print("\n=== digest/importance 价值评估(语义摘要+重要性加权) ===")
    print(f"{'场景':<28s} 事件数 摘要字符 压缩比 强信号 外壳噪声")
    results = []
    for case in CASES:
        if not case["url"].startswith("http"):
            continue  # digest 价值评估只看真站
        name = case["name"]
        try:
            r = await call(session, "world_open", {"url": case["url"], "wait_ms": case["open_wait_ms"]})
            wid = r["world_id"]
        except Exception as e:
            print(f"[SKIP] {name}: {type(e).__name__}: {str(e)[:100]}")
            continue
        try:
            # 前置步骤(如先点开弹窗)
            setup = case.get("setup")
            if setup:
                sid, _ = await pick_target(session, wid, setup["find"])
                if sid:
                    await call(session, "world_click", {"world_id": wid, "id": sid})
                    await asyncio.sleep(1.0)
            # 定位并执行动作
            tid, _ = await pick_target(session, wid, case["find"])
            if not tid:
                raise ValueError("找不到目标构件")
            if case["action"] == "click":
                await call(session, "world_click", {"world_id": wid, "id": tid})
            elif case["action"] == "fill":
                await call(session, "world_fill", {"world_id": wid, "id": tid, "text": case["action_text"]})
            elif case["action"] == "fill_submit":
                await call(session, "world_fill", {"world_id": wid, "id": tid, "text": case["action_text"]})
                await call(session, "world_press", {"world_id": wid, "id": tid, "key": "Enter"})
            elif case["action"] == "press":
                await call(session, "world_press", {"world_id": wid, "id": tid, "key": case["action_key"]})
            await asyncio.sleep(1.2)
            # 读变更流 digest
            r = await call(session, "world_changes", {"world_id": wid, "since": 0})
            m = _digest_assess(r.get("events", []), r.get("digest", {}))
            # 管线闭环验证:key 里的强 ID 必须能用 world_entity 查详图(004# 圆孔→详图)
            m["pipeline_ok"] = None
            m["pipeline_first"] = None
            key = r.get("digest", {}).get("key") or []
            for k in key[:3]:
                if k.get("id"):
                    try:
                        ent = await call(session, "world_entity", {"world_id": wid, "id": k["id"]})
                        m["pipeline_ok"] = bool(ent and ent.get("bounds"))
                        m["pipeline_first"] = f"{k['id']}→bounds={ent.get('bounds')} semantic={ent.get('semantic')}"
                        break
                    except Exception:
                        m["pipeline_ok"] = False
            m["name"] = name
            results.append(m)
            print(f"{name:<28s} {m['events_total']:>5d} {m['key_count']:>4d} {m['json_len']:>5d} {m['compression']:>6.2f} {m['signal_hits']:>4d} {m['chrome_hits']:>6d} 管线={m['pipeline_ok']}")
            print(f"    key: {m['key'][:5]}")
        except Exception as e:
            print(f"[SKIP] {name}: {type(e).__name__}: {str(e)[:100]}")
        await call(session, "world_close", {"world_id": wid})
    # 汇总
    total_events = sum(r["events_total"] for r in results)
    total_json = sum(r["json_len"] for r in results)
    total_signal = sum(r["signal_hits"] for r in results)
    total_chrome = sum(r["chrome_hits"] for r in results)
    pipeline_ok = sum(1 for r in results if r.get("pipeline_ok"))
    print(f"\n汇总: 总事件 {total_events} → 结构化 digest {total_json} 字符, 全局压缩比 {round(total_events/max(1,total_json),2)} 事件/字符")
    print(f"      key 中强信号(操作结果) {total_signal} 条 vs 外壳/弱信号(噪声) {total_chrome} 条")
    print(f"      key 强 ID 可 world_entity 查详图: {pipeline_ok}/{len(results)}")
    if total_chrome > total_signal:
        print("⚠️ 外壳噪声 > 强信号:importance 对重型 SPA 的重渲染假新增降权不足(digest 价值受限)")
    else:
        print("✅ 强信号 ≥ 噪声:importance 加权有效(digest 有真实价值)")
    return results


async def main():
    # 分阶段:先 --local(只跑本地夹具),再全量(含真实站点),--digest 只做 digest 价值评估
    digest_only = "--digest" in sys.argv
    local_only = "--local" in sys.argv
    selected = [c for c in CASES if local_only and c["url"].startswith("file://")] if local_only else CASES

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            if digest_only:
                await run_digest_cases(session)
                return

            results = []
            print("=== effect 判定一致性矩阵 ===")
            for case in selected:
                res = await run_case(session, case)
                results.append(res)
                print(f"[{res['class']:4s}] {res['name']}: verdict={res['verdict']} conf={res['confidence']} truth={res['truth']}")
                if res.get("why"):
                    print(f"        why: {res['why']}")
                if res.get("truth_detail"):
                    print(f"        truth: {res['truth_detail']}")
                if res.get("action_note"):
                    print(f"        action: {res['action_note']}")
                if res.get("evidence"):
                    ev = res["evidence"]
                    print(f"        证据窗: polls={ev.get('polls')} total={ev.get('total_ms')}ms first_change={ev.get('first_change_ms')}ms stop={ev.get('stop')}")

            if not local_only:
                await run_waitfor_cases(session)

            # 汇总
            print("\n=== 汇总 ===")
            tp = sum(1 for r in results if r["class"] == "TP")
            tn = sum(1 for r in results if r["class"] == "TN")
            fp = sum(1 for r in results if r["class"] == "FP")
            fn = sum(1 for r in results if r["class"] == "FN")
            am = sum(1 for r in results if r["class"] == "AM")
            ams = sum(1 for r in results if r["class"] == "AMs")
            skips = sum(1 for r in results if r["class"] == "SKIP")
            scored = [r for r in results if r["class"] not in ("SKIP",)]
            acc = (tp + tn) / len(scored) * 100 if scored else 0
            print(f"TP={tp} TN={tn} FP={fp} FN={fn} AM={am} AMs={ams} SKIP={skips}")
            print(f"准确率(TP+TN)/评分场景 = {acc:.0f}%  (通过线:≥80% 且 FP=0)")

            # 写报告
            lines = ["# 实时闭环反馈 · 实战验证报告\n"]
            lines.append(f"> 日期:2026-08-31 · 通过线:TP+TN ≥ 80% 且 FP=0\n")
            lines.append(f"## 结果\n")
            lines.append("| 场景 | 分类 | verdict | confidence | truth |")
            lines.append("|---|---|---|---|---|")
            for r in results:
                lines.append(f"| {r['name']} | {r['class']} | {r['verdict']} | {r['confidence']} | {r['truth']} |")
            lines.append("")
            lines.append(f"**汇总**: TP={tp} TN={tn} FP={fp} FN={fn} AM={am} AMs={ams} SKIP={skips}")
            lines.append(f"**准确率**: {acc:.0f}% (通过线:≥80% 且 FP=0)")
            lines.append("")
            lines.append("## 失败模式清单\n")
            for r in results:
                if r["class"] in ("FP", "FN", "AM", "AMs"):
                    lines.append(f"- **{r['class']}** {r['name']}: verdict={r['verdict']} truth={r['truth']} → {r.get('why', '')}")
            lines.append("")
            lines.append("## 逐场景细节\n")
            for r in results:
                lines.append(f"### {r['name']} [{r['class']}]")
                if r.get("why"):
                    lines.append(f"- why: {r['why']}")
                if r.get("truth_detail"):
                    lines.append(f"- truth: {r['truth_detail']}")
                if r.get("action_note"):
                    lines.append(f"- action: {r['action_note']}")
                if r.get("evidence"):
                    ev = r["evidence"]
                    lines.append(f"- 证据窗: polls={ev.get('polls')} total={ev.get('total_ms')}ms first_change={ev.get('first_change_ms')}ms stop={ev.get('stop')}")
                lines.append(f"- note: {r.get('note', '')}")
                lines.append("")
            report = Path(__file__).resolve().parent / "validate_report.md"
            report.write_text("\n".join(lines), encoding="utf-8")
            print(f"\n报告已写入: {report}")

            if fp > 0:
                print("\n⚠️ 出现 FP:暂停横向扩展,需先收紧判定(见方案第八节)")
            elif acc >= 80:
                print("\n✅ 通过线达标:可考虑横向复制(fill/press 带 effect)")


if __name__ == "__main__":
    asyncio.run(main())
