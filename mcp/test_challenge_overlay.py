# -*- coding: utf-8 -*-
"""挑战复刻夹具验证:表单提交后出现 fixed 遮罩+iframe(仿 GitLab Arkose)

验证两件事:
  1. 世界能否感知"遮罩 iframe 挑战"(复现真实实验中的盲区)
  2. 操作后判定:提交按钮点击后的 effect 是什么(changed? no-change? 有没有"挑战"信号?)

这是 Step 1(page_outcome challenged)的种子验收场景——记录当前工具的盲区表现。
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "challenge_overlay.html").as_uri()


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

            # 1. 填表单(用 name 属性定位,验证 P0 修复在此页也有效)
            r = await call(session, "world_eval", {"world_id": wid, "expression": """() => {
                const map = {}; for (const e of agentWorld._runtime.world.elements.values()) {
                    if (!e._el) continue; const nm = e._el.getAttribute && e._el.getAttribute('name');
                    if (nm && /^new_user\\[/.test(nm)) map[nm] = e.id; } return JSON.stringify(map); }"""})
            fields = json.loads(r.get("result") or "{}")
            if isinstance(fields, str):
                fields = json.loads(fields)
            print(f"表单字段映射: {fields}")
            vals = {"new_user[first_name]": "Alice", "new_user[email]": "a@example.com", "new_user[password]": "x"}
            for nm, text in vals.items():
                if nm in fields:
                    rr = await call(session, "world_fill", {"world_id": wid, "id": fields[nm], "text": text})
                    print(f"  填 {nm}: verdict={rr.get('effect', {}).get('verdict')}")

            # 2. 点 Continue(仿真实场景)
            btns = await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 10})
            btn = next((e for e in btns.get("entities", []) if "continue" in (e.get("text") or "").lower()), None)
            rc = await call(session, "world_click", {"world_id": wid, "id": btn["id"]})
            eff = rc.get("effect", {})
            print(f"\n点击 Continue: verdict={eff.get('verdict')} conf={eff.get('confidence')}")
            print(f"  why: {eff.get('why', '')[:100]}")

            # 3. 提交后的世界状态(真实实验中:表单清空 + 遮罩 iframe,但状态卡全"正常")
            await asyncio.sleep(1)
            st = await call(session, "world_state", {"world_id": wid})
            state = st.get("state", {})
            print(f"\n提交后 world_state:")
            print(f"  url: {state.get('url', '')[:60]}")
            print(f"  dialogs: {[x.get('name') for x in state.get('dialogs', [])]}")
            print(f"  forms: {state.get('forms', [])}")
            print(f"  page.state: {state.get('page', {}).get('state')}")

            # 4. 世界能列出 iframe 相关吗?找遮罩或 iframe 构件
            ents = await call(session, "world_entities", {"world_id": wid, "max_results": 30})
            frame_like = [e for e in ents.get("entities", []) if e.get("tag") == "iframe" or "挑战" in (e.get("text") or "") or "验证" in (e.get("text") or "")]
            print(f"\n世界清单中 iframe/挑战相关构件: {len(frame_like)} 个")
            for e in frame_like[:6]:
                print(f"  {e['id']} {e['semantic']:<10s} {e['name'][:40]} text={(e.get('text') or '')[:40]}")

            print("\n=== 盲区判定 ===")
            print(f"① dialogs 是否感知遮罩: {'是(感知到)' if state.get('dialogs') else '否(盲区,同真实实验)'}")
            print(f"② forms 是否为空(提交后清空): {'是,空' if not state.get('forms') else '有值'}")
            print(f"③ iframe 是否可见: {'可见' if frame_like else '不可见(盲区)'}")
            await call(session, "world_close", {"world_id": wid})


asyncio.run(main())