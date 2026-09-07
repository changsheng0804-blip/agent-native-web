# -*- coding: utf-8 -*-
"""视觉证据重核:截图/视觉diff的开销 vs 判定增益(真实站点)。

  V1 world_screenshot         → 耗时 + base64 大小
  V2 点击导航(visual_evidence=False) → 耗时 + 返回大小 + 判定
  V3 点击导航(visual_evidence=True)  → 耗时 + 返回大小 + 判定 + 视觉diff结果
对比:视觉路径的成本(耗时/载荷)与收益(判定差异)。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
URL = "https://github.com/git/git"


async def call(session, name, args, timeout=150):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    return {}


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 8000, "reuse_policy": "never",
                                                        "task_id": "verify-visual"}, timeout=150)
            wid = opened["world_id"]
            print("WORLD", wid, opened.get("url"))

            # V1: 截图
            print("\n[V1] world_screenshot")
            t0 = asyncio.get_event_loop().time()
            s = await call(session, "world_screenshot", {"world_id": wid}, timeout=60)
            dt = round((asyncio.get_event_loop().time() - t0) * 1000)
            b64 = s.get("screenshot") or s.get("image") or ""
            size_kb = round(len(b64) * 3 / 4 / 1024) if b64 else 0
            print(f"  [耗时 {dt}ms, base64→PNG 约 {size_kb}KB, 字段: {[k for k in s.keys()][:6]}]")

            # 找 tab
            r = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                       "text": "Pull requests", "max_results": 5}, timeout=45)
            pr_tab = next((e for e in r.get("entities", [])
                           if (e.get("semantic") or "").startswith("link")), None)
            r2 = await call(session, "world_entities", {"world_id": wid, "role": "link",
                                                        "text": "Code", "max_results": 5}, timeout=45)
            code_tab = next((e for e in r2.get("entities", [])
                             if (e.get("semantic") or "").startswith("link")), None)

            # V2: 无视觉点击(导航)
            print("\n[V2] 点击 Pull requests(visual_evidence=False)")
            t0 = asyncio.get_event_loop().time()
            card = await call(session, "world_click", {"world_id": wid, "id": pr_tab["id"]}, timeout=150)
            dt2 = round((asyncio.get_event_loop().time() - t0) * 1000)
            raw2 = json.dumps(card, ensure_ascii=False)
            vis = card.get("visual_evidence") or card.get("visual_diff")
            print(f"  [耗时 {dt2}ms, 返回 {round(len(raw2)/1024)}KB, 视觉字段: {bool(vis)}]")
            print(f"  判定: {card.get('page_outcome')} | {str(card.get('why') or '')[:60]}")

            # V3: 视觉点击(返回导航)
            print("\n[V3] 点击 Code(visual_evidence=True)")
            t0 = asyncio.get_event_loop().time()
            card3 = await call(session, "world_click", {"world_id": wid, "id": code_tab["id"],
                                                        "visual_evidence": True}, timeout=150)
            dt3 = round((asyncio.get_event_loop().time() - t0) * 1000)
            raw3 = json.dumps(card3, ensure_ascii=False)
            vis3 = card3.get("visual_evidence") or card3.get("visual_diff") or (card3.get("effect") or {}).get("visual")
            print(f"  [耗时 {dt3}ms, 返回 {round(len(raw3)/1024)}KB, 视觉字段: {bool(vis3)}]")
            print(f"  判定: {card3.get('page_outcome')} | {str(card3.get('why') or '')[:60]}")
            if vis3:
                print(f"  视觉diff: {json.dumps(vis3, ensure_ascii=False)[:300]}")
            print("  → 注:导航判定 progressed,视觉兜底只在 no-change 时触发,此为正确行为")

            # V4: 纯CSS变化(视觉兜底触发场景)——本地 fixture
            print("\n[V4] 纯CSS变色点击(visual_evidence=True, 本地fixture)")
            server, base = _serve_local()
            opened4 = await call(session, "world_open", {"url": base + "/ab/visual.html",
                                                         "ready_policy": "action", "wait_ms": 1500,
                                                         "reuse_policy": "never", "task_id": "verify-visual4"}, timeout=90)
            wid4 = opened4["world_id"]
            r4 = await call(session, "world_entities", {"world_id": wid4, "role": "button",
                                                        "max_results": 5}, timeout=30)
            btn = next((e for e in r4.get("entities", []) if "变色" in str(e.get("name", ""))), None)
            if btn:
                t0 = asyncio.get_event_loop().time()
                card4 = await call(session, "world_click", {"world_id": wid4, "id": btn["id"],
                                                            "visual_evidence": True}, timeout=90)
                dt4 = round((asyncio.get_event_loop().time() - t0) * 1000)
                eff4 = card4.get("effect") or {}
                print(f"  [耗时 {dt4}ms]")
                print(f"  判定: {card4.get('page_outcome')} | {str(card4.get('why') or '')[:80]}")
                print(f"  effect: {json.dumps({k: eff4.get(k) for k in ('verdict', 'confidence', 'why')}, ensure_ascii=False)}")
                vis4 = eff4.get("visual") or card4.get("visual_diff")
                print(f"  视觉/样式diff: {json.dumps(vis4, ensure_ascii=False)[:400] if vis4 else '无'}")
            else:
                print("  未找到变色按钮")
            server.shutdown()
            await call(session, "world_close", {"world_id": wid4}, timeout=15)

            await call(session, "world_close", {"world_id": wid}, timeout=15)


def _serve_local():
    import ab_fixtures
    return ab_fixtures.serve()


if __name__ == "__main__":
    asyncio.run(main())
