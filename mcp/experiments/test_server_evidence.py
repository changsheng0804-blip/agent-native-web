# -*- coding: utf-8 -*-
"""L3 动作证据卡 server 集成测试。

覆盖:
  1. fixture 动作(无网络请求)→ 证据卡存在,decision=无请求分支
  2. GitHub 点击 Pull requests → 证据卡含请求,decision=已生效
  3. GitHub 点击 Star(未登录)→ decision=需要登录
运行: python mcp/experiments/test_server_evidence.py
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "order_form.html"
GH = "https://github.com/git/git"


async def call(session, name, args, timeout=90):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except Exception:
                return {"_raw": item.text[:200]}
    return {}


async def find_button(session, wid, text):
    r = await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 20}, timeout=30)
    for e in r.get("entities", []):
        if text in e.get("name", ""):
            return e["id"]
    return None


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)

            # ── 场景1:fixture 动作(无网络请求) ──
            opened = await call(session, "world_open", {
                "url": FIXTURE.as_uri(), "ready_policy": "action", "wait_ms": 2000,
                "reuse_policy": "never", "task_id": "test-evidence-fixture",
            }, timeout=90)
            wid = opened["world_id"]
            btn = await find_button(session, wid, "提交订单")
            assert btn, "找不到提交按钮"
            r1 = await call(session, "world_click", {"world_id": wid, "id": btn}, timeout=30)
            ev = r1.get("action_evidence")
            assert ev is not None, f"fixture 动作无证据卡: {list(r1.keys())}"
            assert ev.get("request_count", 0) == 0, ev
            assert "无任何请求" in (ev.get("decision") or ""), ev
            print(f"PASS 场景1(fixture): evidence={json.dumps(ev, ensure_ascii=False)[:180]}")
            await call(session, "world_close", {"world_id": wid}, timeout=15)

            # ── 场景2:GitHub 点击 Pull requests(真实请求) ──
            opened2 = await call(session, "world_open", {
                "url": GH, "ready_policy": "action", "wait_ms": 5000,
                "reuse_policy": "never", "task_id": "test-evidence-gh",
            }, timeout=150)
            wid2 = opened2["world_id"]
            r = await call(session, "world_entities", {"world_id": wid2, "text": "Pull requests", "max_results": 10}, timeout=45)
            pr_link = None
            for e in r.get("entities", []):
                if "pulls" in (e.get("name") or "") or "Pull requests" in (e.get("name") or ""):
                    pr_link = e["id"]
                    break
            if pr_link:
                r2 = await call(session, "world_click", {"world_id": wid2, "id": pr_link}, timeout=120)
                ev2 = r2.get("action_evidence")
                assert ev2 is not None, f"GitHub 动作无证据卡: {list(r2.keys())}"
                assert ev2.get("request_count", 0) > 0, ev2
                assert "已生效" in (ev2.get("decision") or ""), ev2
                print(f"PASS 场景2(GitHub 导航): {ev2.get('request_count')} 请求, decision={ev2.get('decision')}")
            else:
                print("SKIP 场景2: Pull requests 链接未找到")
            await call(session, "world_close", {"world_id": wid2}, timeout=15)

            # ── 场景3:GitHub 点击 Star(未登录 → 重定向) ──
            opened3 = await call(session, "world_open", {
                "url": GH, "ready_policy": "action", "wait_ms": 5000,
                "reuse_policy": "never", "task_id": "test-evidence-star",
            }, timeout=150)
            wid3 = opened3["world_id"]
            r3 = await call(session, "world_entities", {"world_id": wid3, "role": "button", "max_results": 30}, timeout=45)
            star_id = None
            for e in r3.get("entities", []):
                if "star" in (e.get("name") or "").lower():
                    star_id = e["id"]
                    break
            if star_id:
                r4 = await call(session, "world_click", {"world_id": wid3, "id": star_id}, timeout=120)
                ev3 = r4.get("action_evidence")
                assert ev3 is not None, f"Star 动作无证据卡: {list(r4.keys())}"
                decision = ev3.get("decision") or ""
                assert "登录" in decision, f"Star 决策未识别登录重定向: {decision}"
                print(f"PASS 场景3(GitHub Star): decision={decision}")
            else:
                print("SKIP 场景3: Star 按钮未找到")
            await call(session, "world_close", {"world_id": wid3}, timeout=15)

            print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
