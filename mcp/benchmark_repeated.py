# -*- coding: utf-8 -*-
"""重复任务：历史路线提示组与关闭提示对照组。"""
import asyncio, json, os, sys, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URL = "http://127.0.0.1:8765/far_modal.html"
MEMORY = Path(__file__).resolve().parent / "memory" / "navigation_graph.json"

async def call(s, name, args):
    r = await asyncio.wait_for(s.call_tool(name, args), timeout=40)
    return json.loads(r.content[0].text)

async def run_group(disable_history=False):
    env = dict(os.environ)
    if disable_history:
        env["AGENT_WORLD_DISABLE_ROUTE_HINT"] = "1"
    else:
        env.pop("AGENT_WORLD_DISABLE_ROUTE_HINT", None)
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    browser_mode = os.environ.get("BENCH_BROWSER_MODE", "isolated")
    rows = []
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            for i in range(4):
                t0 = time.perf_counter()
                opened = await call(s, "world_open", {"url": URL, "wait_ms": 0, "ready_policy": "action", "browser_mode": browser_mode})
                wid = opened["world_id"]
                guide = await call(s, "world_guide", {"world_id": wid, "task": "打开居中弹窗"})
                hint = guide.get("route_hint") or {}
                if hint.get("source") == "history" and hint.get("validated") and hint.get("steps"):
                    fp = hint["steps"][0].get("target_fingerprint")
                    found = await call(s, "world_find", {"world_id": wid, "fingerprint": fp})
                    find_mode = "history-fingerprint"
                else:
                    found = await call(s, "world_find", {"world_id": wid, "role": "button", "text": "打开居中弹窗"})
                    find_mode = "live-text"
                target = found["matches"][0]["id"]
                card = await call(s, "world_act", {"world_id": wid, "kind": "click", "id": target})
                await call(s, "world_close", {"world_id": wid})
                rows.append({"run": i + 1, "route_source": hint.get("source"), "validated": hint.get("validated"),
                             "find_mode": find_mode, "outcome": card.get("page_outcome"),
                             "guide_candidates": len(guide.get("candidates") or []),
                             "elapsed_ms": round((time.perf_counter() - t0) * 1000)})
    return rows

async def main():
    original = MEMORY.read_bytes() if MEMORY.exists() else None
    try:
        MEMORY.unlink(missing_ok=True)
        history = await run_group(False)
        MEMORY.unlink(missing_ok=True)
        control = await run_group(True)
    finally:
        if original is not None:
            MEMORY.parent.mkdir(exist_ok=True)
            MEMORY.write_bytes(original)
        else:
            MEMORY.unlink(missing_ok=True)
    print(json.dumps({"history": history, "control": control}, ensure_ascii=False, indent=2))
    for name, rows in (("history", history), ("control", control)):
        print(name, {"history_hits": sum(r["route_source"] == "history" for r in rows),
                     "fingerprint_finds": sum(r["find_mode"] == "history-fingerprint" for r in rows),
                     "success": sum(r["outcome"] == "progressed" for r in rows),
                     "avg_ms": round(sum(r["elapsed_ms"] for r in rows) / len(rows))})

if __name__ == "__main__": asyncio.run(main())
