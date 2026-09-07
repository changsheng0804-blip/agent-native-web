# -*- coding: utf-8 -*-
"""正式实验:前提失效监视 vs 现状(LLM agent 驱动, fixture 精确 A/B)。

场景:旅行订单表单(7 步任务)。
中途注入两处前提失效(与 agent 进度挂钩,两臂公平):
  注入1: 出发地与目的地都已填写后 → 重置出发地(受控组件)
  注入2: 保险已勾选后 → 取消勾选(脚本反选)
核心指标: 提交一次通过率 / 提交前主动修正次数 / 步骤与 token 成本 / 失效暴露延迟。

运行: python mcp/experiments/run_premise_experiment.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from premise_monitor import PremiseMonitor, read_actual
from llm_agent import LlmAgent

SERVER = Path(__file__).resolve().parent.parent / "server.py"
FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "order_form.html"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RUNS = 3

TASK_TEXT = (
    "你在一个旅行订单表单页面。需要完成以下步骤:\n"
    "1. 出发地 = 北京\n"
    "2. 目的地 = 上海\n"
    "3. 日期 = 2026-10-01\n"
    "4. 人数 = 2\n"
    "5. 勾选“购买保险”复选框(点一次即可,不要重复点击)\n"
    "6. 点击“提交订单”按钮,确认出现“订单提交成功”和订单回执\n"
    "7. 最后报告:回执中显示的出发地、目的地、日期、人数、保险状态\n\n"
    "注意:页面脚本可能在任务中途重置或修改已填内容,而且不会触发任何报错提示——\n"
    "提交按钮也不会校验必填项。如果已填的内容被修改,回执会显示错误的值。\n"
    "提交前请确认所有字段的正确性。全部完成后点击提交,确认回执内容完全正确后再结束。\n"
    "完成时直接回复“任务完成”并报告回执内容。最多 25 次工具调用。"
)

B_EXTRA = (
    "\n\n额外能力(必须使用):\n"
    "- 每完成一个关键字段的填写/勾选,立即调用 premise_assume 声明你的假设\n"
    "  (例如 name=origin, doc_id=field-origin, attr=value, expect=北京)\n"
    "- 如果收到“环境通知”说明某假设失效,立即重新填写/勾选该字段,然后调用 premise_ack 恢复\n"
    "- 可用 premise_status 查看你的假设列表"
)

TOOLS_BASE = [
    {"type": "function", "function": {"name": "world_entities",
     "description": "列出页面构件(输入框/按钮/选项等),可按 role 和 text 过滤。先用它了解页面结构。",
     "parameters": {"type": "object", "properties": {
         "role": {"type": "string", "description": "如 input/button/checkbox/link"},
         "text": {"type": "string", "description": "匹配文本,如 北京/提交/保险"},
         "max_results": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "world_click",
     "description": "点击指定构件(传 world_entities 返回的 id)",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
    {"type": "function", "function": {"name": "world_fill",
     "description": "向输入框填写文本(传 world_entities 返回的 id)",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "string"}, "text": {"type": "string"}}, "required": ["id", "text"]}}},
    {"type": "function", "function": {"name": "world_state",
     "description": "查看页面当前状态(URL/弹窗/输入框值)",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
]

TOOLS_B = TOOLS_BASE + [
    {"type": "function", "function": {"name": "premise_assume",
     "description": "声明决策前提:我假设某元素的状态是某个值,请持续监视,失效时通知我",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "前提名,如 origin"},
         "doc_id": {"type": "string", "description": "元素 DOM id,如 field-origin"},
         "attr": {"type": "string", "description": "监视的属性:value 或 checked", "enum": ["value", "checked"]},
         "expect": {"type": "string", "description": "期望值,如 北京 / true"}},
         "required": ["name", "doc_id", "attr", "expect"]}}},
    {"type": "function", "function": {"name": "premise_ack",
     "description": "修正完成后恢复对该前提的监视",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "premise_status",
     "description": "查看当前所有前提及其状态",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
]

INJECT_JS = {
    "origin": "() => { document.getElementById('field-origin').value = ''; return true; }",
    "insurance": "() => { const c = document.getElementById('field-insurance'); c.checked = false; return true; }",
    "price": "() => { const p = document.getElementById('price-total'); p.textContent = '当前价格: ¥1200'; return true; }",
}


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except Exception:
                return {"_raw_text": item.text[:200]}
    return {}


async def open_world(session, tag):
    return await call(session, "world_open", {
        "url": FIXTURE.as_uri(), "ready_policy": "action", "wait_ms": 2000,
        "reuse_policy": "never", "task_id": f"premise-{tag}",
    }, timeout=90)


def read_expr(doc_id, attr):
    if attr == "checked":
        return f"() => (document.getElementById('{doc_id}') || {{}}).checked === true ? 'true' : 'false'"
    return f"() => (document.getElementById('{doc_id}') || {{}}).value || ''"


async def read_field(session, wid, doc_id, attr="value"):
    return str(await read_actual(session, wid, read_expr(doc_id, attr))).strip()


async def injection_controller(session, wid, done_ref, injected, log):
    """与 agent 进度挂钩的条件注入(两臂公平):
    注入1: 出发地与目的地都已填写后 → 重置出发地
    注入2: 保险已勾选后 → 取消勾选
    注入3: 注入2 之后 → 价格从 ¥800 改为 ¥1200(不触发任何校验/报错)
    同时实时监测 origin/insurance 的恢复时刻(值回到正确即记录,修复 recovery 指标)。
    """
    while not done_ref["done"]:
        try:
            origin = await read_field(session, wid, "field-origin")
            dest = await read_field(session, wid, "field-dest")
            ins = await read_field(session, wid, "field-insurance", "checked")
            if origin and dest and not injected.get("origin"):
                injected["origin"] = True
                injected["origin_at"] = int(time.time() * 1000)
                await call(session, "world_eval", {"world_id": wid, "expression": INJECT_JS["origin"]}, timeout=15)
                log.append(f"[注入] 重置出发地(当时 origin={origin!r} dest={dest!r})")
            if ins == "true" and not injected.get("insurance"):
                injected["insurance"] = True
                injected["insurance_at"] = int(time.time() * 1000)
                await call(session, "world_eval", {"world_id": wid, "expression": INJECT_JS["insurance"]}, timeout=15)
                log.append(f"[注入] 取消保险勾选")
            if injected.get("insurance") and not injected.get("price"):
                injected["price"] = True
                injected["price_at"] = int(time.time() * 1000)
                await call(session, "world_eval", {"world_id": wid, "expression": INJECT_JS["price"]}, timeout=15)
                log.append(f"[注入] 价格改为 ¥1200")
            # 实时恢复监测(注入后值回到正确 = agent 修正完成)
            if injected.get("origin") and not injected.get("origin_recovered_at"):
                if origin == "北京":
                    injected["origin_recovered_at"] = int(time.time() * 1000)
            if injected.get("insurance") and not injected.get("insurance_recovered_at"):
                if ins == "true":
                    injected["insurance_recovered_at"] = int(time.time() * 1000)
        except Exception:
            pass
        await asyncio.sleep(0.4)


async def outcome_watcher(session, wid, done_ref, log):
    """旁观:记录提交按钮点击后的 error/success 变化(任务客观结果)。"""
    last_error = ""
    submit_clicks = 0
    first_success_at = None
    while not done_ref["done"]:
        try:
            err = await read_field(session, wid, "error", "textContent")
            if err and err != last_error:
                last_error = err
                log.append(f"[页面] {err}")
                if "缺少" in err:
                    submit_clicks += 1
            succ = await read_actual(session, wid,
                                     "() => (document.getElementById('success') || {}).style.display || 'none'")
            if str(succ).strip() == "block" and first_success_at is None:
                first_success_at = int(time.time() * 1000)
                log.append("[页面] ✅ 订单提交成功")
        except Exception:
            pass
        await asyncio.sleep(0.4)
    return {"submit_errors": submit_clicks, "first_success_at": first_success_at}


async def run_arm(session, arm):
    tag = f"{arm}-{int(time.time())}"
    opened = await open_world(session, tag)
    wid = opened["world_id"]
    log = []
    monitor = None
    injected = {}
    done_ref = {"done": False}
    t_start = time.time()

    def notify():
        if monitor is None:
            return []
        out = list(monitor.pending)
        monitor.pending.clear()
        for v in out:
            log.append(f"[监视] ⚠ 前提失效: {v['name']} 期望 {v['expected']!r} 实际 {v['actual']!r}")
        return out

    if arm == "B":
        monitor = PremiseMonitor(session, wid, interval_s=1.0)
        inj_task = asyncio.create_task(injection_controller(session, wid, done_ref, injected, log))
        watch_task = asyncio.create_task(outcome_watcher(session, wid, done_ref, log))
    else:
        inj_task = asyncio.create_task(injection_controller(session, wid, done_ref, injected, log))
        watch_task = asyncio.create_task(outcome_watcher(session, wid, done_ref, log))

    system_prompt = TASK_TEXT + (B_EXTRA if arm == "B" else "")
    tools = TOOLS_B if arm == "B" else TOOLS_BASE
    agent = LlmAgent(session, system_prompt, tools, max_steps=25, verbose=True, notify=notify)
    agent.attach(wid, monitor)

    result = await agent.run()
    done_ref["done"] = True
    await asyncio.sleep(1.0)
    # 客观结果:成功提示 + 回执内容(回执反映提交时的真实字段值)
    succ = str(await read_actual(session, wid,
                                "() => (document.getElementById('success') || {}).style.display || 'none'")).strip()
    success = succ == "block"
    receipt = str(await read_actual(session, wid,
                                    "() => (document.getElementById('receipt') || {}).textContent || ''")).strip()
    # 回执正确性:出发地=北京 且 保险=已购(注入的两处失效若未被修正,回执必然错误)
    receipt_ok = ("出发地=北京" in receipt and "保险=已购" in receipt)
    if success:
        log.append(f"[结果] ✅ 提交成功,回执: {receipt}")
    else:
        log.append("[结果] ❌ 任务失败:未出现成功提示")
    log.append(f"[结果] 回执内容{'正确' if receipt_ok else '错误(字段被重置/取消但未修正)'}")
    # 实时 recovery 数据(注入控制器已记录恢复时刻)
    recovery = {}
    for key, at_key, rec_key in [("origin", "origin_at", "origin_recovered_at"),
                                 ("insurance", "insurance_at", "insurance_recovered_at")]:
        if injected.get(key) and injected.get(rec_key):
            recovery[f"{key}_ms"] = injected[rec_key] - injected[at_key]
    if injected.get("price"):
        recovery["price_exposed_ms"] = None  # 暴露延迟由 B 臂监视器通知时刻给出

    inj_task.cancel()
    watch_task.cancel()
    if monitor:
        monitor.stop()
    await call(session, "world_close", {"world_id": wid}, timeout=15)

    watch = await asyncio.gather(watch_task, return_exceptions=True)
    watch_info = watch[0] if isinstance(watch[0], dict) else {}

    return {
        "arm": arm,
        "success": success,
        "receipt_ok": receipt_ok,
        "receipt": receipt,
        "agent_status": result.get("status"),
        "steps": agent.steps,
        "tokens_in": agent.tokens_in,
        "tokens_out": agent.tokens_out,
        "calls": len(agent.calls),
        "elapsed_s": round(time.time() - t_start, 1),
        "injected": injected,
        "recovery_ms": recovery,
        "monitor": monitor.stats() if monitor else None,
        "log": log,
    }


async def main():
    only_arm = sys.argv[1] if len(sys.argv) > 1 else None
    runs_arg = int(sys.argv[2]) if len(sys.argv) > 2 else RUNS
    ARTIFACTS.mkdir(exist_ok=True)
    results = []
    for arm, runs in [("A", runs_arg), ("B", runs_arg)]:
        if only_arm and arm != only_arm:
            continue
        for run in range(1, runs + 1):
            print(f"\n===== {arm}臂 run{run} =====", flush=True)
            params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), timeout=30)
                        res = await run_arm(session, arm)
            except Exception as e:
                res = {"arm": arm, "success": False, "error": f"{type(e).__name__}: {str(e)[:150]}"}
            res["run"] = run
            results.append(res)
            print(f"结果: success={res.get('success')} receipt_ok={res.get('receipt_ok')} "
                  f"steps={res.get('steps')} tokens={res.get('tokens_in', 0) + res.get('tokens_out', 0)}", flush=True)
            for line in res.get("log", []):
                print("  " + line, flush=True)
            if res.get("monitor"):
                print(f"  监视器: {res['monitor']}", flush=True)
            if res.get("recovery_ms"):
                print(f"  失效→修正延迟: {res['recovery_ms']}", flush=True)

    report = {"runs": RUNS, "results": results, "summary": summarize(results)}
    out = ARTIFACTS / "premise_experiment_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(report["summary"])
    print(f"\n报告: {out}")


def summarize(results):
    from collections import defaultdict
    agg = defaultdict(list)
    for r in results:
        if "error" in r:
            continue
        agg[r["arm"]].append(r)
    out = {}
    for arm, rs in agg.items():
        n = len(rs)
        out[arm] = {
            "n": n,
            "success": sum(1 for r in rs if r["success"]),
            "receipt_ok": sum(1 for r in rs if r.get("receipt_ok")),
            "task_ok": sum(1 for r in rs if r["success"] and r.get("receipt_ok")),
            "steps_avg": round(sum(r["steps"] for r in rs) / n, 1) if n else 0,
            "tokens_avg": round(sum(r["tokens_in"] + r["tokens_out"] for r in rs) / n) if n else 0,
            "recovery_origin_avg_ms": round(sum((r.get("recovery_ms") or {}).get("origin_ms", 0) for r in rs) / n) if n else None,
            "recovery_insurance_avg_ms": round(sum((r.get("recovery_ms") or {}).get("insurance_ms", 0) for r in rs) / n) if n else None,
            "monitor_violations": [r.get("monitor", {}).get("violations") for r in rs] if arm == "B" else [],
            "monitor_polls": [r.get("monitor", {}).get("polls") for r in rs] if arm == "B" else [],
        }
    return out


def print_summary(summary):
    print("\n================ 前提失效实验汇总(不可见信息场景) ================")
    print(f"{'指标':<30}{'A 无监视':<14}{'B 有监视':<14}")
    for key, label in [("n", "运行次数"), ("success", "提交成功"), ("receipt_ok", "回执内容正确"),
                       ("task_ok", "任务成功(提交+回执正确)"), ("steps_avg", "工具调用步数(均)"),
                       ("tokens_avg", "token 消耗(均)"),
                       ("recovery_origin_avg_ms", "出发地失效→修正(ms)"),
                       ("recovery_insurance_avg_ms", "保险失效→修正(ms)")]:
        a = summary.get("A", {}).get(key, "-")
        b = summary.get("B", {}).get(key, "-")
        print(f"{label:<30}{str(a):<14}{str(b):<14}")
    if "B" in summary:
        print(f"B 监视器失效通知: {summary['B'].get('monitor_violations')}")
        print(f"B 监视器轮询次数: {summary['B'].get('monitor_polls')}")


if __name__ == "__main__":
    asyncio.run(main())
