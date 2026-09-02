# -*- coding: utf-8 -*-
"""Shadow DOM 动态验证:修复后 shadow 内新增元素的实时感知

场景:页面加载后,通过 JS 向既有组件的 shadow root 里追加一个新按钮,
验证:
  1. world_wait 能等到它出现(observer 感知 shadow 内部 childList 变化)
  2. world_entities 能找到它、并能点击生效
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "assumption_b_shadow.html").as_uri()


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

            # 1. 动态向 shadow root 追加按钮(模拟组件懒加载/运行期渲染)
            r = await call(session, "world_eval", {"world_id": wid, "expression": """() => {
                const host = document.querySelector('my-widget');
                const shadow = host.shadowRoot;
                const btn = document.createElement('button');
                btn.id = 'dyn-shadow-btn';
                btn.textContent = '动态影子按钮';
                btn.style.cssText = 'padding:6px 16px;cursor:pointer;background:#27ae60;color:#fff;border:none;margin-top:8px;';
                btn.addEventListener('click', () => {
                    const msg = shadow.querySelector('#w-msg');
                    msg.textContent = '点了动态影子按钮';
                });
                shadow.appendChild(btn);
                return true;
            }"""})
            print(f"注入动态按钮: ok={r.get('result')}")

            # 2. world_wait 事件驱动应感知 shadow 内部变化
            r = await call(session, "world_wait", {"world_id": wid, "mode": "appear", "text": "动态影子按钮", "timeout_ms": 8000})
            print(f"world_wait 感知 shadow 内新增: matched={r.get('matched')} driven={r.get('driven')}")

            # 3. 定位并点击
            ents = await call(session, "world_entities", {"world_id": wid, "text": "动态影子按钮", "max_results": 10})
            btn = next((e for e in ents.get("entities", []) if e.get("interactive")), None)
            print(f"世界定位动态按钮: {btn['id'] if btn else '未找到'}")
            if btn:
                rc = await call(session, "world_click", {"world_id": wid, "id": btn["id"]})
                print(f"点击动态按钮: verdict={rc.get('effect', {}).get('verdict')}")
                # 验证真实生效(shadow 内消息文本变化)
                rr = await call(session, "world_eval", {"world_id": wid, "expression": "() => document.querySelector('my-widget').shadowRoot.querySelector('#w-msg').textContent"})
                print(f"shadow 内结果文本: {rr.get('result', '')[:40]}")

            ok = r.get("matched") and bool(btn)
            print(f"\n{'✅' if ok else '❌'} Shadow DOM 动态感知: {'通过' if ok else '失败'}")
            await call(session, "world_close", {"world_id": wid})
            return 0 if ok else 1


rc = asyncio.run(main())
sys.exit(rc)