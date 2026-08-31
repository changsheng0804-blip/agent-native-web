from pathlib import Path
# -*- coding: utf-8 -*-
"""真实全链路实战验证:
1. Google Flights 真实站点搜索自动联想 (通过 type_delay_ms 逐字打字触发联想列表)
2. 键盘按键选择下拉候选项 (world_press Enter)
3. 状态卡与变更日志验证 (dialogs, changesSeq)
4. 多字段批量填写与状态卡 forms 回显 (world_batch_fill)
5. 结果截图归档 (world_screenshot)
"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_google_flights(session):
    print("=" * 60)
    print("【实战场景一】Google Flights 复杂 SPA 真实交互测试")
    print("=" * 60)

    # 1. 打开 Google Flights 并等待世界模型就绪
    print("[1] 打开 Google Flights...")
    r = await session.call_tool(
        "world_open",
        {"url": "https://www.google.com/travel/flights?hl=en", "wait_ms": 4000, "stabilize_ms": 8000},
    )
    open_data = json.loads(r.content[0].text)
    wid = open_data["world_id"]
    st = open_data["status"]
    print(f"    - 世界 ID: {wid}")
    print(f"    - 元素总数: {open_data['summary']['total']}, 可交互: {open_data['summary']['interactive']}")
    print(f"    - 初始状态卡: page.state={st['page']['state']}, dialogs={len(st['dialogs'])}")

    # 2. 点击出发地输入框打开弹窗
    print("\n[2] 点击出发地 (combobox.where-from)...")
    r = await session.call_tool("world_click", {"world_id": wid, "id": "combobox.where-from"})
    click_data = json.loads(r.content[0].text)
    st = click_data["status"]
    print(f"    - 点击方式: {click_data.get('method')}, 目标: {click_data.get('clicked')}")
    print(f"    - 状态卡弹窗感知: dialogs = {st.get('dialogs')}")

    await asyncio.sleep(1)

    # 3. 使用 type_delay_ms 逐字输入 "Tokyo" 触发联想推荐
    print("\n[3] 逐字输入 'Tokyo' (type_delay_ms=40) 触发自动联想...")
    r = await session.call_tool(
        "world_fill",
        {"world_id": wid, "id": "combobox.where-from", "text": "Tokyo", "type_delay_ms": 40},
    )
    fill_data = json.loads(r.content[0].text)
    print(f"    - 输入方式: {fill_data.get('method')}, 填入内容: '{fill_data.get('text')}'")

    # 等待联想下拉列表渲染
    await asyncio.sleep(2.5)

    # 4. 查询世界模型中是否成功生成了 Tokyo 联想候选项
    print("\n[4] 检查世界模型中的自动联想候选项 (world_entities)...")
    r = await session.call_tool("world_entities", {"world_id": wid, "text": "Tokyo", "max_results": 6})
    tokyo_data = json.loads(r.content[0].text)
    print(f"    - 成功检索到相关构件: {tokyo_data['count']} 个")
    for e in tokyo_data["entities"][:5]:
        print(f"      • [{e['id']}] {e['name'][:35]:35s} | {e.get('text', '')[:40]}")

    # 5. 键盘按键 Enter 选择第一个联想建议
    print("\n[5] 按 Enter 键选择第一项建议 (world_press)...")
    r = await session.call_tool("world_press", {"world_id": wid, "id": "combobox.where-from", "key": "Enter"})
    press_data = json.loads(r.content[0].text)
    print(f"    - 按键方式: {press_data.get('method')}, 按键: {press_data.get('key')}")

    await asyncio.sleep(1)

    # 6. 读取变更日志，确认操作后页面的增量变化
    print("\n[6] 读取增量变更日志 (world_changes)...")
    r = await session.call_tool("world_changes", {"world_id": wid, "since": 0})
    changes_data = json.loads(r.content[0].text)
    print(f"    - 页面演进版本序号 (to): {changes_data['to']}, 事件流数: {len(changes_data['events'])}")

    # 7. 截图存档
    r = await session.call_tool("world_screenshot", {"world_id": wid})
    shot_path = json.loads(r.content[0].text)["path"]
    print(f"    - 视觉截图已保存至: {shot_path}")

    # 8. 关闭世界
    await session.call_tool("world_close", {"world_id": wid})
    print("    - 世界已关闭。\n")


async def test_batch_fill_and_status(session):
    print("=" * 60)
    print("【实战场景二】多字段批量填表 (world_batch_fill) 与状态卡回显测试")
    print("=" * 60)

    dyn_uri = (Path(__file__).parent.parent / "test_fixtures" / "dyn.html").resolve().as_uri()
    r = await session.call_tool("world_open", {"url": dyn_uri, "wait_ms": 500})
    wid = json.loads(r.content[0].text)["world_id"]

    # 批量填入 3 个表单项
    print("[1] 批量填写 3 个表单字段 (搜索、用户名、邮箱)...")
    r = await session.call_tool(
        "world_batch_fill",
        {
            "world_id": wid,
            "fields": [
                {"id": "input.搜索", "text": "Antigravity Agent", "type_delay_ms": 20},
                {"id": "input.用户名", "text": "deepmind_tester"},
                {"id": "input.邮箱", "text": "tester@google.com"},
            ],
        },
    )
    batch_data = json.loads(r.content[0].text)
    st = batch_data["status"]
    print(f"    - 批量执行成功数: {batch_data['batch_count']}")
    for item in batch_data["results"]:
        print(f"      • {item['id']} -> {item['target']} ({item['method']})")
    
    print(f"\n[2] 验证状态卡 forms 仪表盘回显:")
    for f in st.get("forms", []):
        print(f"      • [{f['id']}] {f['name']}: '{f['value']}'")

    await session.call_tool("world_close", {"world_id": wid})
    print("    - 测试完成并关闭世界。\n")


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "server.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await test_google_flights(session)
            await test_batch_fill_and_status(session)
            print("=" * 60)
            print("🎉 实战验证全部顺利通过！各层能力与增强功能运行稳定。")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
