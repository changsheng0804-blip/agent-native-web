# -*- coding: utf-8 -*-
"""两个网页世界并行调用的墙钟时间对比。"""
import asyncio, json, sys, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
SERVER = str(Path(__file__).resolve().parent / "server.py")
BASE = "http://127.0.0.1:8765/"

async def call(s, n, a):
    r = await asyncio.wait_for(s.call_tool(n, a), timeout=60)
    return json.loads(r.content[0].text)

async def main():
    p = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(p) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            t0 = time.perf_counter()
            opens = await asyncio.gather(
                call(s, "world_open", {"url": BASE + "dyn.html", "wait_ms": 0, "ready_policy": "action"}),
                call(s, "world_open", {"url": BASE + "far_modal.html", "wait_ms": 0, "ready_policy": "action"}),
            )
            t1 = time.perf_counter()
            d1 = await call(s, "world_find", {"world_id": opens[0]["world_id"], "role": "button", "text": "搜索"})
            d2 = await call(s, "world_find", {"world_id": opens[1]["world_id"], "role": "button", "text": "打开居中弹窗"})
            t2 = time.perf_counter()
            acts = await asyncio.gather(
                call(s, "world_act", {"world_id": opens[0]["world_id"], "kind": "click", "id": d1["matches"][0]["id"]}),
                call(s, "world_act", {"world_id": opens[1]["world_id"], "kind": "click", "id": d2["matches"][0]["id"]}),
            )
            t3 = time.perf_counter()
            for o in opens: await call(s, "world_close", {"world_id": o["world_id"]})
            print(json.dumps({"open_wall_ms": round((t1-t0)*1000), "find_wall_ms": round((t2-t1)*1000),
                              "action_wall_ms": round((t3-t2)*1000), "outcomes": [a.get("page_outcome") for a in acts]}, ensure_ascii=False, indent=2))

if __name__ == "__main__": asyncio.run(main())
