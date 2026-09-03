# -*- coding: utf-8 -*-
"""验收 world_act(wait_policy=receipt) 的即时回执与最终查询。"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def call(session, name, args):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=30)
    return json.loads(result.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            opened = await call(session, "world_open", {
                "url": (FIX / "far_modal.html").as_uri(), "wait_ms": 100,
            })
            wid = opened["world_id"]
            found = await call(session, "world_find", {
                "world_id": wid, "role": "button", "text": "打开居中弹窗",
            })
            target = found["matches"][0]["id"]
            receipt = await call(session, "world_act", {
                "world_id": wid, "kind": "click", "id": target, "wait_policy": "receipt",
            })
            assert receipt["page_outcome"] == "pending" and receipt.get("action_id")
            final = None
            for _ in range(30):
                final = await call(session, "world_outcome", {
                    "world_id": wid, "action_id": receipt["action_id"],
                })
                if final.get("page_outcome") != "pending":
                    break
                await asyncio.sleep(0.1)
            assert final["page_outcome"] == "progressed", final
            await call(session, "world_close", {"world_id": wid})
    print("receipt 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
