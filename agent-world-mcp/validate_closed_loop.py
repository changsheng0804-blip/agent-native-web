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
FIXTURES = Path(__file__).resolve().parent.parent / "test_fixtures"

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

TRUTH_TAB_B_SELECTED = """() => {
    const t = document.getElementById('tab-b');
    return !!t && t.getAttribute('aria-selected') === 'true';
}"""

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


async def truth_check(session, wid, mode):
    """Truth oracle:返回 (truth, detail)。mode: dialog / tab-b-selected / url_change / none"""
    if mode == "none":
        return False, "无变化"
    if mode == "url_change":
        href = await call(session, "world_eval", {"world_id": wid, "expression": "() => location.href"})
        return True, f"url={href.get('result', '')[:120]}"  # 由调用方对比 before/after
    if mode == "dialog":
        r = await call(session, "world_eval", {"world_id": wid, "expression": TRUTH_DIALOG_VISIBLE})
        return bool(json.loads(r.get("result", "false"))), "dialog可见" if json.loads(r.get("result", "false")) else "无可见dialog"
    if mode == "tab-b-selected":
        r = await call(session, "world_eval", {"world_id": wid, "expression": TRUTH_TAB_B_SELECTED})
        return bool(json.loads(r.get("result", "false"))), "tab-b选中" if json.loads(r.get("result", "false")) else "tab-b未选中"
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
        elif case["action"] == "fill_submit":
            r = await call(session, "world_fill", {"world_id": wid, "id": target_id, "text": case["action_text"]})
            action_note = f"fill method={r.get('method')}"
            await call(session, "world_press", {"world_id": wid, "id": target_id, "key": "Enter"})
            # fill 无 effect,提交后从变更/URL 看
    except Exception as e:
        await call(session, "world_close", {"world_id": wid})
        return {"name": name, "class": "SKIP", "verdict": None, "confidence": None, "truth": None, "why": f"动作失败: {type(e).__name__}: {str(e)[:120]}", "action_note": "", "truth_detail": "", "note": case.get("note", "")}

    # 等待渲染(给 effect 的轮询和 truth 一个稳定窗口)
    await asyncio.sleep(1.5)

    # Truth 判定
    truth, truth_detail = await truth_check(session, wid, case["truth_mode"])
    if case["truth_mode"] == "url_change":
        r = await call(session, "world_eval", {"world_id": wid, "expression": "() => location.href"})
        url_after = r.get("result")
        truth = bool(url_after and url_before and url_after != url_before)
        truth_detail = f"url: {str(url_before)[:60]} → {str(url_after)[:60]}"

    verdict = effect.get("verdict") if effect else None
    confidence = effect.get("confidence") if effect else None
    cls = classify(truth, verdict)
    why = effect.get("why", "") if effect else "(fill 无 effect,看 URL)"

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


async def main():
    # 分阶段:先 --local(只跑本地夹具,验证脚本/oracle 正确性),再全量(含真实站点)
    local_only = "--local" in sys.argv
    selected = [c for c in CASES if local_only and c["url"].startswith("file://")] if local_only else CASES

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

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
