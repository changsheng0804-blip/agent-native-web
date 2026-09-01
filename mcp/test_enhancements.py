# -*- coding: utf-8 -*-
"""进阶增强功能验证:
1. world_fill 支持 type_delay_ms (逐字打字 locator-sequential-type)
2. world_batch_fill 批量填表 (单次交互填充多个字段,逐字段容错)
3. world_click 遮挡检测与状态感知
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
DYN_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "dyn.html").as_uri()


async def call(session, name, args, timeout=30):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)

            # 1. 打开本地动态测试页
            r = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 1000})
            wid = r["world_id"]
            print(f"1. 打开测试页: world_id={wid}, summary.total={r['summary']['total']}")

            # 2. 逐字打字 world_fill (type_delay_ms=30)
            r = await call(session, "world_fill", {"world_id": wid, "id": "input.搜索", "text": "agent-world", "type_delay_ms": 30})
            print(f"2. 逐字打字结果: method={r.get('method')}, filled={r.get('filled')}")
            assert r.get("method") == "locator-sequential-type", f"预期 sequential type, 实际 {r.get('method')}"

            # 3. 批量填表 world_batch_fill(含一个故意错误字段验证容错)
            r = await call(session, "world_batch_fill", {
                "world_id": wid,
                "fields": [
                    {"id": "input.用户名", "text": "alice"},
                    {"id": "input.邮箱", "text": "alice@example.com"},
                    {"id": "input.不存在的字段", "text": "should-fail"},
                ],
            })
            print(f"3. 批量填表: batch_count={r.get('batch_count')} ok_count={r.get('ok_count')}")
            for res in r.get("results", []):
                print(f"   - {res.get('id')} -> ok={res.get('ok')} method={res.get('method')} error={res.get('error', '')[:40]}")
            assert r.get("ok_count") == 2, "应成功 2 个,失败 1 个(容错)"

            # 4. 状态卡 forms 回显
            st = r.get("status", {})
            print(f"   状态卡 forms={st.get('forms')}")
            assert len(st.get("forms", [])) >= 2, "状态卡应感知到已填写的表单字段"

            # 5. 点击 + 遮挡诊断(本地页无遮挡,应无 obscured_note)
            r = await call(session, "world_click", {"world_id": wid, "id": "button.搜索"})
            print(f"4. 点击按钮: method={r.get('method')}, clicked={r.get('clicked')}, obscured_note={r.get('obscured_note')}")

            await call(session, "world_close", {"world_id": wid})
            print("5. 测试全部通过!")


if __name__ == "__main__":
    asyncio.run(main())
