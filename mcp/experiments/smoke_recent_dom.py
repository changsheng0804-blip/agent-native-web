# -*- coding: utf-8 -*-
"""冒烟:验证 server digest 的 recent_dom 语义化字段。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ab_fixtures
from run_timeline_inject import call

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"


async def main():
    server, base = ab_fixtures.serve()
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), timeout=30)
                opened = await call(s, "world_open", {"url": base + "/ab/price.html",
                                                      "ready_policy": "action", "wait_ms": 1500,
                                                      "reuse_policy": "never", "task_id": "smoke"}, timeout=90)
                wid = opened["world_id"]
                await asyncio.sleep(2.5)  # 等价格变化(1.5s 轮询 → 1200)
                tl = await call(s, "world_timeline", {"world_id": wid, "since": 0}, timeout=30)
                print("counts:", tl.get("counts"))
                print("dom_changes:", tl.get("dom_changes"))
                print("recent_dom:", json.dumps(tl.get("recent_dom"), ensure_ascii=False))
                await call(s, "world_close", {"world_id": wid}, timeout=15)
    finally:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
