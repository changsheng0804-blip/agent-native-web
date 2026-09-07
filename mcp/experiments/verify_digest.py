# -*- coding: utf-8 -*-
"""world_change_digest 重核:计数摘要对真实 harness 的可读性。

同一次真实导航后读取两种摘要:
  A. world_change_digest(变化摘要信道:counts/importance/semantic/key)
  B. world_timeline digest(统一时间线:counts/statuses/api_hits/recent_dom/窗口)
由对话中的 agent 判断:哪种表达能支撑"页面发生了什么"的决策。
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


async def call(session, name, args, timeout=120):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    return {}


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 8000, "reuse_policy": "never",
                                                        "task_id": "verify-digest"}, timeout=150)
            wid = opened["world_id"]
            print("WORLD", wid, opened.get("url"))

            r = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                       "text": "Pull requests", "max_results": 5}, timeout=45)
            links = [e for e in r.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            await call(session, "world_click", {"world_id": wid, "id": links[0]["id"]}, timeout=120)
            await asyncio.sleep(4)

            print("\n===== A. world_change_digest(导航后) =====")
            d = await call(session, "world_change_digest", {"world_id": wid, "since": 0}, timeout=45)
            print(json.dumps({k: d.get(k) for k in ("changed", "events_seen", "counts",
                                                    "importance_counts", "semantic_counts", "key")},
                             ensure_ascii=False, indent=1))

            print("\n===== B. world_timeline digest(同窗口) =====")
            tl = await call(session, "world_timeline", {"world_id": wid, "since": 0}, timeout=45)
            print(json.dumps({k: tl.get(k) for k in ("counts", "statuses", "api_hits",
                                                     "dom_changes", "recent_dom",
                                                     "silent_failures")},
                             ensure_ascii=False, indent=1))

            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
