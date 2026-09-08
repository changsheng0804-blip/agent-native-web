# -*- coding: utf-8 -*-
"""Profile 路径约束回归探针。

目标：外部 profile 名称只能映射到 mcp/profiles 的直接子目录，
不得通过 `..`、路径分隔符或绝对路径逃逸到其他位置。

这是 Draft PR 的专项探针，不自动加入默认离线门禁；后续 Harness
完成生产修复后应显式运行本文件，并决定是否正式收编到 special 测试组。
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_DIR = Path(__file__).resolve().parent.parent
SERVER = str(MCP_DIR / "server.py")
DYN_URI = (MCP_DIR.parent / "tests" / "fixtures" / "dyn.html").as_uri()
ESCAPE_PROFILE = "../profile-path-escape-probe"
ESCAPE_DIR = MCP_DIR / "profile-path-escape-probe"


async def main():
    # 防止前一次失败留下目录影响判断。
    shutil.rmtree(ESCAPE_DIR, ignore_errors=True)

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    opened_world = None
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=20)

                result = await asyncio.wait_for(
                    session.call_tool(
                        "world_open",
                        {"url": DYN_URI, "wait_ms": 200, "profile": ESCAPE_PROFILE},
                    ),
                    timeout=60,
                )
                text = result.content[0].text if result.content else ""

                # 生产实现当前可能用结构化 JSON 表示成功，用普通错误文本表示拒绝。
                # 只要拿到了 world_id，就说明危险 profile 被实际接受。
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, dict):
                    opened_world = payload.get("world_id")

                if opened_world is not None:
                    await asyncio.wait_for(
                        session.call_tool("world_close", {"world_id": opened_world}),
                        timeout=20,
                    )

                assert opened_world is None, (
                    "profile='../profile-path-escape-probe' 不应被接受；"
                    "profile 必须被约束在 mcp/profiles 的直接子目录内"
                )

                assert not ESCAPE_DIR.exists(), (
                    f"拒绝非法 profile 后不应在 profiles 目录外创建数据目录: {ESCAPE_DIR}"
                )
                print("Profile 路径逃逸输入已被拒绝")
    finally:
        # 当前未修复版本会实际创建逃逸目录；探针失败时也清理，避免污染工作区。
        shutil.rmtree(ESCAPE_DIR, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
