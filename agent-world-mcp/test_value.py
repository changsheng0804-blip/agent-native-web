# -*- coding: utf-8 -*-
"""验证 value 字段:fill 后原生网页世界能看到输入框的值"""
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

            r = await asyncio.wait_for(
                session.call_tool("world_click", {"world_id": wid, "id": "combobox.where-from"}),
                timeout=20,
            )
            r = await asyncio.wait_for(
                session.call_tool("world_fill", {"world_id": wid, "id": "combobox.where-from", "text": "Tokyo"}),
                timeout=20,
            )
            print("fill:", json.loads(r.content[0].text).get("method"))

            r = await asyncio.wait_for(
                session.call_tool("world_entity", {"world_id": wid, "id": "combobox.where-from"}),
                timeout=15,
            )
            ent = json.loads(r.content[0].text)
            print("getEntity value:", repr(ent.get("attributes", {}).get("value")))

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)


if __name__ == "__main__":
    asyncio.run(main())