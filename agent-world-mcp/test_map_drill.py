# -*- coding: utf-8 -*-
"""区域钻取验证(world_entities bounds 空间过滤):
链路:world_map 拿某区 bounds → world_entities({bounds, role}) 查区内构件 → world_entity 详图
本地 tabs.html:tablist 区 bounds 内应能查到 tab 按钮
真站 GitHub:main 区 bounds 内按 role 过滤,应能查到该区按钮/链接,且不在区内的查不到
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
TABS_URI = (Path(__file__).resolve().parent.parent / "test_fixtures" / "tabs.html").as_uri()


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. 本地 tabs.html:tablist 区钻取 ──
            r = await call(session, "world_open", {"url": TABS_URI, "wait_ms": 1200})
            wid = r["world_id"]
            m = await call(session, "world_map", {"world_id": wid})
            tab_region = next((x for x in m.get("regions", []) if x.get("region", {}).get("semantic") == "tablist"), None)
            assert tab_region, "应找到 tablist 区域"
            b = tab_region["region"]["bounds"]
            print(f"1. tablist 区 bounds={b}")
            # 区内查 tab 按钮
            rr = await call(session, "world_entities", {"world_id": wid, "bounds": b, "role": "tab"})
            tabs = rr.get("entities", [])
            print(f"   区内 role=tab → {len(tabs)} 条: {[e['name'] for e in tabs]}")
            assert len(tabs) == 3, "tablist 区应有 3 个 tab 按钮"
            # 钻取:取第一个 tab 详图
            ent = await call(session, "world_entity", {"world_id": wid, "id": tabs[0]["id"]})
            print(f"   钻取详图 {tabs[0]['id']}: semantic={ent.get('semantic')} bounds={ent.get('bounds')}")
            assert ent and ent.get("bounds"), "区内构件应可 world_entity 详图"
            await call(session, "world_close", {"world_id": wid})

            # ── 2. 真站 GitHub:main 区 bounds 钻取 ──
            r = await call(session, "world_open", {"url": "https://github.com/git/git", "wait_ms": 4000})
            wid2 = r["world_id"]
            m2 = await call(session, "world_map", {"world_id": wid2})
            main_region = next((x for x in m2.get("regions", []) if x.get("region", {}).get("semantic") == "main"), None)
            assert main_region, "GitHub 应找到 main 区域"
            mb = main_region["region"]["bounds"]
            print(f"2. GitHub main 区 bounds={mb}")
            # 区内查 button
            rr = await call(session, "world_entities", {"world_id": wid2, "bounds": mb, "role": "button", "max_results": 5})
            buttons = rr.get("entities", [])
            print(f"   main 区内 role=button → {rr.get('count')} 条(截取5): {[e['name'] for e in buttons]}")
            assert len(buttons) >= 1, "main 区应能查到按钮"
            # 区内查 link
            rr = await call(session, "world_entities", {"world_id": wid2, "bounds": mb, "role": "link", "max_results": 5})
            print(f"   main 区内 role=link → {rr.get('count')} 条(截取5)")
            # 空间过滤正确性:main 区 bounds 很小(如 y=72 h=5683 很大,但取一个窄条验证排除)
            # 用一个极小的矩形(页面左上角 10x10),应查不到任何构件
            tiny = {"x": 0, "y": 0, "w": 10, "h": 10}
            rr3 = await call(session, "world_entities", {"world_id": wid2, "bounds": tiny})
            print(f"   极小矩形(0,0,10,10) → {rr3.get('count')} 条(应为 0,验证空间过滤生效)")
            assert rr3.get("count") == 0, "极小矩形应查不到构件(空间过滤应生效)"
            await call(session, "world_close", {"world_id": wid2})

            print("\n✅ 区域钻取验证通过:world_map bounds → world_entities 空间过滤 → world_entity 详图,链路闭环")


if __name__ == "__main__":
    asyncio.run(main())
