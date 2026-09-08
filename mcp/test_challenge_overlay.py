# -*- coding: utf-8 -*-
"""挑战复刻夹具验证:表单提交后出现 fixed 遮罩+iframe(仿 GitLab Arkose)。

验收基线(2026-09-08 实测锁定):
  1. 挑战遮罩是否被感知(fixed 全屏遮罩 + 内部 iframe)
  2. 提交动作必须判 page_outcome=challenged 且带 handoff(resume_condition)
  3. 非提交动作(点标题)不得误判 challenged —— 防误报

这是 page_outcome challenged 的守护场景:挑战检测一旦回归,本测试必须变红。
"""
import asyncio, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "challenge_overlay.html").as_uri()

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def open_world(session):
    d = await call(session, "world_open", {"url": URI, "wait_ms": 1500})
    return d["world_id"]


async def fill_signup(session, wid):
    """用 name 属性定位并填入三个字段(验证 name 定位在本页也有效)。"""
    r = await call(session, "world_eval", {"world_id": wid, "expression": """() => {
        const map = {}; for (const e of agentWorld._runtime.world.elements.values()) {
            if (!e._el) continue; const nm = e._el.getAttribute && e._el.getAttribute('name');
            if (nm && /^new_user\\[/.test(nm)) map[nm] = e.id; } return JSON.stringify(map); }"""})
    fields = json.loads(r.get("result") or "{}")
    if isinstance(fields, str):
        fields = json.loads(fields)
    vals = {"new_user[first_name]": "Alice", "new_user[email]": "a@example.com", "new_user[password]": "x"}
    outcomes = []
    for nm, text in vals.items():
        if nm in fields:
            rr = await call(session, "world_act", {"world_id": wid, "kind": "fill", "id": fields[nm], "text": text})
            outcomes.append(rr.get("page_outcome"))
    return fields, outcomes


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ===== 场景 1:提交 → 必须 challenged + handoff =====
            wid = await open_world(session)
            fields, fills = await fill_signup(session, wid)
            check("三个表单字段定位到", len(fields) == 3, f"fields={fields}")
            check("填表全部 progressed", fills and all(x == "progressed" for x in fills), f"fills={fills}")

            btns = await call(session, "world_entities", {"world_id": wid, "role": "button", "max_results": 10})
            btn = next((e for e in btns.get("entities", []) if "continue" in (e.get("text") or "").lower()), None)
            check("找到 Continue 按钮", btn is not None)

            rc = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn["id"]})
            po = rc.get("page_outcome")
            sit = rc.get("situation") or {}
            handoff = rc.get("handoff") or {}

            check("提交后 page_outcome=challenged", po == "challenged", f"page_outcome={po}")
            check("situation 指向挑战遮罩", sit.get("type") == "modal_iframe_challenge",
                  f"situation={json.dumps(sit, ensure_ascii=False)[:120]}")
            check("challenged 必带 handoff.required=true", handoff.get("required") is True,
                  f"handoff={json.dumps(handoff, ensure_ascii=False)[:120]}")
            check("handoff 带 resume_condition", bool(handoff.get("resume_condition")),
                  f"resume_condition={handoff.get('resume_condition')}")

            # 世界状态应能感知遮罩:挑战遮罩是普通 div(非 role=dialog),
            # 因此正确信号是 task.intervention(而非 dialogs,后者只收 role=dialog/aria-modal)。
            st = await call(session, "world_state", {"world_id": wid})
            task = st.get("task") or {}
            intervention = task.get("intervention") or {}
            check("world_state 报告人工介入要求", intervention.get("required") is True,
                  f"intervention={json.dumps(intervention, ensure_ascii=False)}")
            check("介入类型为 human_challenge", intervention.get("type") == "human_challenge",
                  f"type={intervention.get('type')}")
            check("任务状态进入等待人工", task.get("status") == "waiting_user",
                  f"status={task.get('status')}")

            await call(session, "world_close", {"world_id": wid})

            # ===== 场景 2:非提交动作 → 不得误判 challenged =====
            wid2 = await open_world(session)
            f = await call(session, "world_find", {"world_id": wid2, "q": "注册"})
            matches = f.get("matches") or []
            check("负例:找到非提交目标", bool(matches), f"matches={len(matches)}")
            if matches:
                rc2 = await call(session, "world_act", {"world_id": wid2, "kind": "click", "id": matches[0]["id"]})
                check("负例:非提交点击不得 challenged", rc2.get("page_outcome") != "challenged",
                      f"page_outcome={rc2.get('page_outcome')}")
            await call(session, "world_close", {"world_id": wid2})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
