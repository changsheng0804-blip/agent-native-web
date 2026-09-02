# -*- coding: utf-8 -*-
"""假设B:Shadow DOM 组件——内核扫描能否看见 shadow root 内的元素?

判定:
  - 若 world_entities 能列出组件内按钮/输入框 → 假设不成立(已穿透)
  - 若只看见组件外壳(<my-widget>, 无内部按钮) → 假设成立(现代组件库页面整块失效)
对照:页面普通按钮(主 DOM)必须可见,用于排除"页面整体不可见"的干扰。
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "assumption_b_shadow.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            d = await call(session, "world_open", {"url": URI, "wait_ms": 1000})
            wid = d["world_id"]
            print(f"页面元素总数: {d.get('summary', {}).get('total')}")

            r = await call(session, "world_entities", {"world_id": wid, "max_results": 50})
            ents = r.get("entities", [])
            print(f"实体清单({len(ents)}):")
            for e in ents:
                print(f"  {e['id']:8s} {e['semantic']:<12s} {e['name'][:45]:<45} tag={e['tag']} text={(e.get('text') or '')[:30]}")

            # 判定:组件内按钮(文本"组件内的按钮")与输入框(placeholder 组件内的输入框)是否可见
            inner_btn = [e for e in ents if "组件内的按钮" in (e.get("text") or "")]
            inner_input = [e for e in ents if "组件内" in (e.get("name") or "")]
            plain_btn = [e for e in ents if "普通按钮" in (e.get("text") or "")]
            print(f"\n主 DOM 普通按钮可见: {len(plain_btn) > 0}(对照)")
            print(f"Shadow 内按钮可见: {len(inner_btn) > 0}")
            print(f"Shadow 内输入框可见: {len(inner_input) > 0}")

            if plain_btn and not inner_btn and not inner_input:
                print("\n❌ 假设B 成立:扫描器看不见 Shadow DOM 内部——现代组件库(弹窗/下拉/日期选择器)页面会大面积失效")
                sys.exit(1)
            elif inner_btn or inner_input:
                print("\n✅ 假设B 不成立:Shadow DOM 元素可见(内核已穿透或组件内容被扫出)")
            else:
                print("\n⚠️ 无法判定:普通按钮也不可见(页面整体问题)")
                sys.exit(2)
            await call(session, "world_close", {"world_id": wid})


asyncio.run(main())