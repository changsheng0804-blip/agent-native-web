# -*- coding: utf-8 -*-
"""世界状态卡验证:
1. 工具返回自动附带 status(auth/dialogs/page/forms/world)
2. 点击弹窗 -> dialogs 出现
3. fill -> forms 出现
4. 状态卡大小控制
5. 登录态信号(google-flow profile 有闲鱼登录 cookie)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parent / "server.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)

            # 1. world_open 返回带状态卡
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 5000}),
                timeout=60,
            )
            data = json.loads(r.content[0].text)
            wid = data["world_id"]
            st = data.get("status", {})
            print(f"1. world_open 返回带状态卡: {'是' if 'status' in data else '否'}")
            print(f"   auth={st.get('auth')}")
            print(f"   page={st.get('page')}")
            print(f"   world={st.get('world')}")
            print(f"   状态卡 JSON 长度: {len(json.dumps(st, ensure_ascii=False))} 字符")

            # 2. 本地动态页验证 forms(受控组件稳定的站点)
            dyn_uri = (Path(__file__).resolve().parent.parent / "test_fixtures" / "dyn.html").as_uri()
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": dyn_uri, "wait_ms": 1000}),
                timeout=60,
            )
            w2 = json.loads(r.content[0].text)["world_id"]
            r = await asyncio.wait_for(
                session.call_tool("world_fill", {"world_id": w2, "id": "input.搜索", "text": "hello world"}),
                timeout=20,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"2. 本地页 fill 后 forms={st['forms']}")
            print(f"   changed={st.get('changed')}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": w2}), timeout=15)

            # 3. 点击乘客按钮 -> dialogs 出现
            r = await asyncio.wait_for(
                session.call_tool("world_click", {"world_id": wid, "id": "el_104"}),
                timeout=20,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"3. 点击乘客按钮后 dialogs={st['dialogs']}")
            print(f"   changed={st.get('changed')}")

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)

            # 4. 登录态信号:google-flow profile 含闲鱼登录 cookie(页面会被拦没关系,cookie 检测独立)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://www.goofish.com/", "wait_ms": 3000, "profile": "google-flow"}),
                timeout=60,
            )
            data = json.loads(r.content[0].text)
            st = data.get("status", {})
            print(f"4. 闲鱼 profile 登录态: auth={st.get('auth')}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": data["world_id"]}), timeout=15)


if __name__ == "__main__":
    asyncio.run(main())