# -*- coding: utf-8 -*-
"""稳定指纹(第二 ID)验证——B 方案:同站多次进出的"认路记忆"
1. 打开本地 tabs.html → 取 tab-b 的 fingerprint(稳定指纹)
2. 关闭世界 → 重新打开同一页面(全新世界、全新强 ID)
3. 用 fingerprint 查询 world_entities → 应命中同名的 tab-b(拿到当次新强 ID)
4. 验证:两次打开的 fingerprint 完全一致(可重算、不落盘)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
TABS_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tabs.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 第一次进站:取 tab-b 按钮的 fingerprint ──
            r = await call(session, "world_open", {"url": TABS_URI, "wait_ms": 1200})
            wid1 = r["world_id"]
            print(f"1. 第一次进站: world_id={wid1}")
            rr = await call(session, "world_entities", {"world_id": wid1, "role": "tab", "name": "tab-b"})
            ents = rr.get("entities", [])
            assert ents, "未找到 tab-b 按钮"
            tab1 = ents[0]
            fp = tab1.get("fingerprint")
            print(f"   tab-b 第一次: id={tab1['id']} name={tab1['name']} fingerprint={fp}")
            assert fp, "world_entities 应返回 fingerprint"
            assert "id=tab-b" in fp, "指纹应含稳定属性 id=tab-b"
            await call(session, "world_close", {"world_id": wid1})

            # ── 第二次进站(全新世界):用 fingerprint 认路 ──
            r = await call(session, "world_open", {"url": TABS_URI, "wait_ms": 1200})
            wid2 = r["world_id"]
            print(f"2. 第二次进站: world_id={wid2}(全新强 ID)")
            assert wid2 != wid1, "两次 world_open 应是不同世界"

            rr = await call(session, "world_entities", {"world_id": wid2, "fingerprint": fp})
            hit = rr.get("entities", [])
            print(f"   按 fingerprint 查询 → 命中 {len(hit)} 条")
            assert len(hit) == 1, f"fingerprint 应精确命中 1 条,实际 {len(hit)}"
            tab2 = hit[0]
            print(f"   tab-b 第二次: id={tab2['id']} name={tab2['name']} fingerprint={tab2['fingerprint']}")
            assert tab2["fingerprint"] == fp, "两次 fingerprint 应完全一致(可重算、不落盘)"
            assert tab2["name"] == tab1["name"], "命中同名元素(认对了路)"
            assert tab2["semantic"] == "tab", "命中的应是 tab 按钮(非容器)"
            # 用当次新强 ID 可正常操作(管线闭环)
            r = await call(session, "world_click", {"world_id": wid2, "id": tab2["id"]})
            effect = r.get("effect")
            print(f"   点击命中元素: verdict={effect.get('verdict') if effect else None}(当次强 ID 可用)")
            assert effect and effect["verdict"] == "effected", "点击 tab-b 应生效(aria-selected 翻转)"
            await call(session, "world_close", {"world_id": wid2})

            # ── 负例:不存在的指纹应 0 命中 ──
            r = await call(session, "world_open", {"url": TABS_URI, "wait_ms": 1200})
            wid3 = r["world_id"]
            rr = await call(session, "world_entities", {"world_id": wid3, "fingerprint": "no-such|element|fp"})
            assert len(rr.get("entities", [])) == 0, "不存在指纹应 0 命中"
            print("3. 负例:不存在的指纹 0 命中 ✅")
            await call(session, "world_close", {"world_id": wid3})

            print("\n✅ 稳定指纹验证通过:同站两次进站 fingerprint 一致,按指纹一步定位(认路记忆有效)")


if __name__ == "__main__":
    asyncio.run(main())
