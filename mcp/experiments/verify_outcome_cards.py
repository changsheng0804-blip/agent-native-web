# -*- coding: utf-8 -*-
"""后果卡五态判定重核:真实 harness 逐动作核对 page_outcome 准确性。

动作序列(GitHub git/git):
  A1 点击 Pull requests tab       → 期望 progressed(导航)
  A2 立即点击 Code tab(竞态)      → 期望 progressed/uncertain(导航竞态)
  A3 点击当前已激活 tab           → 期望 unchanged(无副作用)
  A4 fill 搜索框                  → 期望 progressed(值变化)
  A5 导航到不存在页面             → 期望 progressed(URL变,但时间线应见 404)
每步输出: 后果卡判定字段 + 时间线增量(交叉验证真相)。
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
    return {
        "page_outcome": card.get("page_outcome"),
        "situation": card.get("situation"),
        "confidence": card.get("confidence"),
        "why": str(card.get("why") or "")[:110],
        "url_changed": (card.get("feedback") or {}).get("page", {}).get("url_changed"),
        "state_after": (card.get("feedback") or {}).get("page", {}).get("state"),
        "changes_seq": (card.get("feedback") or {}).get("changes_seq"),
        "new_overlays": (card.get("feedback") or {}).get("overlays", {}).get("new"),
        "effect_verdict": (card.get("effect") or {}).get("verdict"),
        "net_err": len((card.get("feedback") or {}).get("net_errors", []) or []),
    }


def tl_compact(tl):
    return {
        "counts": tl.get("counts"),
        "statuses": tl.get("statuses"),
        "silent": tl.get("silent_failures"),
        "dom": tl.get("dom_changes"),
    }


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 8000, "reuse_policy": "never",
                                                        "task_id": "verify-outcome"}, timeout=150)
            wid = opened["world_id"]
            print("WORLD", wid, opened.get("url"))
            cursor = 0

            async def tl_since():
                nonlocal cursor
                tl = await call(session, "world_timeline", {"world_id": wid, "since": cursor}, timeout=45)
                cursor = tl.get("cursor", cursor)
                return tl

            # A1: 点击 Pull requests tab
            print("\n[A1] 点击 Pull requests tab(导航)")
            r = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                       "text": "Pull requests", "max_results": 5}, timeout=45)
            links = [e for e in r.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            if links:
                t0 = asyncio.get_event_loop().time()
                card = await call(session, "world_click", {"world_id": wid, "id": links[0]["id"]}, timeout=120)
                print(f"  [耗时 {round((asyncio.get_event_loop().time()-t0)*1000)}ms]")
                print("  判定:", json.dumps(card_compact(card), ensure_ascii=False))
            await asyncio.sleep(3)
            print("  时间线:", json.dumps(tl_compact(await tl_since()), ensure_ascii=False))

            # A2: 立即点击 Code tab(导航竞态)
            print("\n[A2] 立即点击 Code tab(竞态)")
            r2 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "Code", "max_results": 5}, timeout=45)
            links2 = [e for e in r2.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            if links2:
                t0 = asyncio.get_event_loop().time()
                card2 = await call(session, "world_click", {"world_id": wid, "id": links2[0]["id"]}, timeout=120)
                print(f"  [耗时 {round((asyncio.get_event_loop().time()-t0)*1000)}ms]")
                print("  判定:", json.dumps(card_compact(card2), ensure_ascii=False))
            await asyncio.sleep(3)
            print("  时间线:", json.dumps(tl_compact(await tl_since()), ensure_ascii=False))

            # A3: 点击当前已激活 tab(Pull requests)→ 期望无副作用
            print("\n[A3] 点击当前已激活 tab(Pull requests)")
            r3 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "Pull requests", "max_results": 5}, timeout=45)
            links3 = [e for e in r3.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            if links3:
                t0 = asyncio.get_event_loop().time()
                card3 = await call(session, "world_click", {"world_id": wid, "id": links3[0]["id"]}, timeout=120)
                print(f"  [耗时 {round((asyncio.get_event_loop().time()-t0)*1000)}ms]")
                print("  判定:", json.dumps(card_compact(card3), ensure_ascii=False))
            await asyncio.sleep(2)
            print("  时间线:", json.dumps(tl_compact(await tl_since()), ensure_ascii=False))

            # A4: fill 搜索框
            print("\n[A4] fill 搜索框")
            r4 = await call(session, "world_entities", {"world_id": wid, "role": "input",
                                                        "max_results": 5}, timeout=45)
            inputs = [e for e in r4.get("entities", []) if (e.get("semantic") or "").startswith("input")]
            if inputs:
                t0 = asyncio.get_event_loop().time()
                card4 = await call(session, "world_fill", {"world_id": wid, "id": inputs[0]["id"],
                                                           "text": "repo:git/git"}, timeout=60)
                print(f"  [耗时 {round((asyncio.get_event_loop().time()-t0)*1000)}ms]")
                print("  判定:", json.dumps(card_compact(card4), ensure_ascii=False))
            await asyncio.sleep(2)
            print("  时间线:", json.dumps(tl_compact(await tl_since()), ensure_ascii=False))

            # A5: 导航到不存在页面(404 内容页)
            print("\n[A5] world_navigate 到不存在页面")
            t0 = asyncio.get_event_loop().time()
            card5 = await call(session, "world_navigate", {"world_id": wid,
                                                           "url": "https://github.com/git/git/nonexistent-page-xyz",
                                                           "wait_ms": 4000}, timeout=120)
            print(f"  [耗时 {round((asyncio.get_event_loop().time()-t0)*1000)}ms]")
            print("  判定:", json.dumps(card_compact(card5), ensure_ascii=False))
            await asyncio.sleep(2)
            print("  时间线:", json.dumps(tl_compact(await tl_since()), ensure_ascii=False))

            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
