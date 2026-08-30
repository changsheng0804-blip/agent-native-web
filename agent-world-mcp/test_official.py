# -*- coding: utf-8 -*-
"""官方 MCP 客户端测 server:initialize + tools/list + world_open"""
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
            print("initialize: OK")

            tools = await session.list_tools()
            print(f"tools: {len(tools.tools)} ->", ", ".join(t.name for t in tools.tools))

            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://news.ycombinator.com/", "wait_ms": 3000}),
                timeout=60,
            )
            print("world_open:", r.content[0].text[:300])

            r = await asyncio.wait_for(
                session.call_tool("world_list", {}), timeout=10
            )
            print("world_list:", r.content[0].text[:200])

            r = await asyncio.wait_for(
                session.call_tool("world_close", {"world_id": 1}), timeout=15
            )
            print("world_close:", r.content[0].text[:200])


if __name__ == "__main__":
    asyncio.run(main())