# -*- coding: utf-8 -*-
"""表单字段 name 属性定位验证(端到端实验 P0 修复的回归测试)

背景:2026-09-01 真站注册实验发现世界模型 attributes 不含 input 的 name 属性,
导致按 DOM 顺序填表被"幽灵字段"(真实表单前的干扰 input)错位。
本测试验证:
 1. world_entity 返回 attributes.name(精确字段身份)
 2. 真实表单字段(new_user[...] 模式)可按 name 唯一命中,幽灵字段(裸 firstname/lastname)可区分
 3. 按 name 过滤后按序填表,值进入正确字段(可用 world_entity 复核 value)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FORM_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "form_names.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            d = await call(session, "world_open", {"url": FORM_URI, "wait_ms": 800})
            wid = d["world_id"]

            # 1. 全部 input 构件,复核 attributes.name
            r = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 20})
            ids = [e["id"] for e in r.get("entities", [])]
            assert len(ids) >= 7, f"应有 7 个 input(2 幽灵+5 表单),实际 {len(ids)}"
            name_by_id = {}
            for iid in ids:
                ent = await call(session, "world_entity", {"world_id": wid, "id": iid})
                nm = (ent.get("attributes") or {}).get("name", "")
                name_by_id[nm] = iid
            print(f"1. attributes.name 全部可见: {sorted(k for k in name_by_id if k)}")
            assert "new_user[email]" in name_by_id, "真实表单字段 name 应可读取"
            assert "firstname" in name_by_id, "幽灵字段也应可读取(用于区分)"

            # 2. 按 name 定位真实字段并填表(绕过幽灵字段)
            mapping = {
                "new_user[first_name]": "Alice",
                "new_user[last_name]": "Qian",
                "new_user[username]": "alice2026",
                "new_user[email]": "alice@example.com",
                "new_user[password]": "Passw0rd!x",
            }
            for nm, text in mapping.items():
                ent = await call(session, "world_entity", {"world_id": wid, "id": name_by_id[nm]})
                assert (ent.get("attributes") or {}).get("name") == nm, f"{nm} 定位错误"
            print("2. 按 name 定位 5 个真实字段全部命中 ✅")

            # 3. 填表 + 复核值进入正确字段(幽灵字段必须为空)
            for nm, text in mapping.items():
                r = await call(session, "world_fill", {"world_id": wid, "id": name_by_id[nm], "text": text})
                assert r.get("effect", {}).get("verdict") in ("effected", "changed", "no-change"), f"{nm} 填表异常"
            for nm, text in mapping.items():
                ent = await call(session, "world_entity", {"world_id": wid, "id": name_by_id[nm]})
                val = (ent.get("attributes") or {}).get("value", "")
                assert val == text, f"{nm} 值 {val!r} != {text!r}"
            # 幽灵字段未被填
            for ghost in ("firstname", "lastname"):
                ent = await call(session, "world_entity", {"world_id": wid, "id": name_by_id[ghost]})
                val = (ent.get("attributes") or {}).get("value", "")
                assert not val, f"幽灵字段 {ghost} 不应被填入,实际 {val!r}"
            print("3. 填表值进入正确字段且幽灵字段为空 ✅")

            await call(session, "world_close", {"world_id": wid})
            print("\n✅ 表单字段 name 属性定位验证通过(修复真实短板)")


if __name__ == "__main__":
    asyncio.run(main())