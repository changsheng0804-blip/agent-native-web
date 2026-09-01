# -*- coding: utf-8 -*-
"""最小闭环验证:页面整体反馈优先于点击目标附近的局部变化。

覆盖三种情况:
1. 点击无副作用标题 -> no-change
2. 点击链接改变 URL -> effected/high + url_changed
3. 点击按钮出现远处弹窗 -> effected/high + new_overlays
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


async def find_one(session, world_id, **filters):
    data = await call(session, "world_entities", {"world_id": world_id, **filters, "max_results": 8})
    entities = [e for e in data.get("entities", []) if e.get("interactive")]
    assert entities, f"没有找到交互目标: {filters}"
    return entities[0]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            data = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = data["world_id"]

            # 1. 局部和整体都没有变化
            heading = await call(session, "world_entities", {
                "world_id": wid, "role": "heading", "text": "动态测试页", "max_results": 4,
            })
            heading_id = heading["entities"][0]["id"]
            no_change = await call(session, "world_click", {"world_id": wid, "id": heading_id})
            assert no_change["effect"]["verdict"] == "no-change"
            assert no_change["feedback"]["page"]["url_changed"] is False
            assert no_change["feedback"]["overlays"]["changed"] is False
            print("1.无副作用标题: no-change, 全局信号一致")

            # 2. 点击目标附近没有关键构件,但 URL 发生变化
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": """() => {
                    const a = document.createElement('a');
                    a.id = 'global-feedback-nav';
                    a.href = '#global-feedback-target';
                    a.textContent = '全局跳转测试';
                    a.style.display = 'block';
                    document.body.appendChild(a);
                    return true;
                }""",
            })
            await call(session, "world_wait", {
                "world_id": wid, "mode": "appear", "text": "全局跳转测试", "timeout_ms": 5000,
            })
            nav = await find_one(session, wid, text="全局跳转测试")
            navigated = await call(session, "world_click", {"world_id": wid, "id": nav["id"]})
            assert navigated["effect"]["verdict"] == "effected"
            assert navigated["effect"]["confidence"] == "high"
            assert navigated["feedback"]["page"]["url_changed"] is True
            assert navigated["feedback"]["page"]["after_url"].endswith("#global-feedback-target")
            print("2.URL 跳转: effected/high, 捕获整体 URL 变化")

            # 3. 弹窗出现在目标区域之外
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": """() => {
                    const b = document.createElement('button');
                    b.id = 'global-feedback-popup';
                    b.textContent = '打开反馈弹窗';
                    document.body.appendChild(b);
                    b.addEventListener('click', () => {
                        const d = document.createElement('div');
                        d.id = 'global-feedback-dialog';
                        d.setAttribute('role', 'dialog');
                        d.textContent = '全局反馈弹窗';
                        d.style.cssText = 'position:fixed;left:900px;top:20px;width:240px;height:100px;background:white;border:2px solid black;z-index:9999;';
                        document.body.appendChild(d);
                    });
                    return true;
                }""",
            })
            await call(session, "world_wait", {
                "world_id": wid, "mode": "appear", "text": "打开反馈弹窗", "timeout_ms": 5000,
            })
            popup_button = await find_one(session, wid, text="打开反馈弹窗")
            popup = await call(session, "world_click", {"world_id": wid, "id": popup_button["id"]})
            assert popup["effect"]["verdict"] == "effected"
            assert popup["effect"]["confidence"] == "high"
            assert popup["feedback"]["overlays"]["changed"] is True
            assert any("dialog" in (x.get("name") or "") for x in popup["feedback"]["overlays"]["new"])
            print("3.远处弹窗: effected/high, 捕获整体覆盖层变化")

            await call(session, "world_close", {"world_id": wid})
            print("\n✅ 最小闭环通过:无变化、URL 跳转、远处弹窗均有整体反馈")


if __name__ == "__main__":
    asyncio.run(main())
