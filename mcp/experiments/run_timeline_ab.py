# -*- coding: utf-8 -*-
"""A/B 评估:world_timeline 能力包对真实任务决策的收益。

设计:
  - 臂 A(基线):普通 MCP 工具集(world_open/entities/click/fill/close)——现状
  - 臂 B(时间线):A + world_timeline + world_assume/world_ack + 前提失效通知注入
    两臂同任务同 LLM 同上限,仅"时间线能力包"不同
  - 3 任务 × 2 臂 × 3 轮 = 18 次 agent 循环
    任务场景由 ab_fixtures.py 本地 HTTP 提供(慢接口/静默失败/实时变价)
  - 成功判定由脚本读页面最终状态,不由 agent 自评(FP 一票否决)

输出: artifacts/timeline_ab_report.json + 控制台对比表
运行: python mcp/experiments/run_timeline_ab.py [--rounds 3] [--task t1]
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
from premise_monitor import PremiseMonitor

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
MAX_STEPS = 15

BASE_TOOLS = [
    {"type": "function", "function": {
        "name": "world_entities", "description": "列出页面上的元素。参数: world_id, 可选 text(文本过滤), role(元素角色,如 button/input/link), max_results。",
        "parameters": {"type": "object", "properties": {
            "world_id": {"type": "integer"}, "text": {"type": "string"},
            "role": {"type": "string"}, "max_results": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "world_click", "description": "点击页面元素。参数: world_id, id(元素ID)。返回点击结果。",
        "parameters": {"type": "object", "properties": {
            "world_id": {"type": "integer"}, "id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "world_fill", "description": "向输入框填入文本。参数: world_id, id(元素ID), text。",
        "parameters": {"type": "object", "properties": {
            "world_id": {"type": "integer"}, "id": {"type": "string"}, "text": {"type": "string"}}}}},
]

ARM_B_TOOLS = [
    {"type": "function", "function": {
        "name": "world_timeline", "description": "查询环境时间线(动作/网络请求/响应/DOM 变化的因果记录)。"
        "参数: world_id, since(0 或上次返回的 cursor), mode(digest 摘要 / raw 明细)。"
        "摘要含: 各类型计数、响应状态码分布、接口命中、DOM 变化、前提失效、请求失败、静默失败标注(有 4xx/失败但页面无变化)、因果窗口(每个动作引发的后果)。"
        "适合: 动作后判断是否真的生效、是否有接口报错、页面是否有变化。",
        "parameters": {"type": "object", "properties": {
            "world_id": {"type": "integer"}, "since": {"type": "integer"},
            "mode": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "world_assume", "description": "注册一个前提监视:声明你依赖的页面状态(如某输入框的值=某个数字),"
        "状态偏离时你会收到环境通知。参数: world_id, name, doc_id(DOM id 或内核 id), attr(value/checked/文本), expect(期望值)。",
        "parameters": {"type": "object", "properties": {
            "world_id": {"type": "integer"}, "name": {"type": "string"},
            "doc_id": {"type": "string"}, "attr": {"type": "string"},
            "expect": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "world_ack", "description": "前提修正后恢复监视。参数: world_id, name(前提名), expect(修正后的期望值)。",
        "parameters": {"type": "object", "properties": {
            "world_id": {"type": "integer"}, "name": {"type": "string"},
            "expect": {"type": "string"}}}}},
]

# 任务定义: prompt 是给 agent 的目标;verify 读页面最终状态返回 (ok, reason)
TASKS = {
    "t1": {
        "name": "t1-spa-slow",
        "url": "/ab/orders.html",
        "prompt": "打开订单查询页,点击「查询订单」按钮,等待查询结果出现后,报告第一笔订单的金额(元)。"
                  "注意:查询可能较慢,结果不会立即出现;若页面尚无结果,请等待重试而非放弃。",
        "verify_expr": "() => { const r = document.getElementById('result'); "
                       "const o = r ? r.querySelector('.order') : null; "
                       "return o ? o.textContent : document.getElementById('status').textContent; }",
        "ok_if": lambda text: "328" in str(text),
        "note": "SPA 慢加载(接口 2.5s)",
    },
    "t2": {
        "name": "t2-silent-500",
        "url": "/ab/submit.html",
        "prompt": "打开提交订单页,点击「提交订单」按钮,然后判断订单是否提交成功。"
                  "只在你确定成功时报告成功;若不确定或发现失败迹象,如实报告未成功。",
        "verify_expr": "() => document.getElementById('status').textContent",
        "ok_if": lambda text: True,   # 成功与否由 LLM 结论判定(见 judge_final)
        "note": "静默失败(接口 500 无页面变化)",
    },
    "t3": {
        "name": "t3-price-change",
        "url": "/ab/price.html",
        "prompt": "打开下单页。页面上「当前价格」可能随时变化(实时行情)。"
                  "下单前把「下单价格」输入框的值填成页面显示的当前价格,然后点击「下单」按钮。"
                  "最终订单价格必须与最新当前价格一致。",
        "verify_expr": "() => JSON.stringify({cur: document.getElementById('price').textContent, "
                       "ordered: document.getElementById('order-price').value})",
        "ok_if": lambda text: True,   # 见 judge_price
        "note": "前提失效(价格 1000→1200)",
    },
}

# 成功判定(FP 一票否决:把失败当成功 = 失败)
FAIL_HINTS = ("未成功", "没有成功", "失败", "未能", "不确定", "可能失败", "没有生效", "没成功", "500",
              "无法确认", "没有完成")


def judge_final(agent, page_state):
    final = (agent.final_text or "").lower()
    if any(h in final for h in FAIL_HINTS):
        return True, "结论:未成功(正确识别失败)"
    if "成功" in final and "提交" in final:
        return False, "误报成功(FP 一票否决)"
    return None, f"结论不明确,页面状态: {page_state[:60]}"


def judge_price(agent, page_state):
    try:
        st = json.loads(page_state)
    except Exception:
        return False, f"页面读取异常: {page_state[:80]}"
    cur, ordered = st.get("cur"), st.get("ordered")
    if cur == "1200" and ordered == "1200":
        return True, f"价格 1200 下单 1200(正确用最新价)"
    if cur == "1000" and ordered == "1000":
        return None, "价格尚未变化即下单(信息不足,不算成功)"
    if cur != ordered:
        return False, f"下单价 {ordered} ≠ 当前价 {cur}(用旧价)"
    return None, f"cur={cur} ordered={ordered}"


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    return {}


async def eval_page(session, wid, expr):
    r = await call(session, "world_eval", {"world_id": wid, "expression": expr}, timeout=20)
    raw = r.get("result") if isinstance(r, dict) else r
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def system_prompt(arm, task):
    base = ("你是网页操作 agent。目标页面已经打开,直接使用工具在页面上执行任务:"
            "用 world_entities 查看元素,world_click 点击,world_fill 填表。"
            "逐步执行,完成后用文字报告结果。不要编造页面状态,只依据工具返回。")
    extra = ("\n附加能力:你还可以查询环境时间线(world_timeline,可查看动作引发的请求/响应/DOM 变化、"
             "接口状态码、静默失败标注)和注册前提监视(world_assume,页面状态偏离你依赖的值时会收到环境通知)。"
             ) if arm == "B" else ""
    return base + extra


async def run_round(session, task, arm, rnd, rounds):
    tag = f"{task['name']}-{arm}-{rnd}"
    print(f"\n▶ [{rnd}/{rounds}] {arm} {task['name']} ({task['note']})", flush=True)
    opened = await call(session, "world_open", {
        "url": task["url"], "ready_policy": "action", "wait_ms": 1500,
        "reuse_policy": "never", "task_id": tag}, timeout=90)
    wid = opened["world_id"]
    monitor = None
    retried = False
    try:
        for attempt in (1, 2):
            tools = list(BASE_TOOLS) + (list(ARM_B_TOOLS) if arm == "B" else [])
            if arm == "B":
                monitor = PremiseMonitor(session, wid, interval_s=1.0)
            agent = LlmAgent(session, system_prompt(arm, task), tools,
                             max_steps=MAX_STEPS, verbose=False,
                             notify=(lambda: list(monitor.pending)) if arm == "B" else None)
            agent.attach(wid, monitor)
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
            clicks = [c for c in agent.calls if c["name"] == "world_click"]
            trace = [{"step": c["step"], "name": c["name"],
                      "args": json.dumps(c["args"], ensure_ascii=False)[:80]}
                     for c in agent.calls]
            # 去模型噪声:工具调用 <2 次且判定失败 → 重跑一次(两臂同规则)
            if attempt == 1 and not ok and len(agent.calls) < 2:
                retried = True
                print(f"    ↻ 工具调用过少({len(agent.calls)}),重跑", flush=True)
                if monitor:
                    monitor.stop()
                continue
            break
        print(f"    → {'PASS' if ok else 'FAIL'} steps={res.get('steps')} {elapsed_s}s "
              f"tokens={agent.tokens_in + agent.tokens_out} | {reason}", flush=True)
        return {
            "task": task["name"], "arm": arm, "round": rnd, "ok": ok, "reason": reason,
            "status": res.get("status"), "steps": res.get("steps"),
            "elapsed_s": elapsed_s, "tokens_in": agent.tokens_in, "tokens_out": agent.tokens_out,
            "clicks": len(clicks), "final": (agent.final_text or "")[:120],
            "retried": retried, "trace": trace,
        }
    finally:
        if monitor:
            monitor.stop()
        try:
            await call(session, "world_close", {"world_id": wid}, timeout=15)
        except Exception:
            pass


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--task", default=None)
    args = ap.parse_args()

    server, base = ab_fixtures.serve()
    tasks = {k: v for k, v in TASKS.items() if args.task in (None, k, v["name"])}
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

    # ── 汇总 ──
    print("\n" + "=" * 78)
    print(f"{'任务':<16}{'臂':<4}{'成功率':<10}{'平均步数':<10}{'平均耗时':<10}{'tokens':<10}{'点击次数'}")
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
                "clicks_avg": round(sum(r["clicks"] for r in rs) / len(rs), 1),
            }
            print(f"{task['name']:<16}{arm:<4}{summary[f'{task['name']}-{arm}']['ok']:<10}"
                  f"{summary[f'{task['name']}-{arm}']['steps_avg']:<10}"
                  f"{summary[f'{task['name']}-{arm}']['elapsed_avg']:<10}"
                  f"{summary[f'{task['name']}-{arm}']['tokens_avg']:<10}"
                  f"{summary[f'{task['name']}-{arm}']['clicks_avg']:<10}")
    print("=" * 78)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "timeline_ab_report.json").write_text(
        json.dumps({"fixture": "ab_fixtures.py", "summary": summary, "runs": results},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("报告:", ARTIFACTS / "timeline_ab_report.json")


if __name__ == "__main__":
    asyncio.run(main())
