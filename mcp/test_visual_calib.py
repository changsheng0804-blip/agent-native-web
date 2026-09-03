# -*- coding: utf-8 -*-
"""P0-2 视觉阈值校准锁死测试(校准 v1)。

依据: docs/视觉阈值校准报告.md。
锁定:
  1. canvas DOM静默重绘 → visual-effected(视觉通道真阳性,不依赖 DOM 侧)
  2. 邻区动画渗入(raw~2.1) → no-change(阈值 5.0 吃掉渗入 FP)
  3. 点击致滚动 → no-change + visual_skipped=scroll-shift(错位帧作废,不判生效)
  4. visual.html 回归:anim visual-effected / static no-change(raw 0)
  5. 阈值常量本身(改值必须同步改本测试与报告)
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = str(HERE / "server.py")
FIX = HERE.parent / "tests" / "fixtures"
CALIB2_URI = (FIX / "visual_calib2.html").as_uri()
VISUAL_URI = (FIX / "visual.html").as_uri()

PASS = 0
FAIL = 0


def _load_threshold():
    spec = importlib.util.spec_from_file_location("wserver", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.VISUAL_RMS_THRESHOLD


THRESHOLD = _load_threshold()


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


async def click_case(session, uri, cid):
    d = await call(session, "world_open", {"url": uri, "wait_ms": 800})
    wid = d["world_id"]
    eid = await css_id(session, wid, cid)
    card = await call(session, "world_click", {"world_id": wid, "id": eid, "visual_evidence": True})
    await call(session, "world_close", {"world_id": wid})
    return card.get("effect", {})


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    print(f"当前阈值 VISUAL_RMS_THRESHOLD={THRESHOLD}")
    check("阈值=5.0(校准v1,改值须同步改测试与报告)", THRESHOLD == 5.0, THRESHOLD)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            print("\n[1] canvas 重绘 → visual-effected")
            eff = await click_case(session, CALIB2_URI, "c-canvas")
            check("canvas判visual-effected", eff.get("verdict") == "visual-effected",
                  eff.get("verdict"))
            check("canvas raw 远高于阈值", (eff.get("visual_diff_raw") or 0) > THRESHOLD * 2,
                  eff.get("visual_diff_raw"))

            print("\n[2] 邻区动画渗入 → no-change")
            eff = await click_case(session, CALIB2_URI, "c-near")
            check("渗入不判生效", eff.get("verdict") == "no-change", eff.get("verdict"))
            check("渗入 raw 被阈值吃掉", (eff.get("visual_diff_raw") or 0) < THRESHOLD,
                  eff.get("visual_diff_raw"))

            print("\n[3] 点击致滚动 → 作废不判生效")
            eff = await click_case(session, CALIB2_URI, "c-far")
            check("滚动不错判生效", eff.get("verdict") != "visual-effected",
                  eff.get("verdict"))
            check("标记scroll-shift作废", eff.get("visual_skipped") == "scroll-shift",
                  eff.get("visual_skipped"))

            print("\n[4] visual.html 回归")
            eff = await click_case(session, VISUAL_URI, "anim")
            check("anim仍visual-effected", eff.get("verdict") == "visual-effected",
                  eff.get("verdict"))
            eff = await click_case(session, VISUAL_URI, "static")
            check("static仍no-change", eff.get("verdict") == "no-change", eff.get("verdict"))
            check("static raw≈0", (eff.get("visual_diff_raw") or 0) < 0.5,
                  eff.get("visual_diff_raw"))

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())
