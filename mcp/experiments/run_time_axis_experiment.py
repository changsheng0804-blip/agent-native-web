# -*- coding: utf-8 -*-
"""A/B 实验:时间感知反馈 vs 现状基线(同一 MCP server,同一夹具,同一动作)。

- 基线臂(A):世界打开 → world_click → 读 server 现成判定(page_outcome / effect.verdict)
- 时间感知臂(B):动作前取页面时钟 t0 → world_click → 窗口结束后一次性读 world_changes
  → 时间感知引擎出 temporal card(时间后果卡)
- 地面真值:夹具 HTML 里每个按钮的 data-expected 属性

输出:mcp/experiments/artifacts/time_axis_report.json + 控制台对比表

运行: python mcp/experiments/run_time_axis_experiment.py
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_aware_engine import build_temporal_card, DEFAULT_WINDOW_MS

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "time_axis.html"
SERVER = Path(__file__).resolve().parent.parent / "server.py"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 场景:按钮 text 前缀 → (夹具 data-expected, 基线正确判定集合, 时间卡正确判定集合)
SCENARIOS = [
    ("s0-btn", "S0 点击后", "none",          {"unchanged"},                          {"no-change"}),
    ("s1-btn", "S1 点击后", "delayed-effect", {"progressed"},                        {"effected", "effected-delayed"}),
    ("s2-btn", "S2 点击后", "oscillation",   {"progressed", "uncertain"},            {"oscillation"}),
    ("s3-btn", "S3 点击后", "progressive",   {"progressed"},                         {"effected", "effected-delayed", "changed"}),
    ("s4-btn", "S4 点击后", "reverted",      {"uncertain", "unchanged", "errored"},  {"reverted"}),
]
RUNS_PER_ARM = 3


def ground_truths():
    html = FIXTURE.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r'<button id="([^"]+)"[^>]*data-expected="([^"]+)"', html):
        out[m.group(1)] = m.group(2)
    return out


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    return {}


async def open_world(session, tag):
    return await call(session, "world_open", {
        "url": FIXTURE.as_uri(),
        "ready_policy": "action",
        "wait_ms": 2000,
        "reuse_policy": "never",
        "task_id": f"time-axis-{tag}",
    }, timeout=90)


async def find_button(session, wid, text_prefix):
    def pick(ents):
        # 文本过滤会先命中 region 容器;必须优先语义为 button 的构件
        buttons = [e for e in ents if (e.get("semantic") or "").startswith("button")]
        return buttons or ents

    r = await call(session, "world_entities", {"world_id": wid, "text": text_prefix, "max_results": 10}, timeout=30)
    ents = pick(r.get("entities", []))
    if not ents:
        r2 = await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 40}, timeout=30)
        ents = [e for e in r2.get("entities", []) if (e.get("name") or "").startswith(text_prefix)]
    if not ents:
        raise RuntimeError(f"找不到按钮: {text_prefix}")
    return ents[0]["id"]


async def run_arm_a(session, btn_id, text_prefix):
    """基线臂:server 现成判定。"""
    opened = await open_world(session, f"A-{btn_id}")
    wid = opened["world_id"]
    try:
        ent_id = await find_button(session, wid, text_prefix)
        t0 = time.perf_counter()
        clicked = await call(session, "world_click", {"world_id": wid, "id": ent_id}, timeout=30)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        why = clicked.get("why")
        if not isinstance(why, str):
            why = (clicked.get("effect") or {}).get("why", "")
        return {
            "arm": "A-baseline",
            "server_page_outcome": clicked.get("page_outcome"),
            "server_effect_verdict": (clicked.get("effect") or {}).get("verdict"),
            "server_why": why[:120],
            "latency_ms": latency_ms,
        }
    finally:
        try:
            await call(session, "world_close", {"world_id": wid}, timeout=15)
        except Exception:
            pass


async def run_arm_b(session, btn_id, text_prefix):
    """时间感知臂:动作锚 + 窗口变更流 → 时间后果卡。"""
    opened = await open_world(session, f"B-{btn_id}")
    wid = opened["world_id"]
    try:
        ent_id = await find_button(session, wid, text_prefix)
        # 页面时钟锚(与事件 t 同基准)
        r0 = await call(session, "world_eval", {"world_id": wid, "expression": "() => Date.now()"}, timeout=15)
        t0 = r0.get("result") if isinstance(r0, dict) and "result" in r0 else r0
        t0 = float(t0)
        # 动作前游标
        before = await call(session, "world_changes", {"world_id": wid, "since": 0}, timeout=15)
        cursor = int(before.get("to", 0))
        t_click = time.perf_counter()
        clicked = await call(session, "world_click", {"world_id": wid, "id": ent_id}, timeout=30)
        click_latency_ms = int((time.perf_counter() - t_click) * 1000)
        # 窗口结束后一次性读流(内核 append-only 缓冲,不漏事件)
        deadline_ms = int(t0 + DEFAULT_WINDOW_MS + 500)
        await asyncio.sleep(max(0, (deadline_ms - time.time() * 1000) / 1000))
        after = await call(session, "world_changes", {"world_id": wid, "since": cursor}, timeout=15)
        events = after.get("events", [])
        card = build_temporal_card(f"click:{btn_id}", t0, events)
        return {
            "arm": "B-time-aware",
            "server_page_outcome": clicked.get("page_outcome"),
            "card_verdict": card.verdict,
            "card_confidence": card.confidence,
            "card_why": card.why[:140],
            "window_events": card.window_events,
            "attributed": [{"id": e.get("id"), "semantic": e.get("semantic"), "lag_ms": e.get("lag_ms")}
                           for e in card.attributed[:5]],
            "oscillation": card.oscillation,
            "stability": card.stability,
            "reversion": card.reversion[:3],
            "click_latency_ms": click_latency_ms,
            "event_fetch_ms": int((time.time() * 1000) - t0 - DEFAULT_WINDOW_MS),
        }
    finally:
        try:
            await call(session, "world_close", {"world_id": wid}, timeout=15)
        except Exception:
            pass


def verdict_match_arm_a(res, correct):
    po = res.get("server_page_outcome")
    return po in correct


def verdict_match_arm_b(res, correct):
    return res.get("card_verdict") in correct


async def main():
    ARTIFACTS.mkdir(exist_ok=True)
    truth = ground_truths()
    print(f"地面真值: {truth}")
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    results = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            for btn_id, text_prefix, expected, correct_a, correct_b in SCENARIOS:
                assert truth.get(btn_id) == expected, f"夹具 data-expected 不一致: {btn_id}"
                for run in range(1, RUNS_PER_ARM + 1):
                    a = await run_arm_a(session, btn_id, text_prefix)
                    a.update({"scenario": btn_id, "expected": expected, "run": run,
                              "match": verdict_match_arm_a(a, correct_a)})
                    results.append(a)
                    print(f"[A] {btn_id} run{run}: outcome={a['server_page_outcome']} "
                          f"effect={a['server_effect_verdict']} match={a['match']} ({a['latency_ms']}ms)")
                    b = await run_arm_b(session, btn_id, text_prefix)
                    b.update({"scenario": btn_id, "expected": expected, "run": run,
                              "match": verdict_match_arm_b(b, correct_b)})
                    results.append(b)
                    print(f"[B] {btn_id} run{run}: card={b['card_verdict']} "
                          f"server={b['server_page_outcome']} match={b['match']} "
                          f"({b.get('window_events')} evts) {b['card_why']}")

    report = {"fixture": str(FIXTURE), "runs_per_arm": RUNS_PER_ARM,
              "summary": summarize(results), "results": results}
    report_path = ARTIFACTS / "time_axis_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(report["summary"])
    print(f"\n报告: {report_path}")


def summarize(results):
    from collections import defaultdict
    agg = defaultdict(lambda: {"A": {"n": 0, "ok": 0, "outcomes": {}},
                               "B": {"n": 0, "ok": 0, "outcomes": {}}})
    for r in results:
        s = agg[r["scenario"]][r["arm"][0]]
        s["n"] += 1
        s["ok"] += 1 if r["match"] else 0
        key = r["arm"][0] == "A" and r.get("server_page_outcome") or r.get("card_verdict")
        s["outcomes"][key] = s["outcomes"].get(key, 0) + 1
    return {k: {"A": dict(v["A"]), "B": dict(v["B"])} for k, v in agg.items()}


def print_summary(summary):
    print("\n================ 汇总(判定命中率) ================")
    print(f"{'场景':<16}{'期望':<16}{'基线A':<12}{'时间感知B':<12}")
    label = {"none": "无变化", "delayed-effect": "延时生效", "oscillation": "振荡",
             "progressive": "渐进渲染", "reverted": "静默回退"}
    for scen, v in summary.items():
        a, b = v["A"], v["B"]
        print(f"{scen:<16}{label.get(scen, scen):<16}"
              f"{a['ok']}/{a['n']}      {b['ok']}/{b['n']}")
    a_tot = sum(v["A"]["ok"] for v in summary.values())
    b_tot = sum(v["B"]["ok"] for v in summary.values())
    a_n = sum(v["A"]["n"] for v in summary.values())
    b_n = sum(v["B"]["n"] for v in summary.values())
    print(f"{'总计':<16}{'':<16}{a_tot}/{a_n}      {b_tot}/{b_n}")


if __name__ == "__main__":
    asyncio.run(main())
