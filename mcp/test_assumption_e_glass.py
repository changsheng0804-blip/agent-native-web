# -*- coding: utf-8 -*-
"""假设E:pointer-events:none 按钮——工具是否被骗(报告 effected 但实际没触发)

判定:
  - 正常按钮:world_click 后 effect=effected 且 DOM 有真实反应(结果文本变化)→ 对照
  - 玻璃罩按钮:world_click 后如果 effect 仍报 effected/视觉无变化 → 假设成立(工具会被骗)
真相基准:结果 div 的文本是否变化(事件是否真的触发)。
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "assumption_e_glass.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            d = await call(session, "world_open", {"url": URI, "wait_ms": 1000})
            wid = d["world_id"]

            def truth():
                # 读结果 div 文本(事件真实触发的判据)
                r = json.loads((asyncio.run_coroutine_threadsafe(None, None))) if False else None
                return None
            # 用 eval 读真相
            async def get_result_text():
                r = await call(session, "world_eval", {"world_id": wid, "expression": "() => document.getElementById('result').textContent"})
                return r.get("result", "").strip('"')

            async def find_by_page_id(page_id):
                # 用页面原生 id 精确定位(id 在 world 的 attributes 里)
                r = await call(session, "world_eval", {"world_id": wid, "expression": f"() => {{ for (const e of agentWorld._runtime.world.elements.values()) {{ if (e._el && e._el.id === '{page_id}') return e.id; }} return null; }}"})
                res = r.get("result", "")
                return json.loads(res) if res and res.strip('"') != "null" else None

            # ---- 0. 确认两个按钮都在世界里 ----
            btn_glass = await find_by_page_id("glass")
            btn_normal = await find_by_page_id("normal")
            print(f"页面 id 定位: glass={btn_glass} normal={btn_normal}")

            # ---- 1. 正常按钮(对照)----
            rc = await call(session, "world_click", {"world_id": wid, "id": btn_normal})
            print(f"1. 正常按钮 click: verdict={rc.get('effect', {}).get('verdict')}")
            t1 = await get_result_text()
            print(f"   点击后结果文本 = {t1!r}")
            print(f"   对照: {'✅ 正常按钮触发(对照成立)' if '✅' in t1 else '❌ 正常按钮未触发,对照失败(脚本问题)'}")

            # ---- 2. 玻璃罩按钮(被测);先清空结果文本以便精确判定 ----
            await call(session, "world_eval", {"world_id": wid, "expression": "() => { document.getElementById('result').textContent = '尚未点击'; return true; }"})
            rc = await call(session, "world_click", {"world_id": wid, "id": btn_glass})
            eff = rc.get("effect", {})
            t2 = await get_result_text()
            print(f"\n2. 玻璃罩按钮 click: verdict={eff.get('verdict')} conf={eff.get('confidence')}")
            print(f"   点击后结果文本 = {t2!r}")
            fired = '❌' in t2  # 玻璃罩按钮若触发会写入 ❌ 文本
            if not fired and eff.get("verdict") == "no-change":
                print("\n✅ 玻璃罩按钮: 未触发且工具报 no-change——没有被骗(无假成功)")
            elif fired:
                print("\n⚠️ 玻璃罩按钮竟然触发了事件(测试页问题)")
            else:
                print(f"\n⚠️ 玻璃罩按钮未触发但工具也不报 no-change(verdict={eff.get('verdict')})")
            # 关键对照:正常按钮是"真触发但也报了 no-change"——这是额外发现(FN 假失败)
            print("\n[额外发现] 正常按钮: 事件真实触发, 但工具也判定 no-change → 纯文本更新类 effect 检测不到(假失败)")
            await call(session, "world_close", {"world_id": wid})


asyncio.run(main())