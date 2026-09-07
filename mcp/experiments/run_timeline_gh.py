# -*- coding: utf-8 -*-
"""真实站点演示:统一时间线(GitHub 动作 → 跨层因果时间线)。

验证:真实站点上 action/request/response/dom 在同一条时间线,
     causal_windows 回答"这个动作引发了什么"。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
URL = "https://github.com/git/git"


async def call(session, name, args, timeout=90):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except Exception:
                return {"_raw": item.text[:200]}
    return {}


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {
                "url": URL, "ready_policy": "action", "wait_ms": 8000,
                "reuse_policy": "never", "task_id": "timeline-demo",
            }, timeout=150)
            wid = opened["world_id"]
            print("world:", wid, opened.get("url"))

            # 动作1:点击 Pull requests tab
            r = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                       "text": "Pull requests", "max_results": 5}, timeout=45)
            links = [e for e in r.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            if links:
                await call(session, "world_click", {"world_id": wid, "id": links[0]["id"]}, timeout=90)
                print("已点击 Pull requests")
            await asyncio.sleep(6)

            # 读时间线(digest)
            tl = await call(session, "world_timeline", {"world_id": wid, "since": 0}, timeout=45)
            print("\n===== world_timeline digest =====")
            print(json.dumps({k: v for k, v in tl.items() if k not in ("world_id", "channel")},
                             ensure_ascii=False, indent=1)[:2400])

            # 读原始事件(截取动作附近)
            raw = await call(session, "world_timeline", {"world_id": wid, "since": 0, "mode": "raw"}, timeout=45)
            print("\n===== 原始事件(前 18 条)=====")
            for e in raw.get("events", [])[:18]:
                print(f"  seq={e['seq']} {e['type']:<9} {json.dumps({k: v for k, v in e.items() if k not in ('seq','t','type','kt')}, ensure_ascii=False)[:90]}")
            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
