# -*- coding: utf-8 -*-
"""最终验证:正常站点(GF)上 frames/anomaly/navigate 无异常"""
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
                session.call_tool("world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 4000}),
                timeout=90,
            )
            data = json.loads(r.content[0].text)
            wid = data["world_id"]
            st = data["status"]
            print(f"GF world_open: worldElements={st['world'].get('elements')} domVisible={st['page'].get('domTotal')} state={st['page']['state']}")
            print(f"  frames({len(st['frames'])}):", [(f['url'][:50], f['elements']) for f in st['frames']])
            print(f"  anomaly={'anomaly' in st['page']['state']}")
            r = await asyncio.wait_for(
                session.call_tool("world_navigate", {"world_id": wid, "url": "https://www.google.com/travel/flights?hl=en", "wait_ms": 3000}),
                timeout=60,
            )
            st = json.loads(r.content[0].text)["status"]
            print(f"navigate 后: url={st['page']['url'][:60]} state={st['page']['state']}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
            print("done")


if __name__ == "__main__":
    asyncio.run(main())