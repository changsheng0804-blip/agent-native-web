# -*- coding: utf-8 -*-
"""验证 headful + profile:
1. profile 持久化:设置 cookie -> 关世界 -> 重开同 profile -> cookie 还在
2. headful 模式可正常打开世界(Windows 会弹窗,验证后立即关闭)
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LOCAL = "http://127.0.0.1:8001/dyn.html"
PROFILE_NAME = "test-profile-1"
PROFILE_DIR = Path(__file__).resolve().parent / "profiles" / PROFILE_NAME
COOKIE_MARKER = "agentworld_test=hello"


def _run_profile_probe(code):
    result = subprocess.run(
        [sys.executable, "-c", code, str(PROFILE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parent / "server.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)

            # 1. 打开 profile 世界,确认服务器能创建并关闭该 profile。
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": LOCAL, "wait_ms": 1000, "profile": PROFILE_NAME}),
                timeout=60,
            )
            d = json.loads(r.content[0].text)
            wid = d["world_id"]
            print(f"打开 profile 世界: {wid}, headful={d['headful']}, profile={d['profile']}")
            await asyncio.wait_for(
                session.call_tool("world_close", {"world_id": wid}), timeout=15
            )

            # 1.5 MCP storage_state 恢复链路(独立进程 cookie DB 之外的核心验证):
            #    open → 世界内设 cookie → close(导出 storage_state.json) → reopen → 世界内断言 cookie 可见
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": LOCAL, "wait_ms": 1000, "profile": PROFILE_NAME}),
                timeout=60,
            )
            wid = json.loads(r.content[0].text)["world_id"]
            ev = await asyncio.wait_for(
                session.call_tool("world_eval", {"world_id": wid, "expression":
                    "() => { document.cookie = 'agentworld_test=hello; path=/; expires=Fri, 31 Dec 2027 23:59:59 GMT'; return document.cookie; }"}),
                timeout=20,
            )
            print("MCP 世界内设置 cookie:", json.loads(ev.content[0].text).get("result"))
            await asyncio.wait_for(
                session.call_tool("world_close", {"world_id": wid}), timeout=15
            )
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": LOCAL, "wait_ms": 1000, "profile": PROFILE_NAME}),
                timeout=60,
            )
            wid = json.loads(r.content[0].text)["world_id"]
            ev2 = await asyncio.wait_for(
                session.call_tool("world_eval", {"world_id": wid, "expression":
                    "() => document.cookie"}),
                timeout=20,
            )
            restored = str(json.loads(ev2.content[0].text).get("result") or "")
            print("MCP 重开后世界内 cookie:", restored)
            assert COOKIE_MARKER in restored, f"MCP storage_state 恢复链路失效: {restored}"
            await asyncio.wait_for(
                session.call_tool("world_close", {"world_id": wid}), timeout=15
            )
            print("MCP storage_state 恢复链路 OK")

            # 2. 用 Playwright 直连同 profile 设置持久 cookie。
            code = """
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
profile_dir = Path(sys.argv[1])
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=True,
    )
    pg = ctx.new_page()
    pg.goto("http://127.0.0.1:8001/dyn.html")
    pg.evaluate("document.cookie = 'agentworld_test=hello; path=/; expires=Fri, 31 Dec 2027 23:59:59 GMT'")
    print(pg.evaluate("document.cookie"))
    ctx.close()
"""
            output = _run_profile_probe(code)
            assert COOKIE_MARKER in output, f"设置 cookie 失败: {output}"
            print("设置后 cookie:", output)

            # 3. 让 MCP 服务器重开并关闭同一 profile，再由独立 Playwright 进程验证 cookie 仍存在。
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": LOCAL, "wait_ms": 1000, "profile": PROFILE_NAME}),
                timeout=60,
            )
            wid = json.loads(r.content[0].text)["world_id"]
            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)

            code2 = """
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
profile_dir = Path(sys.argv[1])
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=True,
    )
    pg = ctx.new_page()
    pg.goto("http://127.0.0.1:8001/dyn.html")
    print(pg.evaluate("document.cookie"))
    ctx.close()
"""
            output = _run_profile_probe(code2)
            assert COOKIE_MARKER in output, f"重开后 cookie 未持久化: {output}"
            print("重开后 cookie:", output)

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
