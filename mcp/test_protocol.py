# -*- coding: utf-8 -*-
"""阶段 B 收口:默认 6 词协议验收(open → guide → find → act → outcome → close)。

覆盖:
  1. list_tools:规范 6 词置前,旧工具描述带 [内部/调试] 前缀;LITE 模式只暴露 6 词
  2. world_find:q 解析 / 角色过滤 / ambiguous 标记;禁止执行动作
  3. world_act:单步 click/fill → 统一后果卡;steps 聚合执行(等价 world_run);任一步 errored 即停
  4. world_outcome:幂等读最近一张卡;since 增量语义
  5. 负例纪律:无副作用点击 → unchanged(FP 一票否决)
  6. 全程只用 6 词,不调用 world_click/world_state
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FAR_URI = (FIX / "far_modal.html").as_uri()
DYN_URI = (FIX / "dyn.html").as_uri()
CHALLENGE_URI = (FIX / "challenge_overlay.html").as_uri()
CANONICAL = ["world_open", "world_guide", "world_find", "world_act", "world_outcome", "world_close"]

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


async def open_world(session, uri):
    d = await call(session, "world_open", {"url": uri, "wait_ms": 800})
    return d["world_id"]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. 工具清单:规范 6 词置前 + 旧工具标记 ──
            print("\n[1] list_tools 收口")
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            check("总工具数 25", len(names) == 25, str(len(names)))
            check("前 6 个是规范词", names[:6] == CANONICAL, str(names[:6]))
            check("旧工具描述带 [内部/调试]", all(t.description.startswith("[内部/调试]") for t in tools.tools if t.name not in CANONICAL))
            check("规范词不带前缀", all(not t.description.startswith("[内部/调试]") for t in tools.tools if t.name in CANONICAL))

            # ── 2. far_modal:find → act(click)→ outcome ──
            print("\n[2] 6 词主环:open → find → act → outcome")
            wid = await open_world(session, FAR_URI)
            f = await call(session, "world_find", {"world_id": wid, "q": "打开居中弹窗"})
            check("find(q) 命中按钮", len(f["matches"]) == 1 and f["matches"][0]["interactive"], str(f["matches"]))
            check("不 ambiguous", f["ambiguous"] is False)
            btn = f["matches"][0]
            card = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn["id"]})
            check("act(click) → progressed", card["page_outcome"] == "progressed", card.get("why"))
            check("act 返回统一卡", card["channel"] == "outcome" and card["evidence_seq"] >= 1)
            # Phase 2 Diff-First:默认轻量 status(无 frames/forms/world 明细)
            check("默认轻量 status(light)", card["status"].get("light") is True and "frames" not in card["status"], str(list(card["status"].keys())))
            out1 = await call(session, "world_outcome", {"world_id": wid})
            check("outcome 幂等=progressed", out1["page_outcome"] == "progressed" and out1["evidence_seq"] == card["evidence_seq"], str(out1.get("page_outcome")))
            out2 = await call(session, "world_outcome", {"world_id": wid, "since": 999999})
            check("outcome(since=新) → none 卡", out2["page_outcome"] == "none", str(out2.get("page_outcome")))

            # 负例:find 标题 → act click → unchanged(失败态自动全量深诊断)
            h = await call(session, "world_find", {"world_id": wid, "role": "heading", "text": "Far Modal Test"})
            check("find(role=heading) 命中", len(h["matches"]) >= 1, str(h["matches"]))
            card2 = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": h["matches"][0]["id"]})
            check("负例 act → unchanged", card2["page_outcome"] == "unchanged", card2.get("why"))
            check("unchanged 自动全量深诊断(含 frames)", card2["status"].get("light") is None and "frames" in card2["status"], str(list(card2["status"].keys())))
            # verbose=true 强制全量(即使 outcome 是 progressed 也带 frames)
            card_verbose = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn["id"], "verbose": True})
            check("verbose=true 全量 status(含 frames)", card_verbose["status"].get("light") is None and "frames" in card_verbose["status"], str(list(card_verbose["status"].keys())))
            await call(session, "world_close", {"world_id": wid})

            # ── 3. dyn:act steps 聚合执行(等价 world_run)──
            print("\n[3] world_act steps 聚合")
            wid = await open_world(session, DYN_URI)
            f1 = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
            f2 = await call(session, "world_find", {"world_id": wid, "q": "邮箱"})
            uid = next((m["id"] for m in f1["matches"] if m.get("interactive")), None)
            emid = next((m["id"] for m in f2["matches"] if m.get("interactive")), None)
            assert uid and emid, "dyn 输入框未找到"
            agg = await call(session, "world_act", {"world_id": wid, "steps": [
                {"kind": "fill", "id": uid, "text": "alice"},
                {"kind": "fill", "id": emid, "text": "a@example.com"},
            ]})
            check("聚合卡 page_outcome=progressed", agg["page_outcome"] == "progressed", agg.get("why"))
            check("step_count=2", agg.get("step_count") == 2, str(agg.get("step_count")))
            check("每步都是统一卡", all(s.get("channel") == "outcome" and s.get("evidence_seq", 0) >= 1 for s in agg.get("steps", [])), str([s.get("page_outcome") for s in agg.get("steps", [])]))
            check("action.kind=act-sequence", agg["action"]["kind"] == "act-sequence", str(agg["action"]))

            # steps 失败:任一步 errored 即停
            agg_bad = await call(session, "world_act", {"world_id": wid, "steps": [
                {"kind": "fill", "id": uid, "text": "x"},
                {"kind": "fill", "id": "el_99999", "text": "y"},
            ]})
            check("聚合失败 → errored 且停在第 2 步", agg_bad["page_outcome"] == "errored" and agg_bad.get("step_count") == 2, str(agg_bad.get("step_count")))
            await call(session, "world_close", {"world_id": wid})

            # ── 4. challenge:steps 聚合触发 challenged ──
            print("\n[4] steps 聚合 → challenged")
            wid = await open_world(session, CHALLENGE_URI)
            fields = {}
            for ph in ("名字", "邮箱", "密码"):
                r = await call(session, "world_find", {"world_id": wid, "q": ph})
                matches = [m for m in r["matches"] if m.get("interactive")]
                assert matches, f"占位符 {ph} 未找到"
                fields[ph] = matches[0]["id"]
            sub = await call(session, "world_find", {"world_id": wid, "role": "button", "text": "Continue"})
            sub_id = next(m["id"] for m in sub["matches"] if m.get("interactive"))
            agg3 = await call(session, "world_act", {"world_id": wid, "steps": [
                {"kind": "fill", "id": fields["名字"], "text": "Alice"},
                {"kind": "fill", "id": fields["邮箱"], "text": "a@example.com"},
                {"kind": "fill", "id": fields["密码"], "text": "x"},
                {"kind": "click", "id": sub_id},
            ]})
            check("聚合提交 → challenged", agg3["page_outcome"] == "challenged", agg3.get("why"))
            await call(session, "world_close", {"world_id": wid})

    # ── 5. LITE 模式:只暴露 6 词,旧工具被拒 ──
    print("\n[5] AGENT_WORLD_LITE=1")
    lite_env = dict(os.environ)
    lite_env["AGENT_WORLD_LITE"] = "1"
    try:
        lite_params = StdioServerParameters(command=sys.executable, args=[SERVER], env=lite_env)
    except TypeError:
        lite_params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(lite_params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            check("LITE 只暴露 6 词", names == sorted(CANONICAL), str(names))
            d = await call(session, "world_open", {"url": FAR_URI, "wait_ms": 600})
            wid = d["world_id"]
            f = await call(session, "world_find", {"world_id": wid, "q": "打开居中弹窗"})
            btn = f["matches"][0]
            card = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": btn["id"]})
            check("LITE 下 6 词可用(act→progressed)", card["page_outcome"] == "progressed", card.get("why"))
            r = await session.call_tool("world_click", {"world_id": wid, "id": btn["id"]})
            txt = r.content[0].text
            check("LITE 下旧工具被拒", "只开放 6 个默认工具" in txt or "内部/调试" in txt, txt[:120])
            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())