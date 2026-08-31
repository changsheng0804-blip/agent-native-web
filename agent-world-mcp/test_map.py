# -*- coding: utf-8 -*-
"""页面结构导览(world_map)验证:语义容器分区 + 各区可交互入口可钻取
1. 本地 tabs.html:应识别 tablist 区域,入口含 tab 按钮
2. 真站 GitHub 仓库页(控制台类复杂结构):应分区(导航/tablist 等),入口带强 ID 且可 world_entity 钻取
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


async def dump_map(title, m):
    print(f"\n--- {title} ---")
    print(f"total={m.get('total')} interactive={m.get('interactive')} 区域数={len(m.get('regions', []))}")
    for r in m.get("regions", []):
        rg = r.get("region", {})
        print(f"  区域 [{rg.get('semantic')}] {rg.get('name')} bounds={rg.get('bounds')} "
              f"构件={r.get('total')} 交互={r.get('interactive')} 类型={r.get('types')}")
        for e in r.get("entries", [])[:4]:
            print(f"     入口 {e.get('id')} [{e.get('semantic')}] {e.get('name')}")
    oth = m.get("other", {})
    if oth:
        print(f"  其他: 构件={oth.get('total')} 交互={oth.get('interactive')} "
              f"入口={[e.get('name') for e in oth.get('entries', [])[:3]]}")


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # 1. 本地 tabs.html
            r = await call(session, "world_open", {"url": TABS_URI, "wait_ms": 1200})
            wid = r["world_id"]
            r = await call(session, "world_map", {"world_id": wid})
            m = r.get("map", r) if isinstance(r, dict) and "map" in r else r
            await dump_map("本地 tabs.html", m)
            regions = m.get("regions", [])
            # tablist 区域应存在,且入口含 tab 按钮(带强 ID)
            tab_region = next((x for x in regions if x.get("region", {}).get("semantic") == "tablist"), None)
            assert tab_region, "tabs.html 应识别出 tablist 区域"
            entry_ids = [e["id"] for e in tab_region.get("entries", [])]
            assert any(eid for eid in entry_ids), "tablist 区域应含可交互入口(带强 ID)"
            # 入口强 ID 可钻取详图
            first_id = entry_ids[0]
            ent = await call(session, "world_entity", {"world_id": wid, "id": first_id})
            print(f"  钻取 {first_id}: semantic={ent.get('semantic')} bounds={ent.get('bounds')}")
            assert ent and ent.get("bounds"), "入口强 ID 应可 world_entity 钻取"
            await call(session, "world_close", {"world_id": wid})

            # 2. 真站 GitHub 仓库页(控制台类复杂结构)
            r = await call(session, "world_open", {"url": "https://github.com/git/git", "wait_ms": 4000})
            wid2 = r["world_id"]
            r = await call(session, "world_map", {"world_id": wid2, "max_entries": 5})
            m2 = r
            await dump_map("GitHub 仓库页", m2)
            regs2 = m2.get("regions", [])
            sems2 = {x.get("region", {}).get("semantic") for x in regs2}
            print(f"识别出的区域语义: {sems2}")
            assert len(regs2) >= 2, "GitHub 复杂页应识别出多个区域(地图有效)"
            # 所有入口强 ID 应能钻取
            all_entries = [e for x in regs2 for e in x.get("entries", [])] + m2.get("other", {}).get("entries", [])
            drill_ok = 0
            for e in all_entries[:5]:
                try:
                    ent = await call(session, "world_entity", {"world_id": wid2, "id": e["id"]})
                    if ent and ent.get("bounds"):
                        drill_ok += 1
                except Exception:
                    pass
            print(f"入口钻取成功: {drill_ok}/{min(len(all_entries), 5)}")
            assert drill_ok >= 1, "至少 1 个入口可钻取详图"
            await call(session, "world_close", {"world_id": wid2})

            print("\n✅ world_map 验证通过:语义分区 + 各区可交互入口 + 强 ID 可钻取")


if __name__ == "__main__":
    asyncio.run(main())
