# -*- coding: utf-8 -*-
"""L3 动作证据卡重核:导航竞态下证据完整性 + decision 合理性。

4 个连续动作(GitHub):
  E1 点击 Pull requests tab(导航)
  E2 立即点击 Code tab(竞态导航)
  E3 fill 搜索框(非导航)
  E4 点击 Releases 链接(导航)
每动作后读 world_evidence(evidence_seq 增量):核对 entry 字段完整性、
transition 准确性、runtime_signals、action_evidence.decision 合理性。
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


def ev_compact(entry):
    return {
        "seq": entry.get("evidence_seq"),
        "action": entry.get("action"),
        "verdict": entry.get("verdict"),
        "confidence": entry.get("confidence"),
        "transition": {k: v for k, v in (entry.get("transition") or {}).items()
                       if k in ("url_changed", "new_overlays", "changes_seq_advanced")},
        "before_url": (entry.get("before") or {}).get("url", "")[-40:],
        "after_url": (entry.get("after") or {}).get("url", "")[-40:],
        "after_state": (entry.get("after") or {}).get("state"),
        "after_seq": (entry.get("after") or {}).get("changes_seq"),
        "runtime_signals": entry.get("runtime_signals") or [],
    }


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 8000, "reuse_policy": "never",
                                                        "task_id": "verify-evidence"}, timeout=150)
            wid = opened["world_id"]
            print("WORLD", wid, opened.get("url"))
            ev_cursor = 0

            async def read_evidence(tag):
                nonlocal ev_cursor
                r = await call(session, "world_evidence", {"world_id": wid,
                                                           "since": ev_cursor}, timeout=45)
                items = r.get("evidence", r.get("entries", [])) or []
                ev_cursor = r.get("cursor", ev_cursor)
                print(f"  [{tag}] evidence {len(items)} 条")
                for e in items:
                    print("   ", json.dumps(ev_compact(e), ensure_ascii=False))

            # E1: 导航
            print("\n[E1] 点击 Pull requests tab")
            r = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                       "text": "Pull requests", "max_results": 5}, timeout=45)
            links = [e for e in r.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            t0 = asyncio.get_event_loop().time()
            card = await call(session, "world_click", {"world_id": wid, "id": links[0]["id"]}, timeout=120)
            dt = round((asyncio.get_event_loop().time() - t0) * 1000)
            print(f"  [动作耗时 {dt}ms]")
            ae = card.get("action_evidence")
            if ae:
                print("  action_evidence:", json.dumps({
                    "window_s": ae.get("window_s"), "request_count": ae.get("request_count"),
                    "statuses": [r_.get("status") for r_ in (ae.get("requests") or []) if r_.get("status")][:8],
                    "api": [r_.get("url") for r_ in (ae.get("requests") or []) if r_.get("api")][:3],
                    "decision": ae.get("decision"),
                }, ensure_ascii=False))
            else:
                print("  action_evidence: (缺失!)")
            await asyncio.sleep(2)
            await read_evidence("E1")

            # E2: 竞态导航(立即点击 Code)
            print("\n[E2] 立即点击 Code tab(竞态)")
            r2 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "Code", "max_results": 5}, timeout=45)
            links2 = [e for e in r2.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            t0 = asyncio.get_event_loop().time()
            card2 = await call(session, "world_click", {"world_id": wid, "id": links2[0]["id"]}, timeout=120)
            dt2 = round((asyncio.get_event_loop().time() - t0) * 1000)
            print(f"  [动作耗时 {dt2}ms]")
            ae2 = card2.get("action_evidence")
            if ae2:
                print("  action_evidence:", json.dumps({
                    "window_s": ae2.get("window_s"), "request_count": ae2.get("request_count"),
                    "decision": ae2.get("decision"),
                }, ensure_ascii=False))
            else:
                print("  action_evidence: (缺失!)")
            await asyncio.sleep(2)
            await read_evidence("E2")

            # E3: 点击已激活 tab(无副作用,期望 no-change 证据)
            print("\n[E3] 点击已激活 Code tab(无副作用)")
            r3 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "Code", "max_results": 5}, timeout=45)
            links3 = [e for e in r3.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            t0 = asyncio.get_event_loop().time()
            card3 = await call(session, "world_click", {"world_id": wid, "id": links3[0]["id"]}, timeout=120)
            dt3 = round((asyncio.get_event_loop().time() - t0) * 1000)
            print(f"  [动作耗时 {dt3}ms]")
            ae3 = card3.get("action_evidence")
            print("  action_evidence:", json.dumps({"request_count": (ae3 or {}).get("request_count"),
                                                    "decision": (ae3 or {}).get("decision")},
                                                   ensure_ascii=False) if ae3 else "(缺失!)")
            print("  page_outcome:", card3.get("page_outcome"))
            await asyncio.sleep(1)
            await read_evidence("E3")

            # E4: 导航到 Releases
            print("\n[E4] 点击 Releases 链接")
            r4 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "Releases", "max_results": 5}, timeout=45)
            links4 = [e for e in r4.get("entities", []) if (e.get("semantic") or "").startswith("link")]
            t0 = asyncio.get_event_loop().time()
            card4 = await call(session, "world_click", {"world_id": wid, "id": links4[0]["id"]}, timeout=120)
            dt4 = round((asyncio.get_event_loop().time() - t0) * 1000)
            print(f"  [动作耗时 {dt4}ms]")
            ae4 = card4.get("action_evidence")
            if ae4:
                print("  action_evidence:", json.dumps({
                    "window_s": ae4.get("window_s"), "request_count": ae4.get("request_count"),
                    "statuses": [r_.get("status") for r_ in (ae4.get("requests") or []) if r_.get("status")][:8],
                    "decision": ae4.get("decision"),
                }, ensure_ascii=False))
            else:
                print("  action_evidence: (缺失!)")
            await asyncio.sleep(2)
            await read_evidence("E4")

            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
