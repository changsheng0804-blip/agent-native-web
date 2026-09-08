# -*- coding: utf-8 -*-
"""Agent-Native Canvas 概念验证。

目标不是证明模型能力，而是证明：一个没有图形 DOM 结构的像素 Canvas，
如果环境自己提供 observe / act / verify 世界接口，就能形成可测量的闭环：
观察 → 行动 → 验证偏差 → 按反馈修正 → 再验证。
"""
import asyncio
import json
import math
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(encoding="utf-8")

SERVER = str(Path(__file__).resolve().parent / "server.py")
CANVAS_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "agent_native_canvas.html").as_uri()


async def call(session, name, args, timeout=30):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(result.content[0].text)


async def canvas_eval(session, wid, expression):
    outer = await call(session, "world_eval", {"world_id": wid, "expression": expression})
    assert not outer.get("pending"), f"Canvas 页面不应处于 pending: {outer}"
    return json.loads(outer["result"])


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            opened = await call(session, "world_open", {"url": CANVAS_URI, "wait_ms": 300})
            wid = opened["world_id"]

            try:
                # 1) 像素画布本身没有 circle/star 等 DOM 图形节点。
                dom_shapes = await canvas_eval(
                    session,
                    wid,
                    "() => ({canvasChildren: document.querySelector('#world').children.length, "
                    "semanticShapeNodes: document.querySelectorAll('circle, star, [data-shape]').length})",
                )
                assert dom_shapes == {"canvasChildren": 0, "semanticShapeNodes": 0}, dom_shapes

                initial = await canvas_eval(session, wid, "() => agentCanvas.observe()")
                assert initial["kind"] == "agent-native-canvas"
                assert initial["revision"] == 0
                assert initial["objects"] == []
                initial_hash = initial["visual_hash"]
                print(f"1. 初始世界: revision=0 visual_hash={initial_hash}, 图形 DOM=0")

                # 2) 先故意画一个正确的蓝圆 + 一个偏离中心的黄星。
                circle = await canvas_eval(
                    session,
                    wid,
                    "() => agentCanvas.act({kind:'create_circle', id:'circle_1', "
                    "center:[320,240], radius:140, fill:'#3366ff'})",
                )
                star = await canvas_eval(
                    session,
                    wid,
                    "() => agentCanvas.act({kind:'create_star', id:'star_1', "
                    "center:[350,210], radius:70, points:5, fill:'#ffd700'})",
                )
                assert circle["outcome"] == "progressed", circle
                assert star["outcome"] == "progressed", star
                assert circle["visual_hash_after"] != initial_hash
                assert star["visual_hash_after"] != circle["visual_hash_after"]
                print("2. 已创建蓝圆与故意偏心的黄星，两个动作均产生真实像素变化")

                # 3) 环境给出机器可读的几何偏差，而不是要求 Agent 从截图猜坐标。
                target = {
                    "circle_id": "circle_1",
                    "star_id": "star_1",
                    "circle_fill": "#3366ff",
                    "star_fill": "#ffd700",
                    "center_tolerance_px": 0,
                }
                spec_json = json.dumps(target, ensure_ascii=False)
                before_fix = await canvas_eval(
                    session, wid, f"() => agentCanvas.verify({spec_json})"
                )
                assert before_fix["matched"] is False, before_fix
                assert before_fix["center_delta"] == [30, -30], before_fix
                assert math.isclose(before_fix["center_error_px"], math.hypot(30, 30), rel_tol=1e-9)
                assert before_fix["contained"] is True
                print(
                    "3. 结构化反馈发现偏差: "
                    f"center_delta={before_fix['center_delta']} "
                    f"error={before_fix['center_error_px']:.2f}px"
                )

                # 4) 不预先写死正确坐标；直接使用 verify 返回的偏差计算修正动作。
                dx, dy = before_fix["center_delta"]
                correction = await canvas_eval(
                    session,
                    wid,
                    f"() => agentCanvas.act({{kind:'move', id:'star_1', dx:{-dx}, dy:{-dy}}})",
                )
                assert correction["outcome"] == "progressed", correction

                after_fix = await canvas_eval(
                    session, wid, f"() => agentCanvas.verify({spec_json})"
                )
                assert after_fix["matched"] is True, after_fix
                assert after_fix["center_delta"] == [0, 0], after_fix
                assert after_fix["center_error_px"] == 0
                assert after_fix["contained"] is True
                assert after_fix["visual_hash"] != before_fix["visual_hash"]
                print("4. 按反馈自动修正后: matched=true center_error=0")

                # 5) 世界状态与 outcome 本身也可验证：无效动作不应伪报 progressed。
                no_op = await canvas_eval(
                    session, wid, "() => agentCanvas.act({kind:'move', id:'star_1', dx:0, dy:0})"
                )
                bad = await canvas_eval(
                    session, wid, "() => agentCanvas.act({kind:'move', id:'missing', dx:1, dy:1})"
                )
                assert no_op["outcome"] == "unchanged", no_op
                assert bad["outcome"] == "errored", bad

                final_state = await canvas_eval(session, wid, "() => agentCanvas.observe()")
                assert final_state["revision"] == 3, final_state
                assert len(final_state["objects"]) == 2
                assert final_state["relations"]["star_to_circle_center_delta"] == [0, 0]
                assert final_state["relations"]["star_inside_circle"] is True
                print("5. outcome 守恒: no-op=unchanged, 非法对象=errored；最终世界状态一致")
                print("Agent-Native Canvas 闭环概念验证通过")
            finally:
                await call(session, "world_close", {"world_id": wid})


if __name__ == "__main__":
    asyncio.run(main())
