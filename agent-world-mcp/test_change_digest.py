# -*- coding: utf-8 -*-
"""变更可读化验证(world_changes 语义摘要 + 重要性加权):
1. 本地 dyn.html(每 300ms 追加动态项):digest 应归纳为"新增 N 个构件",事件带 importance
2. Google Flights:点击乘客按钮 -> 变更流 digest 应体现"弹窗出现/新增构件"
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
DYN_URI = (Path(__file__).resolve().parent.parent / "test_fixtures" / "dyn.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. 本地动态页:dyn.html 每 300ms 追加一个 <p> ──
            r = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 3000})
            wid = r["world_id"]
            print(f"1. 打开动态页: world_id={wid}, total={r['summary']['total']}")

            r = await call(session, "world_changes", {"world_id": wid, "since": 0})
            events = r.get("events", [])
            digest = r.get("digest", {})
            print(f"   digest.summary: {digest.get('summary')}")
            print(f"   digest.counts : {digest.get('counts')}")
            print(f"   事件数: {len(events)}, 样例:")
            for e in events[:5]:
                print(f"     {e}")
            assert "digest" in r, "world_changes 应返回 digest"
            assert r.get("digest", {}).get("counts", {}).get("add", 0) >= 1 or len(events) > 0, "应有新增事件"
            assert all("importance" in e for e in events), "事件应带 importance"
            # 带语义标签的事件应可归类
            tagged = [e for e in events if e.get("semantic")]
            print(f"   带 semantic 标签的事件: {len(tagged)}/{len(events)}")

            await call(session, "world_close", {"world_id": wid})

            # ── 2. Google Flights:点击乘客按钮 → digest 应反映弹窗 ──
            r = await call(session, "world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 4000})
            wid2 = r["world_id"]
            print(f"2. 打开 GF: world_id={wid2}, total={r['summary']['total']}")

            # 找乘客按钮(名称含 passenger,如 button.1-passenger-change-number-of-passen...)
            rr = await call(session, "world_entities", {"world_id": wid2, "name": "passenger"})
            ents = rr.get("entities", [])
            btn = None
            for e in ents:
                if e.get("interactive") or e.get("semantic") in ("button", "combobox"):
                    btn = e
                    break
            if not btn and ents:
                btn = ents[0]
            if btn:
                print(f"   点击 {btn['id']} ({btn['name']})")
                r = await call(session, "world_click", {"world_id": wid2, "id": btn["id"]})
                effect = r.get("effect")
                print(f"   effect.verdict   : {effect.get('verdict') if effect else None}")
                print(f"   effect.confidence: {effect.get('confidence') if effect else None}")
                print(f"   effect.why       : {effect.get('why') if effect else None}")
                print(f"   region_changed   : {effect.get('region_changed') if effect else None}")
                assert effect, "world_click 应返回 effect"
                assert effect["verdict"] == "effected", f"点击乘客按钮应生效,实际 {effect['verdict']}"
                assert effect["confidence"] == "high", "应有高置信度"
                await asyncio.sleep(1.0)
                r = await call(session, "world_changes", {"world_id": wid2, "since": 0})
                digest = r.get("digest", {})
                print(f"   digest.summary: {digest.get('summary')}")
                print(f"   digest.counts : {digest.get('counts')}")
                print(f"   highlights    : {json.dumps(digest.get('highlights'), ensure_ascii=False)[:300]}")
            else:
                print("   (未找到乘客按钮,跳过点击段)")

            await call(session, "world_close", {"world_id": wid2})
            print("\n✅ 变更可读化验证完成(本地段已断言)")


if __name__ == "__main__":
    asyncio.run(main())
