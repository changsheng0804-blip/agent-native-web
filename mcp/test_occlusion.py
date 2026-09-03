# -*- coding: utf-8 -*-
"""Phase 3 遮挡归因验收:结构化 covered_by/at/action + unchanged 归因。

覆盖:
  1. click 目标被 fixed 遮罩遮挡 → occlusion.covered=True, situation.type=occluded, why 含归因
  2. 移除遮罩后点击 → 无 occlusion 字段,正常 progressed
  3. fill 目标被遮挡 → occlusion 存在(js-setter 兜底路径也带归因)
  4. click_at 坐标命中遮罩 → 坐标模式 occlusion(top 信息 + covered)
  5. 无遮挡动作返回不携带 occlusion(省 token)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FAR_URI = (FIX / "far_modal.html").as_uri()
DYN_URI = (FIX / "dyn.html").as_uri()

PASS = 0
FAIL = 0

BACKDROP_JS = """() => {
    const old = document.getElementById('test-backdrop');
    if (old) old.remove();
    const d = document.createElement('div');
    d.id = 'test-backdrop';
    d.className = 'modal-backdrop';
    d.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10000';
    document.body.appendChild(d);
    return true;
}"""
REMOVE_BACKDROP_JS = """() => {
    const old = document.getElementById('test-backdrop');
    if (old) old.remove();
    return true;
}"""


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def find_one(session, world_id, interactive=True, **filters):
    data = await call(session, "world_entities", {"world_id": world_id, **filters, "max_results": 10})
    entities = data.get("entities", [])
    assert entities, f"没有找到目标: {filters}"
    ent = next((e for e in entities if e.get("interactive")), entities[0]) if interactive else entities[0]
    return ent


async def open_world(session, uri):
    d = await call(session, "world_open", {"url": uri, "wait_ms": 800})
    return d["world_id"]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. far_modal:注入遮罩 → click 被挡 → occluded 归因 ──
            print("\n[1] click 被 fixed 遮罩遮挡 → occlusion + situation.type=occluded")
            wid = await open_world(session, FAR_URI)
            btn = await find_one(session, wid, role="button", text="打开居中弹窗")
            await call(session, "world_eval", {"world_id": wid, "expression": BACKDROP_JS})
            # 等遮罩进入世界模型(before 基线已含遮罩,点击后才不会误判"新弹窗")
            await call(session, "world_eval", {"world_id": wid, "expression": "() => { agentWorld._runtime.refreshStatus(); return true; }"})
            await asyncio.sleep(0.5)
            card = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn["id"]})
            check("page_outcome=unchanged", card["page_outcome"] == "unchanged", card.get("why"))
            check("situation.type=occluded", card["situation"]["type"] == "occluded", str(card["situation"]))
            occ = card.get("occlusion") or {}
            check("occlusion.covered=True", occ.get("covered") is True, str(occ))
            check("covered_by.id=test-backdrop", (occ.get("covered_by") or {}).get("id") == "test-backdrop", str(occ.get("covered_by")))
            check("at 坐标存在", isinstance(occ.get("at"), list) and len(occ["at"]) == 2, str(occ.get("at")))
            check("action 建议存在", bool(occ.get("action")), str(occ.get("action")))
            check("why 含遮挡归因", "遮挡" in card.get("why", ""), card.get("why"))

            # ── 2. 移除遮罩 → 正常 progressed,无 occlusion ──
            print("\n[2] 移除遮罩后 click → progressed,无 occlusion")
            await call(session, "world_eval", {"world_id": wid, "expression": REMOVE_BACKDROP_JS})
            card2 = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn["id"]})
            check("page_outcome=progressed", card2["page_outcome"] == "progressed", card2.get("why"))
            check("无 occlusion 字段", "occlusion" not in card2, str(card2.get("occlusion")))
            check("situation 非 occluded", card2["situation"]["type"] != "occluded", str(card2["situation"]))
            await call(session, "world_close", {"world_id": wid})

            # ── 3. dyn:fill 被遮挡 → occlusion 存在 ──
            print("\n[3] fill 目标被遮挡 → occlusion")
            wid = await open_world(session, DYN_URI)
            await call(session, "world_eval", {"world_id": wid, "expression": BACKDROP_JS})
            fu = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
            uid = next((m["id"] for m in fu["matches"] if m.get("interactive")), None)
            assert uid, "用户名输入框未找到"
            card3 = await call(session, "world_act", {"world_id": wid, "kind": "fill", "id": uid, "text": "alice"})
            occ3 = card3.get("occlusion") or {}
            check("fill occlusion.covered=True", occ3.get("covered") is True, str(occ3))
            check("fill covered_by.id=test-backdrop", (occ3.get("covered_by") or {}).get("id") == "test-backdrop", str(occ3.get("covered_by")))
            await call(session, "world_eval", {"world_id": wid, "expression": REMOVE_BACKDROP_JS})
            await call(session, "world_close", {"world_id": wid})

            # ── 4. far_modal:click_at 坐标命中遮罩 → 坐标模式 occlusion ──
            print("\n[4] click_at 坐标命中遮罩 → 坐标模式 occlusion")
            wid = await open_world(session, FAR_URI)
            await call(session, "world_eval", {"world_id": wid, "expression": BACKDROP_JS})
            r = await call(session, "world_eval", {"world_id": wid, "expression": """() => {
                const b = document.getElementById('corner-btn').getBoundingClientRect();
                return JSON.stringify({ x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) });
            }"""})
            pt = json.loads(r.get("result") or "{}")
            if isinstance(pt, str):
                pt = json.loads(pt)
            card4 = await call(session, "world_click_at", {"world_id": wid, "x": pt["x"], "y": pt["y"]})
            occ4 = card4.get("occlusion") or {}
            check("click_at occlusion.covered=True", occ4.get("covered") is True, str(occ4))
            check("click_at covered_by 含遮罩 class", "backdrop" in ((occ4.get("covered_by") or {}).get("cls") or ""), str(occ4.get("covered_by")))
            await call(session, "world_eval", {"world_id": wid, "expression": REMOVE_BACKDROP_JS})
            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())