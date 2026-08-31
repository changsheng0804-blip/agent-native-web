# -*- coding: utf-8 -*-
"""验证 world_fill:Google Flights 出发地输入框填 Tokyo,看建议列表出现"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parent / "server.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 5000}),
                timeout=60,
            )
            wid = json.loads(r.content[0].text)["world_id"]
            print(f"world_open: {wid}")

            r = await asyncio.wait_for(
                session.call_tool("world_click", {"world_id": wid, "id": "combobox.where-from"}),
                timeout=15,
            )
            print("click where-from:", r.content[0].text[:150])

            r = await asyncio.wait_for(
                session.call_tool("world_fill", {"world_id": wid, "id": "combobox.where-from", "text": "Tokyo"}),
                timeout=15,
            )
            print("fill Tokyo:", r.content[0].text[:150])

            await asyncio.sleep(2)

            r = await asyncio.wait_for(
                session.call_tool("world_entities", {"world_id": wid, "text": "Tokyo", "max_results": 8}),
                timeout=15,
            )
            data = json.loads(r.content[0].text)
            print(f"含 Tokyo 文本的构件: {data['count']} 个")
            for e in data["entities"][:8]:
                print(f"  {e['id']:14s} {e['name'][:36]:36s} text={e['text'][:40]!r}")

            r = await asyncio.wait_for(
                session.call_tool("world_screenshot", {"world_id": wid}),
                timeout=15,
            )
            print("screenshot:", json.loads(r.content[0].text)["path"])

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)


if __name__ == "__main__":
    asyncio.run(main())