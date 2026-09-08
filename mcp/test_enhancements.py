# -*- coding: utf-8 -*-
"""进阶增强功能验证:
1. world_fill 支持 type_delay_ms (逐字打字 locator-sequential-type)
2. world_batch_fill 批量填表 (单次交互填充多个字段,逐字段容错)
3. world_click 遮挡检测与状态感知
4. batch_fill 不得把单字段未生效误聚合为 progressed
5. Agent-Native Canvas: 无图形 DOM 的像素画布可借助环境原生状态/动作/验证接口闭环修正
"""
import asyncio
import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
DYN_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "dyn.html").as_uri()
REJECT_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fill_reject.html").as_uri()
CANVAS_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "agent_native_canvas.html").as_uri()


async def call(session, name, args, timeout=30):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def canvas_eval(session, wid, expression):
    """实验阶段仅把 world_eval 当 transport；真正被验证的是页面自己的 world 接口。"""
    outer = await call(session, "world_eval", {"world_id": wid, "expression": expression})
    assert not outer.get("pending"), f"Canvas 页面不应处于 pending: {outer}"
    return json.loads(outer["result"])


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

            # 5. 回归:单字段 fill 未生效时,batch_fill 不得因为调用未抛异常就标记成功。
            rr = await call(session, "world_open", {"url": REJECT_URI, "wait_ms": 500})
            reject_wid = rr["world_id"]
            single = await call(session, "world_fill", {
                "world_id": reject_wid,
                "id": "input.拒绝输入",
                "text": "never-sticks",
            })
            print(f"5a. 单字段拒绝输入: page_outcome={single.get('page_outcome')} method={single.get('method')}")
            assert single.get("page_outcome") != "progressed", (
                "回归夹具必须先证明单字段没有真正生效;若这里 progressed,请先检查夹具/后果卡判定"
            )

            batch = await call(session, "world_batch_fill", {
                "world_id": reject_wid,
                "fields": [
                    {"id": "input.拒绝输入", "text": "never-sticks"},
                ],
            })
            print(f"5b. 批量拒绝输入: page_outcome={batch.get('page_outcome')} ok_count={batch.get('ok_count')}")
            assert batch.get("page_outcome") != "progressed", (
                "batch_fill 不得把单字段非 progressed 结果聚合为 progressed"
            )
            assert batch.get("ok_count") == 0, "未生效字段不得计入 ok_count"
            await call(session, "world_close", {"world_id": reject_wid})

            # 6. Agent-Native Canvas 概念验证。
            # 图形只存在于 Canvas 像素中，没有 circle/star DOM 节点；页面另行提供
            # observe / act / verify 世界接口，使 Agent 能拿到精确偏差并闭环修正。
            cr = await call(session, "world_open", {"url": CANVAS_URI, "wait_ms": 300})
            canvas_wid = cr["world_id"]
            try:
                dom_shapes = await canvas_eval(
                    session,
                    canvas_wid,
                    "() => ({canvasChildren: document.querySelector('#world').children.length, "
                    "semanticShapeNodes: document.querySelectorAll('circle, star, [data-shape]').length})",
                )
                assert dom_shapes == {"canvasChildren": 0, "semanticShapeNodes": 0}, dom_shapes

                initial = await canvas_eval(session, canvas_wid, "() => agentCanvas.observe()")
                assert initial["revision"] == 0 and initial["objects"] == [], initial
                initial_hash = initial["visual_hash"]

                circle = await canvas_eval(
                    session,
                    canvas_wid,
                    "() => agentCanvas.act({kind:'create_circle', id:'circle_1', "
                    "center:[320,240], radius:140, fill:'#3366ff'})",
                )
                star = await canvas_eval(
                    session,
                    canvas_wid,
                    "() => agentCanvas.act({kind:'create_star', id:'star_1', "
                    "center:[350,210], radius:70, points:5, fill:'#ffd700'})",
                )
                assert circle["outcome"] == "progressed", circle
                assert star["outcome"] == "progressed", star
                assert circle["visual_hash_after"] != initial_hash
                assert star["visual_hash_after"] != circle["visual_hash_after"]

                target = {
                    "circle_id": "circle_1",
                    "star_id": "star_1",
                    "circle_fill": "#3366ff",
                    "star_fill": "#ffd700",
                    "center_tolerance_px": 0,
                }
                spec_json = json.dumps(target, ensure_ascii=False)
                before_fix = await canvas_eval(
                    session, canvas_wid, f"() => agentCanvas.verify({spec_json})"
                )
                assert before_fix["matched"] is False, before_fix
                assert before_fix["center_delta"] == [30, -30], before_fix
                assert math.isclose(before_fix["center_error_px"], math.hypot(30, 30), rel_tol=1e-9)
                assert before_fix["contained"] is True

                # 关键点：修正量直接来自环境反馈，不在测试里预先写死目标坐标。
                dx, dy = before_fix["center_delta"]
                correction = await canvas_eval(
                    session,
                    canvas_wid,
                    f"() => agentCanvas.act({{kind:'move', id:'star_1', dx:{-dx}, dy:{-dy}}})",
                )
                assert correction["outcome"] == "progressed", correction

                after_fix = await canvas_eval(
                    session, canvas_wid, f"() => agentCanvas.verify({spec_json})"
                )
                assert after_fix["matched"] is True, after_fix
                assert after_fix["center_delta"] == [0, 0]
                assert after_fix["center_error_px"] == 0
                assert after_fix["contained"] is True
                assert after_fix["visual_hash"] != before_fix["visual_hash"]

                no_op = await canvas_eval(
                    session, canvas_wid,
                    "() => agentCanvas.act({kind:'move', id:'star_1', dx:0, dy:0})",
                )
                bad = await canvas_eval(
                    session, canvas_wid,
                    "() => agentCanvas.act({kind:'move', id:'missing', dx:1, dy:1})",
                )
                assert no_op["outcome"] == "unchanged", no_op
                assert bad["outcome"] == "errored", bad

                final_state = await canvas_eval(session, canvas_wid, "() => agentCanvas.observe()")
                assert final_state["revision"] == 3, final_state
                assert len(final_state["objects"]) == 2
                assert final_state["relations"]["star_to_circle_center_delta"] == [0, 0]
                assert final_state["relations"]["star_inside_circle"] is True
                print(
                    "6. Agent-Native Canvas: 图形DOM=0, "
                    f"偏差 {before_fix['center_delta']} → [0, 0], "
                    "no-op=unchanged, invalid=errored"
                )
            finally:
                await call(session, "world_close", {"world_id": canvas_wid})

            # 7. 点击 + 遮挡诊断(本地页无遮挡,应无 obscured_note)
            r = await call(session, "world_click", {"world_id": wid, "id": "button.搜索"})
            print(f"7. 点击按钮: method={r.get('method')}, clicked={r.get('clicked')}, obscured_note={r.get('obscured_note')}")

            await call(session, "world_close", {"world_id": wid})
            print("8. 测试全部通过!")


if __name__ == "__main__":
    asyncio.run(main())
