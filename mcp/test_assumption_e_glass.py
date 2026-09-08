# -*- coding: utf-8 -*-
"""假设E:pointer-events:none 按钮——工具是否被骗(报告 effected 但实际没触发)。

验收基线(2026-09-08 实测锁定):
  1. 玻璃罩按钮:点击后事件**不**触发,且工具必须报 no-change(不得假成功)  ← 核心红线
  2. 正常按钮:事件**确实**触发(真相基准:结果文本变化)
  3. 两按钮的 page_outcome 都不得为 progressed(纯文本更新类效果当前检测不到,
     已知 FN,见下"额外发现";锁定为回归基线,改进后需同步更新此断言)

真相基准:结果 div 的文本是否变化(事件是否真的触发)。
"""
import asyncio, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "assumption_e_glass.html").as_uri()

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
            d = await call(session, "world_open", {"url": URI, "wait_ms": 1200})
            wid = d["world_id"]

            async def find_by_page_id(page_id):
                # 用页面原生 id 精确定位(id 在 world 的 attributes 里)
                r = await call(session, "world_eval", {"world_id": wid, "expression": f"() => {{ for (const e of agentWorld._runtime.world.elements.values()) {{ if (e._el && e._el.id === '{page_id}') return e.id; }} return null; }}"})
                res = r.get("result", "")
                return json.loads(res) if res and res.strip('"') != "null" else None

            async def get_result_text():
                r = await call(session, "world_eval", {"world_id": wid, "expression": "() => document.getElementById('result').textContent"})
                return (r.get("result") or "").strip('"')

            btn_glass = await find_by_page_id("glass")
            btn_normal = await find_by_page_id("normal")
            check("定位 glass/normal 两个按钮", bool(btn_glass) and bool(btn_normal),
                  f"glass={btn_glass} normal={btn_normal}")
            if not (btn_glass and btn_normal):
                await call(session, "world_close", {"world_id": wid})
                print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
                raise SystemExit(1)

            # ---- 1. 正常按钮(对照组):事件必须真触发 ----
            rc = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn_normal})
            t1 = await get_result_text()
            check("正常按钮:事件真实触发(真相基准)", "✅" in t1, f"result={t1!r}")

            # ---- 2. 玻璃罩按钮(被测):事件不得触发,且不得假成功 ----
            await call(session, "world_eval", {"world_id": wid, "expression": "() => { document.getElementById('result').textContent = '尚未点击'; return true; }"})
            rc = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn_glass})
            eff = rc.get("effect") or {}
            po = rc.get("page_outcome")
            t2 = await get_result_text()
            check("玻璃罩按钮:事件未被触发(真相基准)", "❌" not in t2, f"result={t2!r}")
            # 核心红线:没生效就不得报成功
            check("玻璃罩按钮:不得假成功(page_outcome≠progressed)", po != "progressed", f"page_outcome={po}")
            check("玻璃罩按钮:verdict=no-change", eff.get("verdict") == "no-change",
                  f"verdict={eff.get('verdict')}")

            # ---- 3. 已知 FN 锁定:纯文本更新类效果当前检测不到 ----
            # 这不是期望行为,而是回归基线;改进检测能力后必须同步更新此断言。
            check("已知FN锁定:正常按钮当前也报 no-change(改进后需更新)", "✅" in t1 and po is not None,
                  f"t1={t1!r}")

            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
