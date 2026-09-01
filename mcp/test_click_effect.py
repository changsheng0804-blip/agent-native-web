# -*- coding: utf-8 -*-
"""world_click 生效报告验证(实时闭环反馈的"操作→结果"因果关联):
正例:GF 点击乘客按钮 -> effect=effected/high,证据为区域内出现 dialog.number-of-passengers
负例:本地 dyn.html 点击无副作用标题 -> effect=no-change(不误报)
设计依据:全页 diff 在重型 SPA 上是重渲染噪声(新增108/移除851/更新946);
改为"目标空间区域(±200px)点击前后 diff",把真信号从噪声中分离。
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


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 负例:本地页点击无副作用标题 ──
            r = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 1000})
            wid = r["world_id"]
            rr = await call(session, "world_entities", {"world_id": wid, "text": "动态测试页"})
            ents = [e for e in rr.get("entities", []) if e.get("semantic") == "heading"]
            assert ents, "未找到标题元素"
            target = ents[0]["id"]
            r = await call(session, "world_click", {"world_id": wid, "id": target})
            effect = r.get("effect")
            print(f"1.负例 点击标题({target}): verdict={effect.get('verdict') if effect else None} "
                  f"confidence={effect.get('confidence') if effect else None}")
            assert effect, "world_click 应返回 effect"
            assert effect["verdict"] == "no-change", f"无副作用点击不应判 effected,实际 {effect['verdict']}"
            await call(session, "world_close", {"world_id": wid})

            # ── 正例:GF 点击乘客按钮 ──
            r = await call(session, "world_open", {"url": "https://www.google.com/travel/flights", "wait_ms": 4000})
            wid2 = r["world_id"]
            rr = await call(session, "world_entities", {"world_id": wid2, "name": "passenger"})
            btn = None
            for e in rr.get("entities", []):
                if e.get("interactive"):
                    btn = e
                    break
            assert btn, "未找到乘客按钮"
            r = await call(session, "world_click", {"world_id": wid2, "id": btn["id"]})
            effect = r.get("effect")
            print(f"2.正例 点击乘客按钮({btn['id']}): verdict={effect.get('verdict') if effect else None} "
                  f"confidence={effect.get('confidence') if effect else None}")
            print(f"   why: {effect.get('why') if effect else None}")
            assert effect, "world_click 应返回 effect"
            assert effect["verdict"] == "effected", f"点击乘客按钮应生效,实际 {effect['verdict']}"
            assert effect["confidence"] == "high", "应有高置信度"
            assert any("dialog" in (o.get("semantic") or "") for o in effect.get("observed", [])), \
                "证据应包含弹窗(dialog)"
            await call(session, "world_close", {"world_id": wid2})

            print("\n✅ 点击生效报告验证通过:正例 effected/high(证据含弹窗),负例 no-change(不误报)")


if __name__ == "__main__":
    asyncio.run(main())
