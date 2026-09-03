# -*- coding: utf-8 -*-
"""分级打开策略：action 不等全量扫描，stable 仍可显式使用。"""
import asyncio, json, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

async def call(s, n, a):
    r = await asyncio.wait_for(s.call_tool(n, a), timeout=40)
    return json.loads(r.content[0].text)

async def main():
    p = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(p) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            a = await call(s, "world_open", {"url": (FIX / "far_modal.html").as_uri(), "wait_ms": 0, "ready_policy": "action"})
            assert a["readiness"]["action"] is True
            assert a["readiness"]["full_scan"] in ("pending", "ready")
            await call(s, "world_close", {"world_id": a["world_id"]})
            b = await call(s, "world_open", {"url": (FIX / "far_modal.html").as_uri(), "wait_ms": 0, "ready_policy": "stable", "stabilize_ms": 5000})
            assert b["readiness"]["stable"] is True, b
            await call(s, "world_close", {"world_id": b["world_id"]})
    print("readiness policy 测试通过")

if __name__ == "__main__": asyncio.run(main())
