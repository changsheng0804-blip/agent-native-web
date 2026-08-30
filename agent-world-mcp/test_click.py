# -*- coding: utf-8 -*-
"""验证坐标点击:打开 Google Flights,点击乘客按钮,确认面板弹出"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[r"F:\成果库\Agent 友好插件\agent-world-mcp\server.py"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 5000}),
                timeout=60,
            )
            data = json.loads(r.content[0].text)
            wid = data["world_id"]
            print(f"world_open: {wid}, {data['summary']['total']} 元素")

            r = await asyncio.wait_for(
                session.call_tool("world_entities", {"world_id": wid, "name": "passenger", "max_results": 3}),
                timeout=15,
            )
            print("点击前查 passenger:", r.content[0].text[:200])

            r = await asyncio.wait_for(
                session.call_tool("world_click", {"world_id": wid, "id": "el_104"}),
                timeout=15,
            )
            print("world_click:", r.content[0].text[:200])

            await asyncio.sleep(1.5)

            r = await asyncio.wait_for(
                session.call_tool("world_entities", {"world_id": wid, "text": "Adults", "max_results": 10}),
                timeout=15,
            )
            print("点击后查 passenger:", r.content[0].text[:800])

            r = await asyncio.wait_for(
                session.call_tool("world_changes", {"world_id": wid, "since": 0}),
                timeout=15,
            )
            data = json.loads(r.content[0].text)
            print(f"changes to={data['to']}, 最后 5 条:")
            for e in data["events"][-5:]:
                print("  ", e)

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
            print("closed")


if __name__ == "__main__":
    asyncio.run(main())