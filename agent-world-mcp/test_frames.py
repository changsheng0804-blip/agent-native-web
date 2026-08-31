# -*- coding: utf-8 -*-
"""验证:frame 感知状态卡 + anomaly + world_navigate + world_click_at
闲鱼搜索页(headful):frames 应包含登录框 iframe;headless 简化页应标 anomaly
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

            # 1. headful 闲鱼搜索页:frames 感知
            r = await asyncio.wait_for(
                session.call_tool("world_open", {
                    "url": "https://www.goofish.com/search?q=Antigravity",
                    "wait_ms": 6000, "headful": True, "profile": "google-flow",
                }),
                timeout=90,
            )
            data = json.loads(r.content[0].text)
            wid = data["world_id"]
            st = data["status"]
            print(f"1. headful 闲鱼搜索页:")
            print(f"   page.url={st['page']['url'][:60]}")
            print(f"   page.state={st['page']['state']} domTotal={st['page'].get('domTotal')} worldElements={st['world'].get('elements')}")
            print(f"   frames({len(st['frames'])}):")
            for f in st["frames"]:
                print(f"     - ready={f['ready']} elements={f['elements']} url={f['url'][:60]}")

            # 2. world_navigate 世界内导航
            r = await asyncio.wait_for(
                session.call_tool("world_navigate", {"world_id": wid, "url": "https://www.goofish.com/", "wait_ms": 4000}),
                timeout=60,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"2. world_navigate 后: url={st['page']['url'][:50]} state={st['page']['state']} changed.page={'page' in st.get('changed', {})}")

            # 3. world_click_at 坐标点击(点击搜索框区域)
            r = await asyncio.wait_for(
                session.call_tool("world_click_at", {"world_id": wid, "x": 500, "y": 90}),
                timeout=20,
            )
            data = json.loads(r.content[0].text)
            print(f"3. world_click_at: {data.get('method')} clicked_at={data.get('clicked_at')}")

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)

            # 4. headless 闲鱼:anomaly 检测
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://www.goofish.com/search?q=Antigravity", "wait_ms": 4000}),
                timeout=90,
            )
            data = json.loads(r.content[0].text)
            st = data["status"]
            print(f"4. headless 闲鱼: state={st['page']['state']} domTotal={st['page'].get('domTotal')} worldElements={st['world'].get('elements')}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": data["world_id"]}), timeout=15)


if __name__ == "__main__":
    asyncio.run(main())