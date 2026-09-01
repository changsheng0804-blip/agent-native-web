# -*- coding: utf-8 -*-
"""实时任务导览接入三条页面信道的最小验证。"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
DYN_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "dyn.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = opened["world_id"]
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": """() => {
                    const a = document.createElement('a');
                    a.id = 'guide-route';
                    a.href = '#guide-target';
                    a.textContent = '任务入口';
                    document.body.appendChild(a);
                    return true;
                }""",
            })
            await call(session, "world_wait", {
                "world_id": wid, "mode": "appear", "text": "任务入口", "timeout_ms": 5000,
            })

            guide = await call(session, "world_guide", {
                "world_id": wid,
                "task": "打开任务入口并确认页面跳转",
                "max_candidates": 3,
            })
            assert guide["channel"] == "task-guide"
            assert guide["state"]["url"].startswith("file:")
            assert guide["change_digest"]["channel"] == "change-digest"
            assert "events" not in guide["change_digest"]
            assert guide["recent_evidence"] == []
            assert guide["candidates"], "任务导览没有找到候选入口"
            candidate = next(x for x in guide["candidates"] if x.get("text") == "任务入口")
            assert candidate["relation"] == "direct-link-confirmed"
            assert guide["routes"] and guide["routes"][0]["status"] == "confirmed"
            print("1.任务导览: 找到相关区域、候选入口和已确认路径")

            clicked = await call(session, "world_click", {"world_id": wid, "id": candidate["id"]})
            assert clicked["effect"]["verdict"] == "effected"
            since_change = guide["next_cursors"]["change_since"]
            since_evidence = guide["next_cursors"]["evidence_since"]
            refreshed = await call(session, "world_guide", {
                "world_id": wid,
                "task": "确认任务入口后的页面状态",
                "change_since": since_change,
                "evidence_since": since_evidence,
                "max_candidates": 3,
            })
            assert refreshed["recent_evidence"], "动作后的任务导览没有接入操作证据"
            assert refreshed["state"]["url"].endswith("#guide-target")
            assert refreshed["change_digest"]["from"] == since_change
            assert refreshed["next_cursors"]["evidence_since"] >= since_evidence
            print("2.刷新导览: 接入最新页面状态、变化摘要和动作证据")

            await call(session, "world_close", {"world_id": wid})
            print("\n✅ 实时任务导览已接入三条页面信道")


if __name__ == "__main__":
    asyncio.run(main())
