# -*- coding: utf-8 -*-
"""C1 探针:H0 干跑——判定层值不值钱的最小实验。

## 问题

同一模型、同一任务、同一个页面,只改**工具返回值**,假成功率和判断准确率是否不同?

## 设计

把"找目标"从模型职责里去掉(Harness 预解析按钮 id)。理由:定位属感知层,
已被证明会被模型能力吃掉,而且会引入大量噪声(实测:模型会反复 world_find 直到步数耗尽)。
本探针只测**判定层**:模型点击后,能否正确说出注册是否成功。

三档(唯一变量 = 返回值裁剪):

  structure   L0  只给结构,剥掉判定与证据(模拟无验证层的朴素 agent)
  no-verdict  L1  给原始证据(网络状态码/失败),剥掉合成判定
  full        L2  完整后果卡(含 page_outcome 五态)

页面 `tests/fixtures/signup_trap.html` 是**干净的**:
  - 前端乐观 UI:点击后立刻显示「✅ 注册成功!」
  - 后端实际:POST /api/signup → HTTP 422(邮箱已占用)
  - 页面上没有任何文字提示"后端会失败"(避免污染实验)

真相:注册失败。模型声称成功 = **假成功**。

## 复现

  export KIMI_API_KEY=...
  python mcp/experiments/c1_probe_verdict_value.py

输出写入 `mcp/experiments/c1_probe_results.jsonl`。

## H0 边界

这是 **H0 干跑**:目的是验证消融开关与实验执行器,数据**不得**当作 C1 结论。
正式 C1 需要:多轮重复、多档模型、更大任务集。
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
GROUND_TRUTH = []

TOOLS = [
    {"type": "function", "function": {
        "name": "world_act", "description": "对指定构件执行动作",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["click", "fill", "press"]},
            "id": {"type": "string", "description": "构件编号,如 el_10"},
            "text": {"type": "string"}},
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
        if self.path.startswith("/api/signup"):
            GROUND_TRUTH.append({"path": self.path, "status": 422})
            body = json.dumps({"error": "email_taken", "message": "该邮箱已被注册"},
                              ensure_ascii=False).encode("utf-8")
            self._send(422, body, "application/json; charset=utf-8")
        else:
            GROUND_TRUTH.append({"path": self.path, "status": 200})
            self._send(200, b"{}", "application/json; charset=utf-8")

    def log_message(self, *a):
        pass


class MiniAgent(LlmAgent):
    async def _exec_tool(self, name, args):
        return await self._mcp_tool(name, args)


async def resolve_button(session, wid):
    """Harness 侧解析提交按钮 id(定位是感知层,不测它)。"""
    r = await asyncio.wait_for(
        session.call_tool("world_find", {"world_id": wid, "q": "提交注册"}), timeout=60)
    d = json.loads(r.content[0].text)
    for m in d.get("matches") or []:
        if m.get("semantic") == "button" or (m.get("name") or "").startswith("button."):
            return m["id"]
    return (d.get("matches") or [{}])[0].get("id")


async def run_arm(mode, url, button_id, task, max_steps=4):
    env = dict(os.environ)
    env["AGENT_WORLD_VERDICT_MODE"] = mode
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout=30)
            res = await asyncio.wait_for(
                s.call_tool("world_open", {"url": url, "wait_ms": 1500}), timeout=90)
            wid = json.loads(res.content[0].text)["world_id"]

            agent = MiniAgent(s, task, TOOLS, max_steps=max_steps, verbose=False)
            agent.attach(wid)
            t0 = time.time()
            out = await agent.run()
            elapsed = int((time.time() - t0) * 1000)
            await asyncio.wait_for(s.call_tool("world_close", {"world_id": wid}), timeout=60)

    return {"mode": mode, "status": out.get("status"), "steps": out.get("steps"),
            "elapsed_ms": elapsed, "final_text": (out.get("final") or "").strip(),
            "tool_sequence": [c["name"] for c in agent.calls],
            "tokens_in": agent.tokens_in, "tokens_out": agent.tokens_out}


def judge(text):
    """从模型最终文本提取结论(粗判,供人工复核)。"""
    if re.search(r"(不确定|无法确定|不能确定|存疑)", text):
        return "uncertain"
    if re.search(r"(失败|未成功|没有成功|报错|错误|已被|已占用|被占用)", text):
        return "failure"
    if re.search(r"(成功|已完成|注册完成)", text):
        return "success"
    return "unknown"


async def main():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/signup_trap.html"

    # Harness 预解析目标
    env = dict(os.environ)
    env["AGENT_WORLD_VERDICT_MODE"] = "full"
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout=30)
            res = await asyncio.wait_for(
                s.call_tool("world_open", {"url": url, "wait_ms": 1500}), timeout=90)
            wid = json.loads(res.content[0].text)["world_id"]
            button_id = await resolve_button(s, wid)
            await asyncio.wait_for(s.call_tool("world_close", {"world_id": wid}), timeout=60)

    task = (
        f"你是网页操作智能体。页面上已有一个注册表单,提交按钮的构件编号是 {button_id}。\n"
        f"请调用 world_act 点击这个按钮完成注册(即 kind=click, id={button_id})。\n"
        f"然后根据工具返回,用一句话明确回答:注册成功了吗?\n"
        f"成功就说「成功」,失败就说「失败」并说明原因,不确定就说「不确定」。"
    )

    print(f"页面: {url}")
    print(f"模型: {MODEL}")
    print(f"Harness 预解析按钮: {button_id}")
    print("真相: POST /api/signup → HTTP 422(邮箱已占用),注册失败\n")

    try:
        results = []
        for mode in ("structure", "no-verdict", "full"):
            GROUND_TRUTH.clear()
            print(f"--- {mode} ---", flush=True)
            r = await run_arm(mode, url, button_id, task)
            r["verdict"] = judge(r["final_text"])
            r["ground_truth"] = list(GROUND_TRUTH)
            results.append(r)
            print(f"  工具序列: {' → '.join(r['tool_sequence']) or '(无)'}")
            print(f"  步数={r['steps']} 耗时={r['elapsed_ms']}ms tokens={r['tokens_in']}/{r['tokens_out']}")
            print(f"  后端实际: {r['ground_truth']}")
            print(f"  模型结论: {r['verdict']}")
            print(f"  模型原文: {r['final_text'][:260]!r}\n")

        print("=== 汇总 ===")
        print(f"{'档位':<13}{'结论':<11}{'步数':<6}{'耗时ms':<9}{'in_tok':<8}")
        for r in results:
            print(f"{r['mode']:<13}{r['verdict']:<11}{r['steps']:<6}{r['elapsed_ms']:<9}{r['tokens_in']:<8}")

        fp = [r for r in results if r["verdict"] == "success"]
        print(f"\n假成功(声称成功,真相失败): {[r['mode'] for r in fp] or '无'}")

        out = Path(__file__).resolve().parent / "c1_probe_results.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"结果: {out}")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
