# -*- coding: utf-8 -*-
"""L2 样式层验收:WAAPI 变色(DOM 全哑火)→ style-diff 结构化生效;静态 → no-change。

依据:视觉梯子 L2 层。要求:
  - animate 判 visual-effected,visual_path=style-diff,
    style_changes 含 backgroundColor 蓝→红,不依赖像素分
  - static 判 no-change
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = str(HERE / "server.py")
URI = (HERE.parent / "tests" / "fixtures" / "visual_style.html").as_uri()

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def css_id(session, wid, cid):
    r = await call(session, "world_eval", {"world_id": wid, "expression": """() => {
        const el = document.getElementById(%s);
        if (!el) return null;
        for (const e of agentWorld._runtime.world.elements.values()) { if (e._el === el) return e.id; }
        return JSON.stringify(e.id);
    }""" % json.dumps(cid)})
    res = r.get("result")
    while isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            break
    assert res, f"找不到 #{cid}"
    return res


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            print("\n[1] WAAPI 变色 → style-diff 结构化生效")
            d = await call(session, "world_open", {"url": URI, "wait_ms": 800})
            wid = d["world_id"]
            eid = await css_id(session, wid, "c-anim")
            card = await call(session, "world_click",
                              {"world_id": wid, "id": eid, "visual_evidence": True})
            eff = card.get("effect", {})
            check("判visual-effected", eff.get("verdict") == "visual-effected",
                  eff.get("verdict"))
            check("走style-diff不走像素", eff.get("visual_path") == "style-diff",
                  eff.get("visual_path"))
            sc = eff.get("style_changes") or []
            check("style_changes非空", bool(sc), sc)
            bg = next((c for c in sc if c.get("prop") == "backgroundColor"), None)
            check("含背景色蓝→红", bool(bg and "52" in bg.get("before", "")
                                        and "231" in bg.get("after", "")), bg)
            check("整单progressed", card.get("page_outcome") == "progressed",
                  card.get("page_outcome"))
            await call(session, "world_close", {"world_id": wid})

            print("\n[2] 静态对照 → no-change")
            d = await call(session, "world_open", {"url": URI, "wait_ms": 800})
            wid = d["world_id"]
            eid = await css_id(session, wid, "c-static")
            card = await call(session, "world_click",
                              {"world_id": wid, "id": eid, "visual_evidence": True})
            eff = card.get("effect", {})
            check("静态no-change", eff.get("verdict") == "no-change", eff.get("verdict"))
            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())
