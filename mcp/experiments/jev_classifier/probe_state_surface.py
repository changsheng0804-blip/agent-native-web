# -*- coding: utf-8 -*-
"""探针:挑战夹具上的运行时输出面(回执字段/状态卡/构件文本),为生产口径 state 拼装取证。

运行:
  uv run --directory agent-native-web python mcp/experiments/jev_classifier/probe_state_surface.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parents[1]
sys.path.insert(0, str(MCP_DIR))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SERVER = str(MCP_DIR / "server.py")
FIX = HERE / "fixtures"


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def probe(session, path):
    d = await call(session, "world_open", {"url": path.as_uri(), "wait_ms": 1200})
    wid = d["world_id"]
    ents = await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 20})
    elid = next((e["id"] for e in ents.get("entities") or [] if "提交" in (e.get("text") or "")), None)
    rc = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": elid})
    print(f"=== {path.name} ===")
    print("receipt.page_outcome =", rc.get("page_outcome"))
    print("receipt.situation    =", json.dumps(rc.get("situation"), ensure_ascii=False)[:260])
    print("receipt.overlays     =", json.dumps(rc.get("overlays"), ensure_ascii=False)[:320])
    st = await call(session, "world_state", {"world_id": wid})
    print("state.frames         =", json.dumps(st.get("frames"), ensure_ascii=False)[:420])
    print("state.dialogs        =", json.dumps(st.get("dialogs"), ensure_ascii=False)[:220])
    print("state.page           =", json.dumps(st.get("page"), ensure_ascii=False)[:220])
    print("state.changed        =", json.dumps(st.get("changed"), ensure_ascii=False)[:160])
    allents = await call(session, "world_entities", {"world_id": wid, "max_results": 40})
    rows = [(e.get("id"), (e.get("name") or "")[:36], (e.get("text") or "")[:60]) for e in (allents.get("entities") or [])]
    print(f"entities({len(rows)})      =", json.dumps(rows[:22], ensure_ascii=False)[:1100])
    await call(session, "world_close", {"world_id": wid})
    print()


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            for name in ("p1_fullscreen_iframe.html", "p4_fullscreen_text.html", "n2_payment_iframe_modal.html"):
                await probe(session, FIX / name)


if __name__ == "__main__":
    asyncio.run(main())
