# -*- coding: utf-8 -*-
"""B2 假成功代价最小对照演示

同一个页面、同一次点击,三种读法并排:

  A 朴素 DOM 读法   —— 只读页面可见文本(模拟没有验证层的 agent)
  B 后果卡读法      —— 本项目的 page_outcome / effect / errors
  C 真值裁判        —— 隐藏的 ground truth:后端实际返回了什么

设计要点:
  - A 与 B 看到的是**同一时刻的同一页面**;差异只来自"读法",不来自环境。
  - C 对 agent 不可见(harness 独占),这正是核心命题说的"缺失的外部证据"。
  - 零 LLM 成本、纯本地、可复现。

用法:
  python mcp/demo_false_success.py              # 打印对照表
  python mcp/demo_false_success.py --report     # 同时写 docs/对照演示-假成功.md
  python mcp/demo_false_success.py --case 2     # 选反例(1/2/3/4/5,默认 1)
"""
import argparse
import asyncio
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURES = ROOT / "tests" / "fixtures"

# 隐藏裁判的记录区(agent 不可见;仅 harness 读)
GROUND_TRUTH = {"requests": []}

CASES = {
    "1": {
        "title": "显示成功但后端 422",
        "button": "提交注册",
        "status_id": "status1",
        "agent_sees": "✅ 注册成功!",
        "truth": "POST 返回 HTTP 422(账号已存在)",
    },
    "2": {
        "title": "按钮变灰但请求 rollback",
        "button": "确认支付",
        "status_id": "status2",
        "agent_sees": "处理中…(按钮变灰)",
        "truth": "1 秒后后端回滚,状态变为失败",
    },
    "3": {
        "title": "202 后异步失败",
        "button": "提交任务",
        "status_id": "status3",
        "agent_sees": "✅ 已提交,任务排队中(202)",
        "truth": "3 秒后后台任务失败",
    },
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/")
        f = FIXTURES / path
        if f.is_file():
            self._send(200, f.read_bytes())
        else:
            self._send(404, b"not found")

    def do_POST(self):
        # 隐藏裁判:记录真实响应(agent 看不到这条通道)
        if "simulate=422" in self.path:
            body = json.dumps({"error": "email_taken", "message": "账号已存在"}, ensure_ascii=False).encode("utf-8")
            GROUND_TRUTH["requests"].append({"path": self.path, "status": 422, "body": body.decode("utf-8")})
            self._send(422, body, "application/json; charset=utf-8")
        elif "simulate=500" in self.path:
            body = json.dumps({"error": "payment_rollback", "message": "支付失败,已回滚"}, ensure_ascii=False).encode("utf-8")
            GROUND_TRUTH["requests"].append({"path": self.path, "status": 500, "body": body.decode("utf-8")})
            self._send(500, body, "application/json; charset=utf-8")
        elif "simulate=202" in self.path:
            body = json.dumps({"task_id": "t-1", "status": "queued"}, ensure_ascii=False).encode("utf-8")
            GROUND_TRUTH["requests"].append({"path": self.path, "status": 202, "body": body.decode("utf-8")})
            self._send(202, body, "application/json; charset=utf-8")
        else:
            GROUND_TRUTH["requests"].append({"path": self.path, "status": 200, "body": "{}"})
            self._send(200, b"{}", "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        return


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def run_demo(case_id: str):
    case = CASES[case_id]
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/external_facts.html"

    result = {"case": case, "url": url}
    try:
        params = StdioServerParameters(command=sys.executable, args=[SERVER])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                d = await call(session, "world_open", {"url": url, "wait_ms": 1500})
                wid = d["world_id"]

                f = await call(session, "world_find", {"world_id": wid, "q": case["button"]})
                matches = f.get("matches") or []
                if not matches:
                    raise RuntimeError(f"未找到按钮: {case['button']}")

                # ---- 动作(两种读法看到的是同一个动作的同一个结果) ----
                card = await call(session, "world_act",
                                  {"world_id": wid, "kind": "click", "id": matches[0]["id"]})

                # ---- 读法 A:朴素 DOM(只看可见文本) ----
                naive = await call(session, "world_eval", {"world_id": wid, "expression":
                    f"() => {{ const el = document.getElementById('{case['status_id']}'); "
                    f"return el ? el.textContent.trim() : ''; }}"})
                result["naive_text"] = (naive.get("result") or "").strip('"')

                # ---- 读法 B:后果卡 ----
                result["card"] = {
                    "page_outcome": card.get("page_outcome"),
                    "situation": (card.get("situation") or {}).get("type"),
                    "why": card.get("why"),
                    "errors": card.get("errors") or (card.get("situation") or {}).get("errors"),
                }

                # 等异步场景展开(②③)
                if case_id in ("2", "3"):
                    await asyncio.sleep(3.5)
                    naive2 = await call(session, "world_eval", {"world_id": wid, "expression":
                        f"() => {{ const el = document.getElementById('{case['status_id']}'); "
                        f"return el ? el.textContent.trim() : ''; }}"})
                    result["naive_text_later"] = (naive2.get("result") or "").strip('"')

                await call(session, "world_close", {"world_id": wid})
    finally:
        httpd.shutdown()

    # ---- 读法 C:隐藏裁判 ----
    result["ground_truth"] = GROUND_TRUTH["requests"]
    return result


def render(result, standalone: bool = True) -> str:
    case = result["case"]
    card = result["card"]
    lines = []
    if standalone:
        lines.append(f"# 对照演示:假成功代价({case['title']})\n")
        lines.append("> 同一页面、同一次点击,三种读法。零 LLM 成本,本地可复现。\n")
        lines.append(f"> 页面:{result['url']}\n")
    else:
        lines.append(f"## 场景:{case['title']}\n")
    lines.append("| 读法 | 看到什么 | 结论 |")
    lines.append("|---|---|---|")
    # 朴素读法的结论按文本动态判断,不写死(避免"因错误的原因好看")
    naive = result["naive_text"]
    if any(k in naive for k in ("✅", "处理中", "排队中", "成功")):
        naive_verdict = "**无失败信号** → 继续下一步"
    elif "❌" in naive or "失败" in naive:
        naive_verdict = "看到失败(但无法区分前后端)"
    else:
        naive_verdict = "无信号"
    lines.append(f"| **A 朴素 DOM**(无验证层) | `{naive}` | {naive_verdict} |")
    # 后果卡的结论也按实际判定动态生成(不写死)
    po = card["page_outcome"]
    card_verdict = {
        "errored": "❌ 明确报错(有结构化原因)",
        "challenged": "⚠️ 被拦截,转人工",
        "unchanged": "⚠️ 未观察到生效证据(不假成功)",
        "uncertain": "⚠️ 有变化但不确定(要求复核)",
        "progressed": "✅ 已生效",
    }.get(po, po)
    lines.append(f"| **B 后果卡**(本项目) | `page_outcome={po}` / `situation={card['situation']}` | {card_verdict} |")
    gt = result["ground_truth"]
    gt_desc = "; ".join(f"{r['path']} → HTTP {r['status']}" for r in gt) or "(无请求)"
    lines.append(f"| **C 真值裁判**(agent 不可见) | `{gt_desc}` | 真相 |")
    lines.append("")
    lines.append(f"- 动作返回时页面**显示**:`{naive}`")
    lines.append(f"- 后端**实际**:`{gt_desc}`")
    lines.append(f"- 后果卡**判定**:`{po}` — {card['why']}")
    lines.append("")
    if card.get("errors"):
        lines.append("```json")
        lines.append(json.dumps(card["errors"], ensure_ascii=False, indent=1)[:600])
        lines.append("```")
        lines.append("")
    if result.get("naive_text_later") is not None:
        lines.append(f"- 3.5 秒后页面文本:`{result['naive_text_later']}`(异步结果不在动作窗口内)")
        lines.append("")
    lines.append("## 为什么这值得看\n")
    lines.append("朴素 DOM 读法拿到的是**页面自称的事实**;后果卡拿到的是**环境证据**(网络状态码)。")
    lines.append("两者不一致时,只有后者能反驳前者。这不是模型能力问题——")
    lines.append("后端 422/500 这些事实**不在页面的任何观察里**,再强的模型也推不出来。\n")
    if po in ("unchanged", "uncertain"):
        lines.append("> 本例后果卡给的是 `unchanged`/`uncertain` 而非 `errored`:")
        lines.append("> 说明**异步结果不在动作窗口内**——这正是 Runtime 待补的「异步结果追踪」职责,")
        lines.append("> 也是 A6 基准里尚未点亮的一条。\n")
    lines.append("> 对应核心命题 §2.2:需要「缺失的外部证据」的部分,不可能靠模型 scaling 凭空解决。")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="1", choices=sorted(CASES) + ["all"])
    ap.add_argument("--report", action="store_true", help="同时写 docs/对照演示-假成功.md")
    a = ap.parse_args()

    results = []
    if a.case == "all":
        for cid in sorted(CASES):
            GROUND_TRUTH["requests"] = []
            results.append(await run_demo(cid))

        summary_rows = []
        for r in results:
            statuses = ", ".join("HTTP %s" % x["status"] for x in r["ground_truth"]) or "(无请求)"
            summary_rows.append(f"| {r['case']['title']} | `{r['naive_text']}` | {statuses} | `{r['card']['page_outcome']}` |")

        parts = [render(r, standalone=False) for r in results]
        md = ("# 对照演示:假成功代价(三个场景)\n\n"
              "> 同一页面、同一次点击,三种读法。零 LLM 成本,本地可复现。\n"
              "> 复现命令:`python mcp/demo_false_success.py --case all --report`\n\n"
              "## 结论一览\n\n"
              "| 场景 | 动作返回时页面显示 | 后端实际 | 后果卡判定 |\n"
              "|---|---|---|---|\n"
              + "\n".join(summary_rows)
              + "\n\n---\n\n" + "\n\n---\n\n".join(parts))
    else:
        result = await run_demo(a.case)
        results.append(result)
        md = render(result)

    print(md)

    if a.report:
        out = ROOT / "docs" / "对照演示-假成功.md"
        out.write_text(md, encoding="utf-8")
        print(f"\n报告已写入: {out}")

    # 自检(守护):这些场景页面都"看起来正常",后果卡**不得**报 progressed。
    # 若报 progressed,就是把"页面自称成功"当成了"环境确认成功"——假成功红线。
    bad = [r for r in results if r["card"]["page_outcome"] == "progressed"]
    if bad:
        print("\n❌ 假成功红线被突破(后果卡判 progressed):")
        for r in bad:
            print(f"   - {r['case']['title']}: page_outcome=progressed")
        raise SystemExit(1)
    print(f"\n✅ 自检通过:{len(results)} 个场景均未把「页面显示成功」当成「环境确认成功」")
    raise SystemExit(0)


if __name__ == "__main__":
    asyncio.run(main())
