from pathlib import Path
# -*- coding: utf-8 -*-
"""world_eval 验证:只读查询、函数表达式、大结果截断、错误处理"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "server.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 4000}),
                timeout=90,
            )
            wid = json.loads(r.content[0].text)["world_id"]

            r = await asyncio.wait_for(
                session.call_tool("world_eval", {"world_id": wid, "expression": "document.title"}),
                timeout=20,
            )
            print("1. document.title:", json.loads(r.content[0].text)["result"])

            r = await asyncio.wait_for(
                session.call_tool("world_eval", {"world_id": wid, "expression": "(() => ({ href: location.href, w: window.innerWidth, h: window.innerHeight }))()"}),
                timeout=20,
            )
            print("2. 函数表达式:", json.loads(r.content[0].text)["result"])

            r = await asyncio.wait_for(
                session.call_tool("world_eval", {"world_id": wid, "expression": "agentWorld.query.layers()"}),
                timeout=20,
            )
            text = json.loads(r.content[0].text)["result"]
            print(f"3. layers 结果长度: {len(text)} 字符(截断保护应生效于超限)")

            r = await asyncio.wait_for(
                session.call_tool("world_eval", {"world_id": wid, "expression": "undefinedVar.test"}),
                timeout=20,
            )
            print("4. 错误处理:", r.content[0].text[:120])

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
            print("done")


if __name__ == "__main__":
    asyncio.run(main())