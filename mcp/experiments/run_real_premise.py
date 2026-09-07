# -*- coding: utf-8 -*-
"""真实站点前提监视验证(HN 真实页面 + 受控失效)。

验证目标(真实站点环境下 server 层前提监视的工程行为):
  1. 0 误报:对真实稳定元素(故事标题/score)持续监视,多轮调用无假通知
  2. 端到端捕获:world_eval 模拟外部脚本修改真实 DOM → 下次工具调用收到通知
  3. 成本:快检对每次工具调用的附加延迟(节流后每前提每 1s 最多一次 evaluate)
  4. 双通道:内核 id(el_N)与 DOM 选择器都能监视
运行: python mcp/experiments/run_real_premise.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except Exception:
                return {"_raw": item.text[:200]}
    return {}


async def main():
    ARTIFACTS.mkdir(exist_ok=True)
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    results = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {
                "url": "https://news.ycombinator.com/", "ready_policy": "action",
                "wait_ms": 5000, "reuse_policy": "never", "task_id": "real-premise",
            }, timeout=150)
            wid = opened["world_id"]
            print("world:", wid, opened.get("url"))
            results["world"] = opened.get("url")

            # 1. 定位一个真实 story 标题 + score 元素(内核 id)
            r = await call(session, "world_entities", {"world_id": wid, "text": "points", "max_results": 10}, timeout=45)
            scores = [e for e in r.get("entities", []) if "point" in e.get("name", "")]
            print("score 元素:", [(e["id"], e["name"]) for e in scores[:3]])
            if not scores:
                print("未找到 score 元素,退出")
                return
            score = scores[0]
            title = await call(session, "world_entities", {"world_id": wid, "max_results": 100}, timeout=45)
            # 第一个链接
            links = [e for e in title.get("entities", []) if e.get("semantic") == "link" and e.get("name", "").startswith("link.")]
            print("首个 link:", links[0]["id"], links[0]["name"][:60] if links else None)

            # 2. 提交假设:内核 id 监视 score 文本(取 score 当前文本)
            score_expr = f"() => {{ const el = document.getElementById('{score['id']}') || (window.agentWorld && agentWorld._runtime.world.elements.get('{score['id']}') || {{}})._el || {{}}; return el.textContent || ''; }}"
            raw = await call(session, "world_eval", {"world_id": wid, "expression": score_expr}, timeout=30)
            raw_text = raw.get("result") if isinstance(raw, dict) else raw
            try:
                current_text = json.loads(raw_text)  # 双层反序列化(字符串值带引号)
            except Exception:
                current_text = str(raw_text).strip()
            print("score 当前文本:", repr(current_text))
            a1 = await call(session, "world_assume", {"world_id": wid, "name": "score",
                                                      "doc_id": score["id"], "attr": "textContent",
                                                      "expect": current_text}, timeout=20)
            assert a1.get("assumed") == "score", a1

            # 3. 0 误报验证:连续 6 次工具调用(间隔 ~1.5s,覆盖节流窗口),应无 notices
            false_alarms = 0
            for i in range(6):
                r2 = await call(session, "world_entities", {"world_id": wid, "text": "points", "max_results": 3}, timeout=45)
                if r2.get("notices"):
                    false_alarms += 1
                    print(f"  !! 误报: {r2['notices']}")
                await asyncio.sleep(1.5)
            results["false_alarms"] = false_alarms
            print(f"0 误报验证: 6 轮调用误报 {false_alarms} 次")

            # 4. 快检成本:测一次带监视的调用延迟
            t0 = time.perf_counter()
            await call(session, "world_status", {"world_id": wid}, timeout=20)
            status_latency = (time.perf_counter() - t0) * 1000
            results["status_latency_ms"] = round(status_latency, 1)
            print(f"world_status 延迟(含快检): {status_latency:.0f}ms")

            # 5. 端到端失效捕获:模拟外部脚本修改真实 DOM 的 score 文本
            await call(session, "world_eval", {"world_id": wid, "expression":
                f"() => {{ const el = window.agentWorld._runtime.world.elements.get('{score['id']}')?._el; if (el) el.textContent = el.textContent.replace(/\\d+ points/, '9999 points'); return el ? el.textContent : 'NONE'; }}"}, timeout=30)
            t_inject = time.time()
            await asyncio.sleep(1.5)  # 等快检节流窗口
            # 下次工具调用应带 notices
            got_notice = None
            for i in range(4):
                r3 = await call(session, "world_status", {"world_id": wid}, timeout=20)
                if r3.get("notices"):
                    got_notice = r3["notices"]
                    break
                await asyncio.sleep(1.0)
            results["notice"] = got_notice
            results["notice_delay_ms"] = int((time.time() - t_inject) * 1000) if got_notice else None
            if got_notice:
                print(f"✅ 真实页面失效捕获: {json.dumps(got_notice, ensure_ascii=False)} (延迟 {results['notice_delay_ms']}ms)")
            else:
                print("❌ 未捕获失效通知")

            # 6. ack 恢复后不再通知(恢复文本 = 原始值)
            restore_js = ("() => { const el = window.agentWorld._runtime.world.elements.get('%s')?._el; "
                          "if (el) el.textContent = %s; return true; }" % (score["id"], json.dumps(current_text, ensure_ascii=False)))
            await call(session, "world_eval", {"world_id": wid, "expression": restore_js}, timeout=30)
            await call(session, "world_ack", {"world_id": wid, "name": "score"}, timeout=20)
            await asyncio.sleep(1.5)
            r4 = await call(session, "world_status", {"world_id": wid}, timeout=20)
            results["post_ack_clean"] = not r4.get("notices")
            print("ack 恢复后无误报:", results["post_ack_clean"])

            await call(session, "world_close", {"world_id": wid}, timeout=15)

    out = ARTIFACTS / "real_premise_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n报告:", out)


if __name__ == "__main__":
    asyncio.run(main())
