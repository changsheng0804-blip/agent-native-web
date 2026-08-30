# -*- coding: utf-8 -*-
"""验证 headful + profile:
1. profile 持久化:设置 cookie -> 关世界 -> 重开同 profile -> cookie 还在
2. headful 模式可正常打开世界(Windows 会弹窗,验证后立即关闭)
"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LOCAL = "http://127.0.0.1:8001/dyn.html"


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[r"F:\成果库\Agent 友好插件\agent-world-mcp\server.py"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)

            # 1. 打开 profile 世界,设置 cookie
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": LOCAL, "wait_ms": 1000, "profile": "test-profile-1"}),
                timeout=60,
            )
            d = json.loads(r.content[0].text)
            wid = d["world_id"]
            print(f"打开 profile 世界: {wid}, headful={d['headful']}, profile={d['profile']}")
            await asyncio.wait_for(
                session.call_tool("world_close", {"world_id": wid}), timeout=15
            )

            # 2. 用 playwright 直连同 profile,设置 cookie 再关闭(验证机制)
            import subprocess
            import sys as _sys
            code = """
import json, sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=r"F:\\成果库\\Agent 友好插件\\agent-world-mcp\\profiles\\test-profile-1",
        headless=True,
    )
    pg = ctx.new_page()
    pg.goto("http://127.0.0.1:8001/dyn.html")
    pg.evaluate("document.cookie = 'agentworld_test=hello; path=/; expires=Fri, 31 Dec 2027 23:59:59 GMT'")
    cookie = pg.evaluate("document.cookie")
    print("设置后 cookie:", cookie)
    ctx.close()
"""
            r = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
            print(r.stdout.strip() or r.stderr.strip())

            # 3. 重开同 profile,验证 cookie 还在
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": LOCAL, "wait_ms": 1000, "profile": "test-profile-1"}),
                timeout=60,
            )
            wid = json.loads(r.content[0].text)["world_id"]
            r = await asyncio.wait_for(
                session.call_tool("world_entity", {"world_id": wid, "id": "root.html"}),
                timeout=15,
            )
            # 直接读 cookie(世界模型不含 cookie,用世界内 evaluate 兜底不了,通过 screenshot 验证不必要;
            # 这里用世界模型的 __evaluate 能力没有暴露,所以用 playwright 直连同 profile 再验证一次)
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)

            code2 = """
import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=r"F:\\成果库\\Agent 友好插件\\agent-world-mcp\\profiles\\test-profile-1",
        headless=True,
    )
    pg = ctx.new_page()
    pg.goto("http://127.0.0.1:8001/dyn.html")
    print("重开后 cookie:", pg.evaluate("document.cookie"))
    ctx.close()
"""
            r = subprocess.run([_sys.executable, "-c", code2], capture_output=True, text=True, encoding="utf-8")
            print(r.stdout.strip() or r.stderr.strip())

            # 4. headful 模式冒烟(会短暂弹窗)
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": "https://example.com/", "wait_ms": 1500, "headful": True}),
                timeout=60,
            )
            d = json.loads(r.content[0].text)
            print(f"headful 世界: {d['world_id']}, headful={d['headful']}, 元素 {d['summary']['total']}")
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": d["world_id"]}), timeout=15)
            print("headful 关闭 OK")


if __name__ == "__main__":
    asyncio.run(main())