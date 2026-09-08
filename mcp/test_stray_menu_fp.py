# -*- coding: utf-8 -*-
"""A5 守护:点失效按钮但页面另处弹出**无关非模态菜单** → 不得报假成功。

背景(核心命题 §2.2 反例⑤):页面发生变化,但这是后台脚本/悬停引发的,不是本次点击。
`_build_click_effect` 与 `_finalize_click_result` 曾把"全页出现任何新 dialog/menu"
都判成 effected(FP 一票否决类别)。修复后:
  - 新增**模态 dialog** 仍可判 effected(远距弹窗是真证据,见 test_global_feedback)
  - 仅新增**非模态 menu** 只能判 changed → page_outcome=uncertain(复核一次),绝不 progressed

夹具:stray_menu.html —— 点击无副作用,800ms 后后台脚本弹出无关 role=menu。
"""
import asyncio, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "stray_menu.html").as_uri()

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


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            d = await call(session, "world_open", {"url": URI, "wait_ms": 400})
            wid = d["world_id"]

            f = await call(session, "world_find", {"world_id": wid, "q": "点我没有任何效果"})
            matches = f.get("matches") or []
            check("找到无副作用按钮", bool(matches))
            if not matches:
                await call(session, "world_close", {"world_id": wid})
                print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
                raise SystemExit(1)

            # 点击(动作窗口内会等到后台菜单弹出)
            card = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": matches[0]["id"]})
            po = card.get("page_outcome")
            eff = card.get("effect") or {}
            ov = (card.get("feedback") or {}).get("overlays") or {}

            check("不得判 progressed(假成功红线)", po != "progressed", f"page_outcome={po}")
            check("verdict 不得为 effected", eff.get("verdict") != "effected", f"verdict={eff.get('verdict')}")
            check("若观察到菜单,应降级为 uncertain", po in ("uncertain", "unchanged"), f"page_outcome={po}")

            # 佐证:卡片应能看到新覆盖层(证明场景确实触发了,而不是没测到)
            new_ov = ov.get("new") or []
            print(f"     观察到的覆盖层变化: {json.dumps(new_ov, ensure_ascii=False)[:160]}")

            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
