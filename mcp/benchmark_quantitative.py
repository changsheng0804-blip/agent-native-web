# -*- coding: utf-8 -*-
"""MCP 与原生浏览器的可重复耗时测量（本地夹具，避免网络抖动）。"""
import asyncio, json, sys, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URL = "http://127.0.0.1:8765/far_modal.html"

async def call(session, name, args, timeout=40):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)

async def one(session, policy, browser_mode="isolated"):
    t0 = time.perf_counter()
    opened = await call(session, "world_open", {
        "url": URL, "wait_ms": 0, "ready_policy": policy,
        "browser_mode": browser_mode,
        "stabilize_ms": 10000,
    })
    t1 = time.perf_counter()
    wid = opened["world_id"]
    found = await call(session, "world_find", {
        "world_id": wid, "role": "button", "text": "打开居中弹窗",
    })
    t2 = time.perf_counter()
    target = found["matches"][0]["id"]
    card = await call(session, "world_act", {
        "world_id": wid, "kind": "click", "id": target,
    })
    t3 = time.perf_counter()
    await call(session, "world_close", {"world_id": wid})
    return {
        "policy": policy,
        "open_ms": round((t1-t0)*1000),
        "find_ms": round((t2-t1)*1000),
        "action_confirm_ms": round((t3-t2)*1000),
        "total_ms": round((t3-t0)*1000),
        "page_outcome": card.get("page_outcome"),
        "full_scan": opened.get("readiness", {}).get("full_scan"),
        "scan_revision": opened.get("readiness", {}).get("scan_revision"),
    }

async def one_receipt(session):
    t0 = time.perf_counter()
    opened = await call(session, "world_open", {"url": URL, "wait_ms": 0, "ready_policy": "action"})
    t1 = time.perf_counter()
    wid = opened["world_id"]
    found = await call(session, "world_find", {"world_id": wid, "role": "button", "text": "打开居中弹窗"})
    target = found["matches"][0]["id"]
    t2 = time.perf_counter()
    receipt = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": target, "wait_policy": "receipt"})
    t3 = time.perf_counter()
    final = None
    for _ in range(50):
        final = await call(session, "world_outcome", {"world_id": wid, "action_id": receipt["action_id"]})
        if final.get("page_outcome") != "pending": break
        await asyncio.sleep(0.02)
    t4 = time.perf_counter()
    await call(session, "world_close", {"world_id": wid})
    return {"policy": "receipt", "open_ms": round((t1-t0)*1000), "find_ms": round((t2-t1)*1000),
            "receipt_ms": round((t3-t2)*1000), "final_confirm_ms": round((t4-t3)*1000),
            "total_ms": round((t4-t0)*1000), "page_outcome": (final or {}).get("page_outcome")}

async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as session:
            await session.initialize()
            rows = []
            for policy in ("action", "terrain", "stable"):
                for _ in range(5):
                    rows.append(await one(session, policy))
            pooled_rows = [await one(session, "action", "pooled") for _ in range(5)]
            receipt_rows = [await one_receipt(session) for _ in range(5)]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            for policy in ("action", "terrain", "stable"):
                xs = [x for x in rows if x["policy"] == policy]
                print(policy, {k: round(sum(x[k] for x in xs)/len(xs)) for k in ("open_ms", "find_ms", "action_confirm_ms", "total_ms")})
            print("receipt", {k: round(sum(x[k] for x in receipt_rows)/len(receipt_rows)) for k in ("open_ms", "find_ms", "receipt_ms", "final_confirm_ms", "total_ms")})
            print("pooled-action", {k: round(sum(x[k] for x in pooled_rows)/len(pooled_rows)) for k in ("open_ms", "find_ms", "action_confirm_ms", "total_ms")})

if __name__ == "__main__": asyncio.run(main())
