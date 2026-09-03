# -*- coding: utf-8 -*-
"""重复动作后，world_guide 只给出已验证的本地路线提示。"""
import asyncio, json, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

async def call(s, n, a):
    r = await asyncio.wait_for(s.call_tool(n, a), timeout=30)
    return json.loads(r.content[0].text)

async def main():
    p = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(p) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            op = await call(s, "world_open", {"url": (FIX / "far_modal.html").as_uri(), "wait_ms": 100})
            wid = op["world_id"]
            for _ in range(2):
                f = await call(s, "world_find", {"world_id": wid, "role": "button", "text": "打开居中弹窗"})
                await call(s, "world_act", {"world_id": wid, "kind": "click", "id": f["matches"][0]["id"]})
                await call(s, "world_act", {"world_id": wid, "kind": "press", "id": f["matches"][0]["id"], "key": "Escape"})
            g = await call(s, "world_guide", {"world_id": wid, "task": "打开居中弹窗"})
            hint = g.get("route_hint") or {}
            assert hint.get("source") == "history" and hint.get("validated") is True, hint
            assert hint.get("steps"), hint
            fp = hint["steps"][0].get("target_fingerprint")
            if fp:
                by_fp = await call(s, "world_find", {"world_id": wid, "fingerprint": fp})
                assert by_fp.get("count") == 1, by_fp
            await call(s, "world_close", {"world_id": wid})
    print("navigation memory 测试通过")

if __name__ == "__main__":
    asyncio.run(main())
