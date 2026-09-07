# -*- coding: utf-8 -*-
"""server 层前提监视集成测试(真实 MCP 客户端 + 真实浏览器)。

覆盖:
  1. world_assume/ack/status 工具已注册
  2. 表单值被 JS 重置(事件流静默)→ 下次工具调用返回自动带 notices
  3. 通知消费后不重复出现
  4. ack 恢复监视,不再重复通知
  5. 双通道解析:DOM id 与内核 id(el_N)都能读值
运行: python mcp/experiments/test_server_premise.py
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
            for t in ("world_assume", "world_ack", "world_status"):
                assert t in names, f"工具未注册: {t}"
            print("PASS 工具注册: world_assume/world_ack/world_status")

            opened = await call(session, "world_open", {
                "url": FIXTURE.as_uri(), "ready_policy": "action", "wait_ms": 2000,
                "reuse_policy": "never", "task_id": "test-premise-server",
            }, timeout=90)
            wid = opened["world_id"]

            # 找出发地输入框(内核 id)
            r = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 10}, timeout=30)
            origin = next(e for e in r["entities"] if "出发地" in e["name"])
            print(f"PASS 定位输入框: {origin['id']} {origin['name']}")

            # 填写
            f1 = await call(session, "world_fill", {"world_id": wid, "id": origin["id"], "text": "北京"}, timeout=30)
            assert (f1.get("effect") or {}).get("verdict") in ("effected", "visual-effected"), f1
            print("PASS 填写 北京")

            # 提交假设(DOM id)
            a1 = await call(session, "world_assume", {"world_id": wid, "name": "origin",
                                                      "doc_id": "field-origin", "attr": "value", "expect": "北京"}, timeout=20)
            assert a1.get("assumed") == "origin", a1
            # 提交假设(内核 id,双通道)
            a2 = await call(session, "world_assume", {"world_id": wid, "name": "origin_el",
                                                      "doc_id": origin["id"], "attr": "value", "expect": "北京"}, timeout=20)
            assert a2.get("assumed") == "origin_el", a2
            print("PASS 假设提交(DOM id + 内核 id 双通道)")

            st = await call(session, "world_status", {"world_id": wid}, timeout=20)
            assert len(st.get("assumptions", [])) == 2, st
            print("PASS world_status 列出 2 个假设")

            # 外部重置值(事件流静默的变化)
            await call(session, "world_eval", {"world_id": wid,
                                               "expression": "() => { document.getElementById('field-origin').value = ''; return true; }"}, timeout=20)
            await asyncio.sleep(1.2)  # 等快检节流窗口

            # 任意工具调用 → 返回应带 notices
            r2 = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 5}, timeout=30)
            notices = r2.get("notices", [])
            assert notices, f"未收到前提失效通知: {r2.get('_raw', r2)}"
            names_violated = {n["name"] for n in notices}
            assert "origin" in names_violated and "origin_el" in names_violated, notices
            print(f"PASS 失效通知注入: {names_violated} (actual='')")

            # 通知消费后不重复
            r3 = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 5}, timeout=30)
            assert not r3.get("notices"), f"通知重复出现: {r3.get('notices')}"
            print("PASS 通知一次性消费")

            # ack 恢复:重填后再 ack,之后不再通知
            await call(session, "world_fill", {"world_id": wid, "id": origin["id"], "text": "北京"}, timeout=30)
            await call(session, "world_ack", {"world_id": wid, "name": "origin"}, timeout=20)
            await call(session, "world_ack", {"world_id": wid, "name": "origin_el"}, timeout=20)
            st2 = await call(session, "world_status", {"world_id": wid}, timeout=20)
            assert all(a["active"] for a in st2["assumptions"]), st2
            print("PASS ack 恢复监视")

            # 恢复后不再误报
            await asyncio.sleep(1.2)
            r4 = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 5}, timeout=30)
            assert not r4.get("notices"), f"恢复后误报: {r4.get('notices')}"
            print("PASS 恢复后无误报")

            await call(session, "world_close", {"world_id": wid}, timeout=15)
            print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
