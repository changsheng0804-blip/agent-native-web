# -*- coding: utf-8 -*-
"""三个独立可读取信道的最小验证。

页面状态: world_state
变化摘要: world_change_digest
操作证据: world_evidence
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
    data = await call(session, "world_entities", {
        "world_id": world_id, **filters, "interactive": True, "max_results": 8,
    })
    assert data.get("entities"), f"没有找到目标: {filters}"
    return data["entities"][0]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = opened["world_id"]

            state = await call(session, "world_state", {"world_id": wid})
            assert state["channel"] == "page-state"
            assert "status" not in state, "独立状态信道不应再附加大 status"
            assert state["state"]["url"].startswith("file:")
            print("1.页面状态信道: 只返回当前状态")

            digest = await call(session, "world_change_digest", {"world_id": wid, "since": 0})
            assert digest["channel"] == "change-digest"
            assert "events" not in digest, "变化摘要不应返回原始事件列表"
            assert "events_seen" in digest and "counts" in digest
            print("2.变化摘要信道: 返回摘要，不返回原始事件")

            initial_evidence = await call(session, "world_evidence", {"world_id": wid, "since": 0})
            assert initial_evidence["channel"] == "operation-evidence"
            assert initial_evidence["evidence"] == []

            # 先记录一个真正的 no-change 动作。
            heading = await call(session, "world_entities", {
                "world_id": wid, "role": "heading", "text": "动态测试页", "max_results": 4,
            })
            assert heading.get("entities"), "没有找到动态测试页标题"
            no_change = await call(session, "world_click", {"world_id": wid, "id": heading["entities"][0]["id"]})
            assert no_change["effect"]["verdict"] == "no-change"

            # 再验证独立证据信道能记录 URL 跳转。
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": """() => {
                    const a = document.createElement('a');
                    a.id = 'channel-nav';
                    a.href = '#channel-target';
                    a.textContent = '信道跳转';
                    document.body.appendChild(a);
                    return true;
                }""",
            })
            await call(session, "world_wait", {
                "world_id": wid, "mode": "appear", "text": "信道跳转", "timeout_ms": 5000,
            })
            nav = await find_one(session, wid, text="信道跳转")
            navigated = await call(session, "world_click", {"world_id": wid, "id": nav["id"]})
            assert navigated["effect"]["verdict"] == "effected"
            assert navigated["feedback"]["page"]["url_changed"] is True

            # 最后验证操作后出现的弹窗也进入证据信道。
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": """() => {
                    const b = document.createElement('button');
                    b.id = 'channel-popup';
                    b.textContent = '信道弹窗';
                    document.body.appendChild(b);
                    b.addEventListener('click', () => {
                        const d = document.createElement('div');
                        d.id = 'channel-dialog';
                        d.setAttribute('role', 'dialog');
                        d.textContent = '信道弹窗内容';
                        d.style.cssText = 'position:fixed;left:800px;top:20px;width:200px;height:100px;background:white;border:2px solid black;z-index:9999;';
                        document.body.appendChild(d);
                    });
                    return true;
                }""",
            })
            await call(session, "world_wait", {
                "world_id": wid, "mode": "appear", "text": "信道弹窗", "timeout_ms": 5000,
            })
            popup_button = await find_one(session, wid, text="信道弹窗")
            popup = await call(session, "world_click", {"world_id": wid, "id": popup_button["id"]})
            assert popup["effect"]["verdict"] == "effected"

            evidence = await call(session, "world_evidence", {"world_id": wid, "since": 0, "limit": 10})
            assert len(evidence["evidence"]) >= 3
            rows = evidence["evidence"]
            assert any(x["verdict"] == "no-change" for x in rows)
            assert any(x["transition"]["url_changed"] for x in rows)
            assert any(x["transition"]["new_overlays"] for x in rows)
            print("3.操作证据信道: 独立记录无变化、跳转和弹窗")

            latest = await call(session, "world_state", {"world_id": wid})
            assert latest["state"]["url"].endswith("#channel-target")
            await call(session, "world_close", {"world_id": wid})
            print("\n✅ 三条独立信道通过:状态、变化摘要、操作证据互不混入整页 status")


if __name__ == "__main__":
    asyncio.run(main())
