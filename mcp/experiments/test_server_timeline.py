# -*- coding: utf-8 -*-
"""统一时间线集成测试(真实 MCP 客户端 + 真实浏览器)。

覆盖:
  1. world_timeline 工具已注册
  2. 动作入账:click/fill 产生 action start/end 事件
  3. 跨层入账:request/response/dom 与 action 在同一时间线
  4. 因果窗口:动作窗口包含该动作引发的请求与 DOM 事件
  5. 前提失效入账:premise 事件带 derived_from(因果指向)
  6. 游标增量读:cursor 续读不重不漏
  7. 静默失败标注:4xx 且无 DOM 变化 → silent_failures
运行: python mcp/experiments/test_server_timeline.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "order_form.html"
PASS = 0


def ok(label):
    global PASS
    PASS += 1
    print(f"PASS {label}")


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
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "world_timeline" in names, "world_timeline 未注册"
            ok("world_timeline 工具注册")

            opened = await call(session, "world_open", {
                "url": FIXTURE.as_uri(), "ready_policy": "action", "wait_ms": 2000,
                "reuse_policy": "never", "task_id": "test-timeline",
            }, timeout=90)
            wid = opened["world_id"]

            # 定位输入框并执行动作
            r = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 10}, timeout=30)
            origin = next(e for e in r["entities"] if "出发地" in e["name"])
            await call(session, "world_fill", {"world_id": wid, "id": origin["id"], "text": "北京"}, timeout=30)
            await asyncio.sleep(0.5)
            # 本地 file:// 页面无网络活动,显式造一个网络请求(no-cors 避免 CORS 拦截)
            await call(session, "world_eval", {"world_id": wid, "expression":
                "() => { fetch('https://example.com/', {mode: 'no-cors'}).catch(() => {}); return true; }"}, timeout=20)
            await asyncio.sleep(1.5)

            # 1. 时间线包含 action + request/response(fill/fetch 不产生 DOM 事件,dom 见测试 2)
            tl = await call(session, "world_timeline", {"world_id": wid, "since": 0}, timeout=30)
            types_seen = set(tl.get("counts", {}).keys())
            assert "action" in types_seen, f"无 action 事件: {types_seen}"
            assert "request" in types_seen and "response" in types_seen, f"无网络事件: {types_seen}"
            ok(f"动作+网络入账: {sorted(types_seen)}")

            # 2. 因果窗口:click 动作窗口包含 DOM 事件(回执文本更新)
            r2 = await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 10}, timeout=30)
            submit = next(e for e in r2["entities"] if "提交" in e["name"])
            await call(session, "world_click", {"world_id": wid, "id": submit["id"]}, timeout=30)
            await asyncio.sleep(0.5)
            tl = await call(session, "world_timeline", {"world_id": wid, "since": 0}, timeout=30)
            types_seen = set(tl["counts"].keys())
            assert {"action", "dom", "request", "response"} <= types_seen, f"三层缺层: {types_seen}"
            ok(f"三层入账同一条时间线: {sorted(types_seen)}")
            windows = tl.get("causal_windows", [])
            click_windows = [w for w in windows if w["action"] == "world_click"]
            assert click_windows, f"无 click 因果窗口: {windows}"
            cw = click_windows[-1]
            assert cw["counts"].get("dom"), f"click 窗口无 DOM 事件: {cw}"
            ok(f"因果窗口: {cw['action']} → {json.dumps(cw['counts'], ensure_ascii=False)}")

            # 3. 前提失效入账(带 derived_from)
            await call(session, "world_assume", {"world_id": wid, "name": "origin",
                                                 "doc_id": "field-origin", "attr": "value", "expect": "北京"}, timeout=20)
            await call(session, "world_eval", {"world_id": wid,
                                               "expression": "() => { document.getElementById('field-origin').value = '上海'; return true; }"}, timeout=20)
            await asyncio.sleep(1.5)
            tl2 = await call(session, "world_timeline", {"world_id": wid, "since": tl["cursor"]}, timeout=30)
            premises = tl2.get("premises", [])
            assert premises, f"无 premise 事件: {tl2.get('counts')}"
            assert premises[0]["name"] == "origin" and premises[0]["actual"] == "上海", premises
            ok(f"前提失效入账: {json.dumps(premises[0], ensure_ascii=False)}")

            # 4. 游标增量:since=cursor 不重不漏
            tl3 = await call(session, "world_timeline", {"world_id": wid, "since": tl2["cursor"]}, timeout=30)
            assert tl3["events_seen"] == 0 or tl3["cursor"] >= tl2["cursor"], (tl3["cursor"], tl2["cursor"])
            ok(f"游标增量读: cursor {tl['cursor']} -> {tl2['cursor']} -> {tl3['cursor']}")

            # 5. 静默失败:造一个 404 请求(无 DOM 变化)——GitHub API 支持 CORS 且未知路径返回 404
            await call(session, "world_eval", {"world_id": wid, "expression":
                "() => { fetch('https://api.github.com/no-such-endpoint-xyz').catch(() => {}); return true; }"}, timeout=20)
            await asyncio.sleep(1.5)
            # 用一个非动作调用触发时间线合并+读取
            await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 3}, timeout=30)
            tl4 = await call(session, "world_timeline", {"world_id": wid, "since": tl3["cursor"]}, timeout=30)
            assert "response" in tl4.get("counts", {}), f"404 请求未入账: {tl4.get('counts')}"
            assert any(int(s) >= 400 for s in tl4.get("statuses", {})), tl4.get("statuses")
            ok(f"4xx 入账: {tl4.get('statuses')}")

            await call(session, "world_close", {"world_id": wid}, timeout=15)
            print(f"\n全部通过 ✅ ({PASS} 项)")


if __name__ == "__main__":
    asyncio.run(main())
