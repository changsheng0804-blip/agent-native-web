# -*- coding: utf-8 -*-
"""对比:persistent context(profile)vs 普通 launch 的原生网页世界差异"""
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

            # A. 无 profile(headful)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {
                    "url": "https://www.goofish.com/search?q=Antigravity",
                    "wait_ms": 6000, "headful": True,
                }),
                timeout=120,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"A. 无 profile(headful): worldElements={st['world'].get('elements')} domVisible={st['page'].get('domTotal')} state={st['page']['state']}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": data["world_id"]}), timeout=15)

            # B. profile(headful)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {
                    "url": "https://www.goofish.com/search?q=Antigravity",
                    "wait_ms": 6000, "headful": True, "profile": "google-flow",
                }),
                timeout=120,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"B. profile(headful): worldElements={st['world'].get('elements')} domVisible={st['page'].get('domTotal')} state={st['page']['state']}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": data["world_id"]}), timeout=15)

            # C. 无 profile(headless)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {
                    "url": "https://www.goofish.com/search?q=Antigravity",
                    "wait_ms": 6000,
                }),
                timeout=120,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"C. 无 profile(headless): worldElements={st['world'].get('elements')} domVisible={st['page'].get('domTotal')} state={st['page']['state']}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": data["world_id"]}), timeout=15)


if __name__ == "__main__":
    asyncio.run(main())