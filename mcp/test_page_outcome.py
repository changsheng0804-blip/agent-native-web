# -*- coding: utf-8 -*-
"""统一后果卡(阶段 A):所有动作的 page_outcome 五态验收。

覆盖:
  - click 正例(弹窗/状态翻转/视觉)/负例(unchanged,FP 一票否决)
  - fill 正例(form/fill_verified)与异常路径(errored 卡而非纯错误文本)
  - press Escape 关闭弹窗(disappear 证据)
  - click_at 坐标点击(区域证据基线)
  - navigate 导航卡(world_epoch+1,target.id=null)
  - batch_fill 聚合卡(progressed)
  - challenge 遮罩:提交→challenged;非提交→不得 challenged

通过线:所有卡片含 channel=outcome/evidence_seq/changes_seq;负例不得误报。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DYN_URI = (FIX / "dyn.html").as_uri()
FAR_URI = (FIX / "far_modal.html").as_uri()
TABS_URI = (FIX / "tabs.html").as_uri()
VISUAL_URI = (FIX / "visual.html").as_uri()
FORM_URI = (FIX / "form_names.html").as_uri()
CHALLENGE_URI = (FIX / "challenge_overlay.html").as_uri()

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


async def find_one(session, world_id, interactive=True, **filters):
    data = await call(session, "world_entities", {"world_id": world_id, **filters, "max_results": 10})
    entities = data.get("entities", [])
    assert entities, f"没有找到目标: {filters}"
    ent = next((e for e in entities if e.get("interactive")), entities[0]) if interactive else entities[0]
    return ent


async def find_by_css_id(session, world_id, css_id):
    """按页面原生 id 找到对应世界构件编号(精确,避免文本包含匹配到祖先节点)。"""
    r = await call(session, "world_eval", {
        "world_id": world_id,
        "expression": f"""() => {{
            const el = document.getElementById({json.dumps(css_id)});
            if (!el) return null;
            for (const e of agentWorld._runtime.world.elements.values()) {{
                if (e._el === el) return e.id;
            }}
            return null;
        }}""",
    })
    res = r.get("result")
    if isinstance(res, str) and res.startswith('"'):
        res = json.loads(res)
    assert res, f"找不到 #{css_id}"
    return res


async def find_by_placeholder(session, world_id, placeholder):
    r = await call(session, "world_eval", {
        "world_id": world_id,
        "expression": f"""() => {{
            const map = {{}};
            for (const e of agentWorld._runtime.world.elements.values()) {{
                if (!e._el) continue;
                const ph = e._el.getAttribute && e._el.getAttribute('placeholder');
                if (ph) map[ph] = e.id;
            }}
            return JSON.stringify(map);
        }}""",
    })
    data = json.loads(r.get("result") or "{}")
    if isinstance(data, str):
        data = json.loads(data)
    assert placeholder in data, f"找不到 placeholder={placeholder}"
    return data[placeholder]


def assert_card(card, expected_outcome=None):
    """统一卡契约:结构完整 + 主标签校验。"""
    assert card.get("channel") == "outcome", f"缺 channel=outcome: {card.get('channel')}"
    assert card.get("page_outcome") in ("progressed", "challenged", "errored", "uncertain", "unchanged"), card.get("page_outcome")
    if card.get("page_outcome") != "errored":
        assert card.get("evidence_seq", 0) >= 1, "evidence_seq 缺失"
    assert "before" in card.get("changes_seq", {}) and "after" in card.get("changes_seq", {}), "changes_seq 不完整"
    assert "world_epoch" in card, "world_epoch 缺失"
    if expected_outcome:
        assert card["page_outcome"] == expected_outcome, f"期望 {expected_outcome},实际 {card['page_outcome']}: {card.get('why')}"


async def open_world(session, uri):
    d = await call(session, "world_open", {"url": uri, "wait_ms": 800})
    return d["world_id"]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. 远距弹窗:click 正例 → progressed ──
            print("\n[1] far_modal click 弹窗按钮 → progressed")
            wid = await open_world(session, FAR_URI)
            btn = await find_one(session, wid, role="button", text="打开居中弹窗")
            card = await call(session, "world_click", {"world_id": wid, "id": btn["id"]})
            assert_card(card, "progressed")
            check("page_outcome=progressed", card["page_outcome"] == "progressed")
            check("situation.type=overlay 或 navigation", card["situation"]["type"] in ("overlay", "navigation"), str(card["situation"]))
            check("effect.verdict=effected", card["effect"].get("verdict") == "effected", str(card["effect"]))
            check("target.id 可回查(el_N)", str(card["target"]["id"]).startswith("el_"), str(card["target"]))
            await call(session, "world_close", {"world_id": wid})

            # ── 2. dyn 负例:click 标题 → unchanged(FP 一票否决)──
            print("\n[2] dyn 负例标题 → unchanged")
            wid = await open_world(session, DYN_URI)
            h1 = await find_one(session, wid, role="heading", text="动态测试页")
            card = await call(session, "world_click", {"world_id": wid, "id": h1["id"]})
            assert_card(card, "unchanged")
            check("page_outcome=unchanged", card["page_outcome"] == "unchanged", card.get("why"))
            await call(session, "world_close", {"world_id": wid})

            # ── 3. far_modal:press Escape 关闭弹窗 → progressed(disappear)──
            print("\n[3] press Escape 关弹窗 → progressed(disappear)")
            wid = await open_world(session, FAR_URI)
            btn = await find_one(session, wid, role="button", text="打开居中弹窗")
            await call(session, "world_click", {"world_id": wid, "id": btn["id"]})
            card = await call(session, "world_press", {"world_id": wid, "id": btn["id"], "key": "Escape"})
            assert_card(card, "progressed")
            check("page_outcome=progressed(关弹窗)", card["page_outcome"] == "progressed", card.get("why"))
            check("observed 含 remove 证据", any(o.get("type") == "remove" for o in card["effect"].get("observed", [])), str(card["effect"].get("observed")))
            await call(session, "world_close", {"world_id": wid})

            # ── 4. tabs:click tab → progressed/state-flip ──
            print("\n[4] tabs 切换 → progressed")
            wid = await open_world(session, TABS_URI)
            tab = await find_one(session, wid, role="tab", text="TAB_B 详情")
            card = await call(session, "world_click", {"world_id": wid, "id": tab["id"]})
            assert_card(card, "progressed")
            check("situation.type=state-flip", card["situation"]["type"] == "state-flip", str(card["situation"]))
            await call(session, "world_close", {"world_id": wid})

            # ── 5. visual:click 变色(visual_evidence)→ progressed ──
            print("\n[5] visual 点击变色 → progressed")
            wid = await open_world(session, VISUAL_URI)
            anim = await find_by_css_id(session, wid, "anim")
            card = await call(session, "world_click", {"world_id": wid, "id": anim, "visual_evidence": True})
            assert_card(card, "progressed")
            check("verdict 生效(effected/visual-effected)", card["effect"]["verdict"] in ("effected", "visual-effected"), card["effect"].get("verdict"))
            # 负例同页:静态块 → unchanged
            st = await find_by_css_id(session, wid, "static")
            card2 = await call(session, "world_click", {"world_id": wid, "id": st})
            assert_card(card2, "unchanged")
            check("静态块负例 unchanged", card2["page_outcome"] == "unchanged", card2.get("why"))
            await call(session, "world_close", {"world_id": wid})

            # ── 6. form_names:fill 按 placeholder → progressed/form ──
            print("\n[6] fill 正例 → progressed")
            wid = await open_world(session, FORM_URI)
            fid = await find_by_placeholder(session, wid, "真实名字")
            card = await call(session, "world_fill", {"world_id": wid, "id": fid, "text": "Alice"})
            assert_card(card, "progressed")
            check("situation.type=form", card["situation"]["type"] == "form", str(card["situation"]))
            check("fill_verified 证据", "填表值" in card.get("why", ""), card.get("why"))
            await call(session, "world_close", {"world_id": wid})

            # ── 7. fill 异常路径 → errored 卡 ──
            print("\n[7] fill 不存在的 id → errored 卡")
            wid = await open_world(session, FORM_URI)
            card = await call(session, "world_fill", {"world_id": wid, "id": "el_99999", "text": "x"})
            assert_card(card, "errored")
            check("返回结构化 errored 卡", card["page_outcome"] == "errored" and "error" in card, str(card.get("error")))
            await call(session, "world_close", {"world_id": wid})

            # ── 8. dyn batch_fill 两字段 → progressed ──
            print("\n[8] batch_fill 两字段 → progressed")
            wid = await open_world(session, DYN_URI)
            uid = await find_by_placeholder(session, wid, "用户名")
            emid = await find_by_placeholder(session, wid, "邮箱")
            card = await call(session, "world_batch_fill", {"world_id": wid, "fields": [
                {"id": uid, "text": "alice"},
                {"id": emid, "text": "a@example.com"},
            ]})
            check("ok_count=2", card.get("ok_count") == 2, str(card.get("results")))
            assert_card(card, "progressed")
            check("聚合卡 progressed", card["page_outcome"] == "progressed", card.get("why"))
            check("action.kind=batch_fill", card["action"]["kind"] == "batch_fill", str(card["action"]))
            await call(session, "world_close", {"world_id": wid})

            # ── 9. far_modal click_at 坐标点击 → 不得 unchanged ──
            print("\n[9] click_at 坐标点角落按钮 → progressed/uncertain")
            wid = await open_world(session, FAR_URI)
            r = await call(session, "world_eval", {"world_id": wid, "expression": """() => {
                const b = document.getElementById('corner-btn').getBoundingClientRect();
                return JSON.stringify({ x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) });
            }"""})
            pt = json.loads(r.get("result") or "{}")
            if isinstance(pt, str):
                pt = json.loads(pt)
            card = await call(session, "world_click_at", {"world_id": wid, "x": pt["x"], "y": pt["y"]})
            assert_card(card)
            check("不得 unchanged(弹窗出现)", card["page_outcome"] in ("progressed", "uncertain", "challenged"), card["page_outcome"])
            await call(session, "world_close", {"world_id": wid})

            # ── 10. navigate → progressed/navigation,epoch+1,target.id=null ──
            print("\n[10] navigate → progressed/navigation")
            wid = await open_world(session, DYN_URI)
            card = await call(session, "world_navigate", {"world_id": wid, "url": FAR_URI})
            assert_card(card, "progressed")
            check("situation.type=navigation", card["situation"]["type"] == "navigation", str(card["situation"]))
            check("world_epoch=1", card["world_epoch"] == 1, str(card["world_epoch"]))
            check("target.id=null(旧 el_N 失效)", card["target"]["id"] is None, str(card["target"]))
            await call(session, "world_close", {"world_id": wid})

            # ── 11. challenge:提交 → challenged ──
            print("\n[11] challenge_overlay 点提交 → challenged")
            wid = await open_world(session, CHALLENGE_URI)
            for ph, val in [("名字", "Alice"), ("邮箱", "a@example.com"), ("密码", "x")]:
                fid_ = await find_by_placeholder(session, wid, ph)
                await call(session, "world_fill", {"world_id": wid, "id": fid_, "text": val})
            sub = await find_one(session, wid, role="button", text="Continue")
            card = await call(session, "world_click", {"world_id": wid, "id": sub["id"]})
            assert_card(card, "challenged")
            check("page_outcome=challenged", card["page_outcome"] == "challenged", card.get("why"))
            check("situation.type=challenge", card["situation"]["type"] in ("challenge", "modal_iframe_challenge"), str(card["situation"]))
            await call(session, "world_close", {"world_id": wid})

            # ── 12. challenge:点非提交装饰 → 不得 challenged ──
            print("\n[12] challenge_overlay 点标题 → 不得 challenged")
            wid = await open_world(session, CHALLENGE_URI)
            h = await find_one(session, wid, role="heading", text="挑战复刻页")
            card = await call(session, "world_click", {"world_id": wid, "id": h["id"]})
            assert_card(card)
            check("不得 challenged(防误报)", card["page_outcome"] != "challenged", card["page_outcome"])
            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())