# -*- coding: utf-8 -*-
"""两个工具缺陷的回归测试(2026-09-02 弱模型验证发现)

缺陷1: world_fill type_delay_ms>0 不清空 → 二次输入追加污染("ap"→"apapple")
缺陷2: observer 无 characterData → 纯文本更新世界模型读不到旧值

场景(autocomplete 夹具):
  1. fill "ap"(打字)→ 联想出现
  2. 再次 fill "apple"(打字)→ 值应被清空替换为 apple,不得是 apapple
  3. 点击联想项 → 最终值区域文本更新 → world 应能读到 "已选择"
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "autocomplete.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def get_input_value(session, wid):
    r = await call(session, "world_eval", {"world_id": wid, "expression": "() => document.getElementById('search').value"})
    return (r.get("result") or "").strip('"')


async def get_final_text(session, wid):
    r = await call(session, "world_eval", {"world_id": wid, "expression": "() => document.getElementById('final-value').textContent"})
    return (r.get("result") or "").strip('"')


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            d = await call(session, "world_open", {"url": URI, "wait_ms": 1000})
            wid = d["world_id"]

            # 找输入框
            r = await call(session, "world_entities", {"world_id": wid, "role": "input", "max_results": 5})
            inp = next(e for e in r.get("entities", []) if e.get("interactive"))

            # ── 缺陷1:二次 fill 打字的清空语义 ──
            await call(session, "world_fill", {"world_id": wid, "id": inp["id"], "text": "ap", "type_delay_ms": 30})
            await asyncio.sleep(0.8)
            v1 = await get_input_value(session, wid)
            print(f"1. 首次 fill 'ap' → 输入框值 = {v1!r}")
            assert v1 == "ap", f"首次输入应为 ap,实际 {v1!r}"

            # 联想应出现
            r = await call(session, "world_entities", {"world_id": wid, "role": "option", "max_results": 10})
            opts = r.get("entities", [])
            print(f"   联想下拉选项: {[e['text'] for e in opts]}")
            assert len(opts) >= 2, "联想下拉应出现 apple/apricot"

            # 关键:再 fill "apple"(打字)——旧值 ap 必须被清空,不能是 apapple
            await call(session, "world_fill", {"world_id": wid, "id": inp["id"], "text": "apple", "type_delay_ms": 30})
            await asyncio.sleep(0.8)
            v2 = await get_input_value(session, wid)
            print(f"2. 二次 fill 'apple'(关键) → 输入框值 = {v2!r}")
            assert v2 == "apple", f"缺陷1未修复:二次输入应替换为 apple,实际 {v2!r}(追加污染)"
            print("   ✅ 缺陷1 修复:fill 先清空再打字,不追加污染")

            # ── 缺陷2:文本更新可感知 ──
            # 清空后触发联想并点选 apple,最终值区域文本应更新
            await call(session, "world_fill", {"world_id": wid, "id": inp["id"], "text": "ap", "type_delay_ms": 30})
            await asyncio.sleep(0.6)
            r = await call(session, "world_entities", {"world_id": wid, "role": "option", "max_results": 10})
            opts = r.get("entities", [])
            apple = next((e for e in opts if "apple" in (e.get("text") or "")), None)
            assert apple, "找不到 apple 联想项"
            await call(session, "world_click", {"world_id": wid, "id": apple["id"]})
            await asyncio.sleep(1.2)  # 等 observer flush

            # world 直接 eval 真值
            final_real = await get_final_text(session, wid)
            print(f"3. 点选后最终值区域(DOM 真值) = {final_real!r}")
            assert "已选择" in final_real, f"页面应显示已选择,实际 {final_real!r}"

            # world_entities 读最终值区域的文本(此前会读到旧文本)
            r = await call(session, "world_entities", {"world_id": wid, "text": "已选择", "max_results": 10})
            found = r.get("entities", [])
            print(f"   world_entities 按'已选择'查询命中: {len(found)} 条")
            assert len(found) >= 1, "缺陷2未修复:世界模型仍读不到更新后的文本"
            print("   ✅ 缺陷2 修复:文本更新进入世界模型,可按新文本查询")

            await call(session, "world_close", {"world_id": wid})
            print("\n✅ 两个工具缺陷回归测试全部通过")
            return 0


rc = asyncio.run(main())
sys.exit(rc)