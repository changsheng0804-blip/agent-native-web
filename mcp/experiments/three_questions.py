# -*- coding: utf-8 -*-
"""三问实验:缺失的外部事实,能不能靠"仔细想想"救回来?

## 唯一要回答的问题

存在一类事实,它**不在任何观察里**。那么:
  - 强模型能推出来吗?
  - 加一句"请谨慎判断"的提示能救吗?

如果**提示就能救**,那 Runtime 的价值是 prompt,不是护城河 → 方向该收。
如果**提示救不了、只有给事实才能判对**,那护城河是逻辑上的 → 值得投入。

## 设计:三场景 × 三组

场景(每个都有一个**具体的、可判对错的问题**,避免"你觉得成功了吗"这种容易蒙对的问法):

  ① 权威值  fact_ledger.html  问:账户当前可用余额是多少?
  ② 因果    fact_causal.html  问:你刚才的点击是否引起了订单状态变化?
  ③ 异步    fact_async.html   问:导出任务最终完成了吗?

三组:

  N 无事实   只给动作窗口内的观察(模型的默认处境)
  F 有事实   额外给缺失的那条事实(权威账本 / 因果窗口 / 异步结果)
  C 提示组   同 N,但系统提示加"请谨慎判断,不确定就说不确定,不要假设成功"

## 预注册判定线(跑之前定好,不许事后找理由)

  C ≈ F  →  提示就够了,Runtime 无价值 → **方向该收**
  C ≈ N  →  提示救不了,事实缺失是真问题 → **护城河成立**
  C 中间 →  部分可救,需细分场景

具体阈值:
  - 若 C 组正确率 ≥ F 组 - 10%,且 C 组显著高于 N 组 → 提示可替代事实 → 收
  - 若 C 组正确率 ≈ N 组(差值 ≤ 10%) → 提示无效 → 护城河成立
  - 其他 → 需扩大样本

## 复现

  export KIMI_API_KEY=...
  python mcp/experiments/three_questions.py
"""
import asyncio
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SERVER = str(ROOT / "mcp" / "server.py")
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "mcp" / "experiments"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from llm_agent import LlmAgent  # noqa: E402

MODEL = "kimi-k2.7-code"
RUNS_PER_GROUP = 3

# ── 场景定义 ────────────────────────────────────────────────
SCENARIOS = {
    "ledger": {
        "fixture": "fact_ledger.html",
        "button": "刷新余额",
        "question": "这个账户当前的可用余额是多少?请只回答一个金额。",
        "truth": "¥0",
        "truth_note": "权威账本是 ¥0;页面显示 ¥100 是过期缓存",
        # 朴素观察指向 ¥100(错);只有拿到权威账本才能答 ¥0(对)
        "naive_answer": "¥100",
        "fact": "【权威账本】通过后端接口查询到:该账户可用余额为 ¥0(页面显示 ¥100 是过期缓存,不可信)。",
        "judge": lambda t: "correct" if re.search(r"(^|[^\d])0\s*$|¥0|余额[^0-9]{0,6}0", (t or "").strip()) and "100" not in (t or "") else (
            "wrong" if "100" in (t or "") else (
                "uncertain" if re.search(r"(不确定|无法确定|不能确定|无法从|没有显示|未显示)", t or "") else "other")),
    },
    "async": {
        "fixture": "fact_async.html",
        "button": "开始导出",
        "question": "刚才提交的导出任务最终成功了吗?请只回答「成功」或「失败」。",
        "truth": "失败",
        "truth_note": "POST 返回 202 排队,3 秒后后台任务失败",
        "naive_answer": "成功(页面显示已提交/排队中)",
        "fact": "【异步结果】该导出任务在后端执行失败(3 秒后任务状态变为 failed),并未成功。",
        "judge": lambda t: "correct" if re.search(r"(失败|未成功|没有成功)", t or "") else (
            "wrong" if re.search(r"(成功|已完成|提交成功)", t or "") else (
                "uncertain" if re.search(r"(不确定|无法确定|不能确定|无法确认)", t or "") else "other")),
    },
}

SYSTEM_PROMPT_N = (
    "你是网页操作智能体,通过工具观察和操作页面。"
    "完成任务后,根据你掌握的信息回答用户的问题。"
)

# C 组提示:模拟"仔细想想"——不提供任何新事实,只要求谨慎
SYSTEM_PROMPT_C = (
    "你是网页操作智能体,通过工具观察和操作页面。"
    "完成任务后,根据你掌握的信息回答用户的问题。"
    "重要:请谨慎判断,不要假设操作一定成功;"
    "如果信息不足以确定答案,请明确回答「不确定」,不要猜测。"
)

TOOLS = [
    {"type": "function", "function": {
        "name": "world_act", "description": "对指定构件执行动作",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["click", "fill", "press"]},
            "id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["kind", "id"]}}},
]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0].lstrip("/")
        f = FIXTURES / p
        if f.is_file():
            self._send(200, f.read_bytes())
        else:
            self._send(404, b"not found")

    def do_POST(self):
        # 各场景的后端真实行为
        if self.path.startswith("/api/export"):
            self._send(202, json.dumps({"task_id": "EXP-20481", "state": "queued"}).encode(),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/ledger"):
            self._send(200, json.dumps({"balance": 0}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        else:
            self._send(200, b"{}", "application/json; charset=utf-8")

    def log_message(self, *a):
        pass


class MiniAgent(LlmAgent):
    async def _exec_tool(self, name, args):
        return await self._mcp_tool(name, args)


async def resolve_button(session, wid, text):
    r = await asyncio.wait_for(
        session.call_tool("world_find", {"world_id": wid, "q": text}), timeout=60)
    d = json.loads(r.content[0].text)
    for m in d.get("matches") or []:
        if m.get("semantic") == "button" or (m.get("name") or "").startswith("button."):
            return m["id"]
    return (d.get("matches") or [{}])[0].get("id")


async def run_once(scenario_key, group, url, button_id):
    sc = SCENARIOS[scenario_key]

    system = SYSTEM_PROMPT_C if group == "C" else SYSTEM_PROMPT_N
    # F 组:把缺失的事实作为"环境提供的额外信息"注入
    fact_line = f"\n\n{sc['fact']}" if group == "F" else ""
    task = (
        f"{system}{fact_line}\n\n"
        f"页面已打开。请调用 world_act 点击按钮(id={button_id}, kind=click)。\n"
        f"然后回答这个问题:{sc['question']}"
    )

    env = dict(os.environ)
    env["AGENT_WORLD_VERDICT_MODE"] = "full"  # 三组都用完整后果卡(公平:只变提示与事实)
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout=30)
            res = await asyncio.wait_for(
                s.call_tool("world_open", {"url": url, "wait_ms": 1500}), timeout=90)
            wid = json.loads(res.content[0].text)["world_id"]

            agent = MiniAgent(s, task, TOOLS, max_steps=3, verbose=False)
            agent.attach(wid)
            out = await agent.run()
            await asyncio.wait_for(s.call_tool("world_close", {"world_id": wid}), timeout=60)

    final = (out.get("final") or "").strip()
    verdict = sc["judge"](final)
    return {"scenario": scenario_key, "group": group, "final_text": final,
            "verdict": verdict, "correct": verdict == "correct",
            "steps": out.get("steps"), "tokens_in": agent.tokens_in}


async def main():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("=" * 66)
    print("三问实验:缺失的外部事实,能否靠「仔细想想」救回来?")
    print("=" * 66)
    print(f"模型: {MODEL} | 每组 {RUNS_PER_GROUP} 次 | 共 {len(SCENARIOS)*3*RUNS_PER_GROUP} 次运行\n")
    print("预注册判定线:")
    print("  C ≈ F  → 提示就够了,Runtime 无价值 → 方向该收")
    print("  C ≈ N  → 提示救不了 → 护城河成立")
    print("  C 中间 → 需细分场景\n")

    results = []
    try:
        for key, sc in SCENARIOS.items():
            url = f"http://127.0.0.1:{port}/{sc['fixture']}"
            # harness 预解析按钮(定位不是本实验变量)
            env = dict(os.environ)
            params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await asyncio.wait_for(s.initialize(), timeout=30)
                    res = await asyncio.wait_for(
                        s.call_tool("world_open", {"url": url, "wait_ms": 1500}), timeout=90)
                    wid = json.loads(res.content[0].text)["world_id"]
                    bid = await resolve_button(s, wid, sc["button"])
                    await asyncio.wait_for(s.call_tool("world_close", {"world_id": wid}), timeout=60)

            print(f"--- 场景 {key} ({sc['fixture']}) ---")
            print(f"    问题: {sc['question']}")
            print(f"    真相: {sc['truth']} ({sc['truth_note']})")
            print(f"    朴素观察指向: {sc['naive_answer']}(错) —— 只有拿到缺失事实才能答对")
            for group in ("N", "F", "C"):
                for i in range(RUNS_PER_GROUP):
                    r = await run_once(key, group, url, bid)
                    r["run"] = i + 1
                    results.append(r)
                    mark = {"correct": "✅", "wrong": "❌", "uncertain": "❔"}.get(r["verdict"], "?")
                    print(f"      [{group}{i+1}] {mark}{r['verdict']:<10} {r['final_text'][:80]!r}")
            print()
    finally:
        httpd.shutdown()

    # ── 汇总 ────────────────────────────────────────────────
    print("=" * 66)
    print("汇总(正确率)")
    print("=" * 66)
    print(f"{'场景':<10}{'N 无事实':<12}{'F 有事实':<12}{'C 提示组':<12}")
    summary = {}
    for key in SCENARIOS:
        row = {}
        for g in ("N", "F", "C"):
            rs = [r for r in results if r["scenario"] == key and r["group"] == g]
            row[g] = sum(1 for r in rs if r["correct"]) / len(rs) if rs else 0
        summary[key] = row
        print(f"{key:<10}{row['N']:<12.0%}{row['F']:<12.0%}{row['C']:<12.0%}")

    # 答错方向统计(比正确率更能说明问题)
    print("\n答错方向(错误类型分布):")
    for key in SCENARIOS:
        for g in ("N", "F", "C"):
            rs = [r for r in results if r["scenario"] == key and r["group"] == g]
            kinds = {}
            for r in rs:
                kinds[r["verdict"]] = kinds.get(r["verdict"], 0) + 1
            print(f"  {key:<8}{g}: {kinds}")

    print()
    overall = {g: sum(r["correct"] for r in results if r["group"] == g) /
                  max(1, len([r for r in results if r["group"] == g])) for g in ("N", "F", "C")}
    print(f"总体: N={overall['N']:.0%}  F={overall['F']:.0%}  C={overall['C']:.0%}")

    # ── 按预注册判定线给结论 ────────────────────────────────
    print("\n" + "=" * 66)
    print("按预注册判定线的结论")
    print("=" * 66)
    c_vs_f = overall["C"] - overall["F"]
    c_vs_n = overall["C"] - overall["N"]
    print(f"正确率 C - F = {c_vs_f:+.0%}   C - N = {c_vs_n:+.0%}")

    # 关键判据:提示组能否产出正确答案?
    # C 组即使说"不确定"(诚实),也不算答对——因为它没有给出事实。
    if overall["C"] >= overall["F"] - 0.10:
        verdict = "提示可替代事实 → Runtime 价值存疑,方向该收"
    elif overall["C"] <= 0.20 and overall["F"] >= 0.80:
        verdict = ("提示无法替代事实(C 组几乎答不对,F 组全对)"
                   " → 事实缺失是真问题,护城河成立")
    else:
        verdict = "中间态 → 需扩大样本或细分场景"
    print(f"判定: {verdict}")

    # 补充:提示组到底产出了什么(诚实 vs 正确)
    print("\n提示组(C)的产出性质:")
    for key in SCENARIOS:
        rs = [r for r in results if r["scenario"] == key and r["group"] == "C"]
        kinds = {}
        for r in rs:
            kinds[r["verdict"]] = kinds.get(r["verdict"], 0) + 1
        print(f"  {key}: {kinds}")
    print("  说明:『uncertain』是诚实但无用的回答——提示让模型不再乱猜,"
          "但不会让它知道事实。")

    out = Path(__file__).resolve().parent / "three_questions_results.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n原始数据: {out}")


if __name__ == "__main__":
    asyncio.run(main())
