# -*- coding: utf-8 -*-
"""信息注入型 A/B:timeline 摘要由 harness 自动附带,不由模型主动调用。

修正 run_timeline_ab.py 的范式缺陷:
  - 真实形态中"何时给模型什么信息"由 harness 决定,不是模型"想起来"调工具
  - 两臂工具集完全相同(都不含 world_timeline)、提示词完全相同
  - 唯一变量:每步工具结果是否自动附带 [环境时间线摘要](增量, since=上次cursor)
  - 同时解决原实验的提示词不对称混淆

用法: python run_timeline_inject.py --rounds 3 --task t1,t3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ab_fixtures
from llm_agent import LlmAgent
from run_timeline_ab import (ARTIFACTS, BASE_TOOLS, MAX_STEPS, TASKS, call,
                             eval_page, judge_final, judge_price)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"

PROMPT = ("你是网页操作 agent。目标页面已经打开,直接使用工具在页面上执行任务:"
          "用 world_entities 查看元素,world_click 点击,world_fill 填表。"
          "逐步执行,完成后用文字报告结果。不要编造页面状态,只依据工具返回。")


def compact_timeline(tl, max_windows=2):
    """把 world_timeline digest 压缩成一行(控制 token 成本)。"""
    parts = []
    counts = tl.get("counts") or {}
    if counts:
        parts.append("事件:" + ",".join(f"{k}={v}" for k, v in sorted(counts.items())))
    statuses = tl.get("statuses") or {}
    if statuses:
        parts.append("状态码:" + ",".join(f"{k}×{v}" for k, v in sorted(statuses.items())))
    apis = tl.get("api_hits") or []
    if apis:
        parts.append("接口:" + ";".join(f"{a.get('url','')[:40]}→{a.get('status')}" for a in apis[:3]))
    dom = tl.get("dom_changes") or {}
    if dom:
        parts.append("DOM:" + ",".join(f"{k}={v}" for k, v in sorted(dom.items())))
    # 语义化:最近 DOM 变化的明细(name 含新文本,如 content.1200 = "内容:1200")
    rd = tl.get("recent_dom") or []
    if rd:
        names = [f"{d.get('semantic') or '?'}={d.get('name') or ''}" for d in rd]
        parts.append("DOM变化:" + "; ".join(names[:3]))
    if tl.get("premises"):
        parts.append(f"前提失效:{len(tl['premises'])}条")
    if tl.get("failures"):
        parts.append(f"请求失败:{len(tl['failures'])}条")
    if tl.get("silent_failures"):
        parts.append("⚠静默失败(有4xx/失败但页面无变化)")
    for w in (tl.get("causal_windows") or [])[:max_windows]:
        if w.get("counts"):
            key = w.get("key") or []
            kstr = ";".join(f"{k.get('type')}:{k.get('url','')[:30]}→{k.get('status')}" for k in key[:2]) if key else "无关键项"
            parts.append(f"窗口[{w.get('action')}]:{json.dumps(w['counts'], ensure_ascii=False)} key={kstr}")
    return "; ".join(parts)


class InjectAgent(LlmAgent):
    """同 LlmAgent,但每步工具结果附带 timeline 增量摘要(inject=True 时)。"""

    def __init__(self, *a, inject=False, **k):
        super().__init__(*a, **k)
        self.inject = inject
        self.tl_cursor = 0
        self.tl_injections = 0
        self.tl_tokens = 0

    async def _mcp_tool(self, name, args):
        result = await super()._mcp_tool(name, args)
        if not self.inject:
            return result
        try:
            tl = await call(self.session, "world_timeline",
                            {"world_id": self.wid, "since": self.tl_cursor}, timeout=30)
            cur = int(tl.get("cursor", self.tl_cursor))
            summary = compact_timeline(tl)
            self.tl_injections += 1
            self.tl_tokens += len(summary)
            self.tl_cursor = cur
            if summary:
                result = f"{result}\n\n[环境时间线摘要] {summary}"
        except Exception as e:
            result = f"{result}\n\n[环境时间线摘要] (获取失败: {str(e)[:60]})"
        return result


async def run_round(session, task, arm, rnd, rounds):
    tag = f"{task['name']}-{arm}-{rnd}"
    print(f"\n▶ [{rnd}/{rounds}] {arm} {task['name']} ({task['note']})", flush=True)
    opened = await call(session, "world_open", {
        "url": task["url"], "ready_policy": "action", "wait_ms": 1500,
        "reuse_policy": "never", "task_id": tag}, timeout=90)
    wid = opened["world_id"]
    retried = False
    try:
        for attempt in (1, 2):
            agent = InjectAgent(session, PROMPT, list(BASE_TOOLS),
                                max_steps=MAX_STEPS, verbose=False,
                                inject=(arm == "B"))
            agent.attach(wid, None)
            t0 = time.perf_counter()
            res = await agent.run()
            elapsed_s = round(time.perf_counter() - t0, 1)
            page_state = await eval_page(session, wid, task["verify_expr"])
            if task["name"] == "t2-silent-500":
                ok, reason = judge_final(agent, page_state)
            elif task["name"] == "t3-price-change":
                ok, reason = judge_price(agent, page_state)
            else:
                ok = task["ok_if"](page_state)
                reason = f"页面: {str(page_state)[:70]}"
            if attempt == 1 and not ok and len(agent.calls) < 2:
                retried = True
                print(f"    ↻ 工具调用过少({len(agent.calls)}),重跑", flush=True)
                continue
            break
        print(f"    → {'PASS' if ok else 'FAIL'} steps={res.get('steps')} {elapsed_s}s "
              f"tokens={agent.tokens_in + agent.tokens_out} "
              f"注入={agent.tl_injections}次({agent.tl_tokens}字符) | {reason}", flush=True)
        return {
            "task": task["name"], "arm": arm, "round": rnd, "ok": ok, "reason": reason,
            "status": res.get("status"), "steps": res.get("steps"),
            "elapsed_s": elapsed_s, "tokens_in": agent.tokens_in, "tokens_out": agent.tokens_out,
            "final": (agent.final_text or "")[:120], "retried": retried,
            "tl_injections": agent.tl_injections, "tl_tokens": agent.tl_tokens,
            "trace": [{"step": c["step"], "name": c["name"],
                       "args": json.dumps(c["args"], ensure_ascii=False)[:80]} for c in agent.calls],
        }
    finally:
        try:
            await call(session, "world_close", {"world_id": wid}, timeout=15)
        except Exception:
            pass


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--task", default="t1,t3")
    args = ap.parse_args()

    server, base = ab_fixtures.serve()
    wanted = {t.strip() for t in args.task.split(",") if t.strip()}
    tasks = {k: v for k, v in TASKS.items() if k in wanted or v["name"] in wanted}
    for t in tasks.values():
        t["url"] = base + t["url"]

    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    results = []
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                for tid, task in tasks.items():
                    for arm in ("A", "B"):
                        for rnd in range(1, args.rounds + 1):
                            try:
                                results.append(await run_round(session, task, arm, rnd, args.rounds))
                            except Exception as e:
                                print(f"    ✗ 轮次异常: {type(e).__name__}: {str(e)[:120]}", flush=True)
    finally:
        server.shutdown()

    print("\n" + "=" * 78)
    print(f"{'任务':<16}{'臂':<4}{'成功率':<10}{'平均步数':<10}{'平均耗时':<10}{'tokens':<10}{'注入次数'}")
    summary = {}
    for tid, task in tasks.items():
        for arm in ("A", "B"):
            rs = [r for r in results if r["task"] == task["name"] and r["arm"] == arm]
            if not rs:
                continue
            ok_n = sum(1 for r in rs if r["ok"])
            summary[f"{task['name']}-{arm}"] = {
                "ok": f"{ok_n}/{len(rs)}",
                "steps_avg": round(sum(r["steps"] for r in rs) / len(rs), 1),
                "elapsed_avg": round(sum(r["elapsed_s"] for r in rs) / len(rs), 1),
                "tokens_avg": round(sum(r["tokens_in"] + r["tokens_out"] for r in rs) / len(rs)),
                "injections_avg": round(sum(r.get("tl_injections", 0) for r in rs) / len(rs), 1),
            }
            s = summary[f"{task['name']}-{arm}"]
            print(f"{task['name']:<16}{arm:<4}{s['ok']:<10}{s['steps_avg']:<10}"
                  f"{s['elapsed_avg']:<10}{s['tokens_avg']:<10}{s['injections_avg']:<10}")
    print("=" * 78)
    (ARTIFACTS / "timeline_inject_report.json").write_text(
        json.dumps({"fixture": "ab_fixtures.py", "mode": "harness-inject", "summary": summary,
                    "runs": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("报告:", ARTIFACTS / "timeline_inject_report.json")


if __name__ == "__main__":
    asyncio.run(main())
