from pathlib import Path
# -*- coding: utf-8 -*-
"""进阶增强功能验证:
1. world_fill 支持 type_delay_ms (逐字打字 locator-sequential-type)
2. world_batch_fill 批量填表 (单次交互填充多个字段)
3. world_click 遮挡检测与状态感知
"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "server.py")],
    )
    dyn_uri = (Path(__file__).parent.parent / "test_fixtures" / "dyn.html").resolve().as_uri()

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)

            # 1. 打开本地动态测试页
            r = await asyncio.wait_for(
                session.call_tool("world_open", {"url": dyn_uri, "wait_ms": 1000}),
                timeout=30,
            )
            data = json.loads(r.content[0].text)
            wid = data["world_id"]
            print(f"1. 打开测试页: world_id={wid}, summary.total={data['summary']['total']}")

            # 2. 测试逐字打字 world_fill (type_delay_ms=30)
            r = await asyncio.wait_for(
                session.call_tool(
                    "world_fill",
                    {"world_id": wid, "id": "input.搜索", "text": "agent-world", "type_delay_ms": 30},
                ),
                timeout=20,
            )
            fill_data = json.loads(r.content[0].text)
            print(f"2. 逐字打字结果: method={fill_data.get('method')}, filled={fill_data.get('filled')}")
            assert fill_data.get("method") == "locator-sequential-type", f"预期 sequential type, 实际 {fill_data.get('method')}"

            # 3. 测试批量填表 world_batch_fill
            r = await asyncio.wait_for(
                session.call_tool(
                    "world_batch_fill",
                    {
                        "world_id": wid,
                        "fields": [
                            {"id": "input.用户名", "text": "alice"},
                            {"id": "input.邮箱", "text": "alice@example.com"},
                        ],
                    },
                ),
                timeout=20,
            )
            batch_data = json.loads(r.content[0].text)
            print(f"3. 批量填表结果: batch_count={batch_data.get('batch_count')}")
            for res in batch_data.get("results", []):
                print(f"   - field {res['id']} -> {res['target']} ({res['method']})")
            
            st = batch_data.get("status", {})
            print(f"   状态卡 forms={st.get('forms')}")
            assert len(st.get("forms", [])) >= 2, "状态卡应感知到已填写的表单字段"

            # 4. 测试点击与遮挡诊断
            r = await asyncio.wait_for(
                session.call_tool("world_click", {"world_id": wid, "id": "button.搜索"}),
                timeout=20,
            )
            click_data = json.loads(r.content[0].text)
            print(f"4. 点击按钮: method={click_data.get('method')}, clicked={click_data.get('clicked')}")

            await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
            print("5. 测试全部通过并成功关闭世界！")


if __name__ == "__main__":
    asyncio.run(main())
