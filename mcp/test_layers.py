# -*- coding: utf-8 -*-
"""world_layers 图层视图最小守护测试(offline 门禁组)。

覆盖:_t_world_layers(aw_query.py) → 内核 agentWorld.query.layers()
返回结构(见 extension/engine/query.js 的 layers()):
  structure: { total, byTag }                    标签分布(byTag 键为小写标签名)
  semantic:  { byRole, types }                   语义角色分布(byRole 键为角色名)
  spatial:   { viewport{width,height}, scroll{y,totalHeight}, grid }
  interactive: 可交互构件数
  names:     { total, named, unnamed }

断言点(以本地 tabs.html 为夹具:3 个 tab 按钮 + tablist + tabpanel + h1):
  1. 返回 5 个顶层键齐全,失败时打印完整结构做差异
  2. structure.total > 0,byTag["button"] == 3(夹具唯一 3 个按钮)
  3. semantic.types >= 3,byRole 含 tablist(夹具角色:tab/tabpanel/tablist/heading…)
  4. spatial.viewport 宽高 > 0,scroll.totalHeight > 0
  5. interactive >= 3(三个 tab 按钮可交互)
  6. names 守恒:named + unnamed == total,且 named >= 3

运行:python mcp/test_layers.py(由 run_quality.py offline 组拉起,cwd=mcp)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
TABS_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tabs.html").as_uri()

EXPECTED_KEYS = ("structure", "semantic", "spatial", "interactive", "names")


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": TABS_URI, "wait_ms": 1200})
            wid = opened["world_id"]

            layers = await call(session, "world_layers", {"world_id": wid})

            # 1. 顶层键齐全
            missing = [k for k in EXPECTED_KEYS if k not in layers]
            assert not missing, f"world_layers 缺字段 {missing}，实际返回:\n{json.dumps(layers, ensure_ascii=False, indent=2)}"

            # 2. 结构层:标签分布
            struct = layers["structure"]
            by_tag = struct.get("byTag", {})
            assert struct.get("total", 0) > 0, f"structure.total 应为正数: {struct}"
            actual_buttons = by_tag.get("button", 0)
            assert actual_buttons == 3, f"tabs.html 应有 3 个 button,实际 {actual_buttons}(byTag={by_tag})"

            # 3. 语义层:角色分布
            sem = layers["semantic"]
            roles = sem.get("byRole", {})
            assert sem.get("types", 0) >= 3, f"语义类型应 >= 3: {sem}"
            assert "tablist" in roles, f"byRole 应含 tablist: {list(roles.keys())}"

            # 4. 空间层:视口与滚动
            sp = layers["spatial"]
            vp = sp.get("viewport", {})
            scroll = sp.get("scroll", {})
            assert vp.get("width", 0) > 0 and vp.get("height", 0) > 0, f"viewport 应为正: {vp}"
            assert scroll.get("totalHeight", 0) > 0, f"scroll.totalHeight 应为正: {scroll}"

            # 5. 交互层:三个 tab 按钮可交互
            assert layers.get("interactive", 0) >= 3, f"interactive 应 >= 3: {layers.get('interactive')}"

            # 6. 名字层:守恒 + 有名字构件
            names = layers["names"]
            assert names.get("total") == struct.get("total"), \
                f"names.total({names.get('total')}) != structure.total({struct.get('total')})"
            assert names.get("named", 0) + names.get("unnamed", 0) == names.get("total"), names
            assert names.get("named", 0) >= 3, f"命名构件应 >= 3: {names}"

            print(f"1.world_layers 结构齐全: total={struct['total']} "
                  f"button={actual_buttons} 角色数={sem.get('types')} interactive={layers.get('interactive')}")
            print(f"2.语义层含 tablist,空间层 viewport={vp} 名字 named={names.get('named')}")

            await call(session, "world_close", {"world_id": wid})
            print("\n✅ world_layers 图层视图验证通过")


if __name__ == "__main__":
    asyncio.run(main())
