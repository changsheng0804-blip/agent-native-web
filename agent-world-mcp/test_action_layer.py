from pathlib import Path
# -*- coding: utf-8 -*-
"""行动层整合验证:
1. world_click 走 locator 路径(method=locator,带 auto-wait)
2. world_fill 走 locator-fill
3. world_press Enter 选择建议
4. 世界模型确认结果
"""
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
                session.call_tool("world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 5000}),
                timeout=60,
            )
            wid = json.loads(r.content[0].text)["world_id"]
            print(f"world_open: {wid}")

            r = await asyncio.wait_for(
                session.call_tool("world_click", {"world_id": wid, "id": "combobox.where-from"}),
                timeout=20,
            )
            print("click:", json.loads(r.content[0].text).get("method", "?"))

            r = await asyncio.wait_for(
                session.call_tool("world_fill", {"world_id": wid, "id": "combobox.where-from", "text": "Tokyo"}),
                timeout=20,
            )
            print("fill:", json.loads(r.content[0].text).get("method", "?"))

            await asyncio.sleep(1.5)

            r = await asyncio.wait_for(
                session.call_tool("world_press", {"world_id": wid, "id": "combobox.where-from", "key": "Enter"}),
                timeout=20,
            )
            print("press Enter:", json.loads(r.content[0].text).get("method", "?"))

            await asyncio.sleep(2)

            r = await asyncio.wait_for(
                session.call_tool("world_entities", {"world_id": wid, "text": "Tokyo, Japan", "max_results": 3}),
                timeout=15,
            )
            data = json.loads(r.content[0].text)
            print(f"含 'Tokyo, Japan' 构件: {data['count']} 个")
            for e in data["entities"][:3]:
                print(f"  {e['id']:10s} {e['name'][:36]}")

            r = await asyncio.wait_for(
                session.call_tool("world_changes", {"world_id": wid, "since": 0}),
                timeout=15,
            )
            data = json.loads(r.content[0].text)
            print(f"changes to={data['to']}")

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
            print("closed")


if __name__ == "__main__":
    asyncio.run(main())