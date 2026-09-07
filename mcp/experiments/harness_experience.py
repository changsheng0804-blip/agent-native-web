# -*- coding: utf-8 -*-
"""真实 harness 体验:Flash 模型 agent 直接走 MCP,逐动作输出两层信息。

每个动作输出:
  A. 后果卡(无时间线时的全部决策依据)
  B. 时间线摘要(有时间线时的附加信息)
由对话中的 agent 本身(真实 harness)逐动作复盘决策差异。
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
            return json.loads(item.text)
    return {}


def card_compact(card):
    """后果卡的关键决策字段(模拟 harness 只给模型的量)。"""
    return {
        "page_outcome": card.get("page_outcome"),
        "why": str(card.get("why") or "")[:90],
        "page_url": (card.get("feedback") or {}).get("page", {}).get("url", ""),
        "page_state": (card.get("feedback") or {}).get("page", {}).get("state", ""),
        "overlays": (card.get("feedback") or {}).get("overlays"),
        "changes_seq": (card.get("feedback") or {}).get("changes_seq"),
    }


def tl_compact(tl):
    """时间线 digest 关键决策字段。"""
    return {
        "counts": tl.get("counts"),
        "statuses": tl.get("statuses"),
        "api_hits": tl.get("api_hits"),
        "dom_changes": tl.get("dom_changes"),
        "recent_dom": tl.get("recent_dom"),
        "failures": tl.get("failures"),
        "silent_failures": tl.get("silent_failures"),
        "causal_windows": [{"action": w.get("action"), "counts": w.get("counts"),
                            "statuses": w.get("statuses"), "key": w.get("key")}
                           for w in (tl.get("causal_windows") or [])[:2]],
    }


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 6000, "reuse_policy": "never",
                                                        "task_id": "harness-exp"}, timeout=150)
            wid = opened["world_id"]
            print("WORLD", wid, opened.get("url"))

            # ── 动作1: 点击 Pull requests tab ──
            print("\n########## 动作1: 点击 Pull requests tab ##########")
            r = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                       "text": "Pull requests", "max_results": 5}, timeout=45)
            links = [e for e in r.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            if links:
                t0 = asyncio.get_event_loop().time()
                card = await call(session, "world_click", {"world_id": wid, "id": links[0]["id"]}, timeout=90)
                lat = round((asyncio.get_event_loop().time() - t0) * 1000)
                print(f"[点击耗时 {lat}ms]")
                print("A.后果卡:", json.dumps(card_compact(card), ensure_ascii=False))
            await asyncio.sleep(4)
            tl = await call(session, "world_timeline", {"world_id": wid, "since": 0}, timeout=45)
            print("B.时间线:", json.dumps(tl_compact(tl), ensure_ascii=False))

            # ── 动作2: 点击第一个 PR 链接 ──
            print("\n########## 动作2: 点击第一个 PR 链接 ##########")
            r2 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "opened", "max_results": 5}, timeout=45)
            pr_links = [e for e in r2.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            if pr_links:
                t0 = asyncio.get_event_loop().time()
                card2 = await call(session, "world_click", {"world_id": wid, "id": pr_links[0]["id"]}, timeout=90)
                lat2 = round((asyncio.get_event_loop().time() - t0) * 1000)
                print(f"[点击耗时 {lat2}ms]")
                print("A.后果卡:", json.dumps(card_compact(card2), ensure_ascii=False))
            await asyncio.sleep(4)
            tl2 = await call(session, "world_timeline", {"world_id": wid, "since": tl["cursor"]}, timeout=45)
            print("B.时间线:", json.dumps(tl_compact(tl2), ensure_ascii=False))

            # ── 动作3: 触发真实 404(静默失败候选) ──
            print("\n########## 动作3: fetch 真实 404 接口 ##########")
            await call(session, "world_eval", {"world_id": wid, "expression":
                "() => { fetch('https://api.github.com/repos/git/git/nonexistent-endpoint').catch(() => {}); return true; }"}, timeout=20)
            await asyncio.sleep(2)
            tl3 = await call(session, "world_timeline", {"world_id": wid, "since": tl2["cursor"]}, timeout=45)
            print("B.时间线:", json.dumps(tl_compact(tl3), ensure_ascii=False))

            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
