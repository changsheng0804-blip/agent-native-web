# -*- coding: utf-8 -*-
"""CDP 挂载安全测试:
1. 用独立临时 profile 启动 Chrome + remote-debugging-port(不碰用户日常浏览器)
2. world_open(cdp_url=...) 连接该实例,验证世界注入/查询/操作
3. world_close 后验证:临时浏览器进程仍存活(只断开不关闭用户浏览器) + cdp_disconnected 标记
4. 清理:kill 临时实例 + 删除临时 profile
"""
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9333  # 用非默认端口,避免与用户已有 9222 冲突


def start_temp_chrome():
    """用独立临时 profile 启动 Chrome,返回 (proc, profile_dir, cdp_url)"""
    profile_dir = tempfile.mkdtemp(prefix="agentworld-cdp-test-")
    cdp_url = f"http://127.0.0.1:{CDP_PORT}"
    proc = subprocess.Popen(
        [
            CHROME,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, profile_dir, cdp_url


def wait_cdp_ready(cdp_url, timeout=20):
    """轮询 CDP 调试端点,直到可连接"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


async def call(session, name, args, timeout=40):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    proc, profile_dir, cdp_url = start_temp_chrome()
    print(f"[安全] 已用临时 profile 启动独立 Chrome: {profile_dir}")
    print(f"[安全] CDP 端点: {cdp_url} (非用户日常浏览器,测试后 kill + 删 profile)")

    try:
        if not wait_cdp_ready(cdp_url):
            print("❌ CDP 端点未就绪")
            return

        params = StdioServerParameters(command=sys.executable, args=[SERVER])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=20)

                # 1. world_open 连接 CDP 实例,打开一个页面
                print("\n[1] world_open(cdp_url) 打开 example.com...")
                r = await call(session, "world_open", {"url": "https://example.com/", "cdp_url": cdp_url, "wait_ms": 3000})
                wid = r["world_id"]
                print(f"    world_id={wid} cdp_url={r.get('cdp_url')}")
                print(f"    url={r.get('url')} ready={r.get('ready')}")
                print(f"    summary.total={r['summary'].get('total')}")
                assert r.get("cdp_url") == cdp_url, "应回显 cdp_url"

                # 2. 世界查询
                print("\n[2] world_entities 查询链接...")
                r = await call(session, "world_entities", {"world_id": wid, "role": "link", "max_results": 5})
                print(f"    count={r.get('count')}")
                for e in r.get("entities", [])[:3]:
                    print(f"      {e['id']:10s} {e['name'][:40]}")
                assert r.get("count", 0) > 0, "CDP 世界应能查到构件"

                # 3. world_navigate 在世界内导航
                print("\n[3] world_navigate 到 HN...")
                r = await call(session, "world_navigate", {"world_id": wid, "url": "https://news.ycombinator.com/", "wait_ms": 3000})
                st = r.get("status", {})
                print(f"    url={st.get('page', {}).get('url', '')[:50]} elements={st.get('world', {}).get('elements')}")

                # 3.5 安全闸门:CDP 会话下 world_eval 应被禁用(IPI 后门防线)
                print("\n[3.5] world_eval 安全闸门(CDP 会话应被禁用)...")
                eval_resp = await asyncio.wait_for(
                    session.call_tool("world_eval", {"world_id": wid, "expression": "document.title"}), timeout=30
                )
                eval_text = eval_resp.content[0].text if eval_resp.content else ""
                print(f"    world_eval 返回: {str(eval_text)[:90]}")
                disabled = "已禁用" in str(eval_text)
                print(f"    闸门生效(返回禁用提示): {'✅ 是' if disabled else '❌ 否'}")
                assert disabled, "CDP 会话下 world_eval 应被禁用"

                # 4. world_close:关键安全断言
                print("\n[4] world_close(应只断开,不关闭浏览器)...")
                r = await call(session, "world_close", {"world_id": wid})
                print(f"    closed={r.get('closed')} cdp_disconnected={r.get('cdp_disconnected')}")
                assert r.get("cdp_disconnected") is True, "CDP 世界关闭应标记 cdp_disconnected"

                # 5. 验证浏览器进程仍存活(核心安全断言:没关用户浏览器)
                time.sleep(1)
                alive = proc.poll() is None
                print(f"\n[5] 安全断言:临时 Chrome 进程仍存活 = {alive}")
                assert alive, "❌ world_close 竟然杀掉了浏览器!严重安全问题"
                print("    ✅ world_close 只断开连接,未关闭浏览器进程(用户日常浏览器同理安全)")

                # 6. CDP 端点仍可访问(连接确实断开,但浏览器还活着)
                try:
                    with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2) as resp:
                        print(f"    CDP 端点仍响应(status={resp.status}),浏览器独立存活 ✓")
                except Exception as e:
                    print(f"    (CDP 端点状态: {e})")

                print("\n🎉 CDP 安全测试全部通过!")
    finally:
        # 清理:kill 临时实例 + 删临时 profile
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
            print(f"\n[清理] 已 kill 临时 Chrome (pid={proc.pid})")
        if profile_dir and os.path.isdir(profile_dir):
            for attempt in range(3):
                try:
                    shutil.rmtree(profile_dir, ignore_errors=True)
                    break
                except Exception:
                    time.sleep(1)
            print(f"[清理] 已删除临时 profile: {profile_dir}")


if __name__ == "__main__":
    asyncio.run(main())
