# -*- coding: utf-8 -*-
"""world_guide 重核:导览是否真减少定位成本(真实站点对比)。

每个任务两种定位路径:
  A. 无导览:world_entities 按直觉过滤盲查,记录命中质量
  B. 有导览:world_guide(task) → 读候选/next_action,记录定位轮次
作为真实 harness(Flash 模型),逐任务判断:导览候选是否直接可执行。
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

TASKS = [
    {"name": "t1-pulls", "task": "打开该仓库的 Pull requests 页面",
     "blind": [{"role": "link", "text": "Pull"}, {"role": "link"}]},
    {"name": "t2-star", "task": "给该仓库点 Star",
     "blind": [{"role": "button", "text": "Star"}, {"role": "button"}]},
    {"name": "t3-releases", "task": "打开该仓库的 Releases 页面",
     "blind": [{"role": "link", "text": "Release"}, {"role": "link", "text": "releases"}]},
]


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    return {}


def guide_compact(g):
    return {
        "candidates": [{"id": c.get("id"), "name": c.get("name"), "text": c.get("text"),
                        "semantic": c.get("semantic"), "score": c.get("match_score"),
                        "href": c.get("href"), "rel": c.get("relation"),
                        "region": (c.get("region") or {}).get("semantic")}
                       for c in (g.get("candidates") or [])[:4]],
        "next_action": g.get("next_action"),
        "regions": [(r.get("semantic"), r.get("name")) for r in (g.get("regions") or [])[:3]],
    }


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 8000, "reuse_policy": "never",
                                                        "task_id": "verify-guide"}, timeout=150)
            wid = opened["world_id"]
            print("WORLD", wid, opened.get("url"))

            for t in TASKS:
                print(f"\n########## {t['name']}: {t['task']} ##########")
                # A. 无导览盲查(模拟模型直觉过滤)
                print("-- 盲查路径 --")
                for i, filt in enumerate(t["blind"], 1):
                    t0 = asyncio.get_event_loop().time()
                    r = await call(session, "world_entities", {"world_id": wid, **filt,
                                                               "max_results": 8}, timeout=45)
                    ents = r.get("entities", [])
                    hits = [(e.get("id"), e.get("name"), e.get("semantic"))
                            for e in ents[:5]]
                    print(f"  第{i}轮 {filt} → {len(ents)}条: {hits}")
                    # 若第一轮就命中目标(名称含任务关键词)即停止
                    if any(any(k in str(e.get("name", "")) for k in
                               ("Pull", "Star", "Release")) for e in ents):
                        print(f"  → 第{i}轮盲查命中,可执行")
                        break
                # B. 有导览
                print("-- 导览路径 --")
                t0 = asyncio.get_event_loop().time()
                g = await call(session, "world_guide", {"world_id": wid, "task": t["task"],
                                                        "max_candidates": 6}, timeout=60)
                gt = round((asyncio.get_event_loop().time() - t0) * 1000)
                print(f"  [耗时 {gt}ms]")
                print(json.dumps(guide_compact(g), ensure_ascii=False))

            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
