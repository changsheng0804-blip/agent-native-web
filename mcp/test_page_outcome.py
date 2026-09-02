# -*- coding: utf-8 -*-
"""Step 1 验证:page_outcome 五态判定(用既有夹具场景)

场景:
  1. challenge_overlay.html 表单提交 → 应判 challenged(fixed 遮罩+iframe)
  2. far_modal.html 点按钮开弹窗 → 应判 progressed/uncertain(无遮罩 iframe、URL 未变)
     或 unchanged(取决于 changes_seq)——记录实际值
  3. dyn.html 点无副作用标题 → 应判 unchanged
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return r


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # 场景1:挑战复刻(表单提交 → 遮罩 iframe)
            uri = (FIX / "challenge_overlay.html").as_uri()
            d = json.loads((await call(session, "world_open", {"url": uri, "wait_ms": 1200})).content[0].text)
            wid = d["world_id"]
            r = await call(session, "world_fill", {"world_id": wid, "id": "el_6", "text": "Alice"})
            btns = json.loads((await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 10})).content[0].text)
            btn = next(e for e in btns.get("entities", []) if "continue" in (e.get("text") or "").lower())
            rc = json.loads((await call(session, "world_click", {"world_id": wid, "id": btn["id"]})).content[0].text)
            po = rc.get("page_outcome")
            print(f"1. 挑战复刻: page_outcome = {po.get('page_outcome') if po else None}")
            if po:
                print(f"   situation = {json.dumps(po.get('situation', {}), ensure_ascii=False)[:200]}")
            ok1 = po and po.get("page_outcome") == "challenged"
            await call(session, "world_close", {"world_id": wid})

            # 场景2:远距弹窗(普通 overlay,无 iframe 遮罩)
            uri2 = (FIX / "far_modal.html").as_uri()
            d = json.loads((await call(session, "world_open", {"url": uri2, "wait_ms": 1000})).content[0].text)
            wid2 = d["world_id"]
            ents = json.loads((await call(session, "world_entities", {"world_id": wid2, "text": "打开居中弹窗", "max_results": 6})).content[0].text)
            tgt = next(e for e in ents.get("entities", []) if e.get("interactive"))
            rc = json.loads((await call(session, "world_click", {"world_id": wid2, "id": tgt["id"]})).content[0].text)
            po2 = rc.get("page_outcome")
            print(f"\n2. 远距弹窗(普通弹窗非遮罩): page_outcome = {po2.get('page_outcome') if po2 else None}")
            print(f"   situation = {json.dumps(po2.get('situation', {}) if po2 else {}, ensure_ascii=False)[:120]}")
            ok2 = po2 and po2.get("page_outcome") in ("progressed", "uncertain", "unchanged")
            print(f"   (普通弹窗不应误判 challenged: {po2.get('page_outcome') != 'challenged' if po2 else '未知'})")
            await call(session, "world_close", {"world_id": wid2})

            # 场景3:无副作用标题(负例)——页面上有动态变化时 uncertain 可接受,
            # 关键是不得误报 challenged/progressed(真实失败:将失败当成功)
            uri3 = (FIX / "dyn.html").as_uri()
            d = json.loads((await call(session, "world_open", {"url": uri3, "wait_ms": 1000})).content[0].text)
            wid3 = d["world_id"]
            ents = json.loads((await call(session, "world_entities", {"world_id": wid3, "text": "动态测试页", "role": "heading", "max_results": 4})).content[0].text)
            tgt3 = ents.get("entities", [])[0]
            rc = json.loads((await call(session, "world_click", {"world_id": wid3, "id": tgt3["id"]})).content[0].text)
            po3 = rc.get("page_outcome")
            po3v = po3.get("page_outcome") if po3 else None
            print(f"\n3. 负例无副作用标题: page_outcome = {po3v}(uncertain/unchanged 均可)")
            ok3 = po3v in ("unchanged", "uncertain")
            await call(session, "world_close", {"world_id": wid3})

            print(f"\n结果: 挑战误判检测={ok1} 普通弹窗不误判={ok2} 负例unchanged={ok3}")
            ok_all = ok1 and ok2 and ok3
            print("✅ Step 1 page_outcome 核心场景通过" if ok_all else "❌ 存在失败项")
            return 0 if ok_all else 1


rc = asyncio.run(main())
sys.exit(rc)