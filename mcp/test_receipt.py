# -*- coding: utf-8 -*-
"""小票标准 v0.1(R1 验收):全字段齐全性 / 来源标记 / handoff / 对账。

依据: docs/小票标准-page_receipt-v0.1.md §2/§7。
覆盖:
  1. world_act fill 正例 → 全字段齐全 + sources 映射( Rag: target.name=untrusted,
     effect.verdict=evidence, situation.type=inference)
  2. world_act click 负例 → unchanged(不得谎报)
  3. 旧工具 world_click 提交 → challenged + handoff.required/resume_condition
  4. world_act 非法 id → errored 结构卡(非纯错误文本)
  5. world_navigate → target.id=null + world_epoch+1
  6. world_outcome 幂等重读一致 + since=evidence_seq 返回 none 卡

通过线:缺一字段即失败;challenged 无 handoff 即失败;基线 test_page_outcome 不退化。
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
FORM_URI = (FIX / "form_names.html").as_uri()
DYN_URI = (FIX / "dyn.html").as_uri()
CHALLENGE_URI = (FIX / "challenge_overlay.html").as_uri()
TABS_URI = (FIX / "tabs.html").as_uri()

PASS = 0
FAIL = 0

REQUIRED = ["world_id", "channel", "page_outcome", "situation", "confidence",
            "why", "target", "action", "effect", "page", "overlays",
            "sources", "next", "evidence_seq", "changes_seq",
            "world_epoch", "status"]


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


def assert_receipt(card, label=""):
    missing = [k for k in REQUIRED if k not in card]
    check(f"{label}全字段齐全", not missing, f"缺: {missing}")
    check(f"{label}channel=outcome", card.get("channel") == "outcome", card.get("channel"))
    check(f"{label}五态合法",
          card.get("page_outcome") in ("progressed", "challenged", "errored", "uncertain", "unchanged"),
          card.get("page_outcome"))
    check(f"{label}evidence_seq为整数", isinstance(card.get("evidence_seq"), int),
          card.get("evidence_seq"))
    cs = card.get("changes_seq") or {}
    check(f"{label}changes_seq成对", "before" in cs and "after" in cs, cs)


def assert_sources(card, label=""):
    src = card.get("sources") or {}
    check(f"{label}target.name恒untrusted", src.get("target.name") == "untrusted",
          json.dumps(src, ensure_ascii=False)[:300])
    check(f"{label}effect.verdict为evidence", src.get("effect.verdict") == "evidence",
          src.get("effect.verdict"))
    check(f"{label}situation.type为inference", src.get("situation.type") == "inference",
          src.get("situation.type"))


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. fill 正例 → progressed 全字段 + sources ──
            print("\n[1] world_act fill → progressed 全字段")
            d = await call(session, "world_open", {"url": FORM_URI, "wait_ms": 800})
            wid = d["world_id"]
            f = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
            ms = f.get("matches", [])
            tid = next((m["id"] for m in ms if m.get("interactive")), ms[0]["id"])
            card = await call(session, "world_act",
                              {"world_id": wid, "kind": "fill", "id": tid, "text": "alice"})
            assert_receipt(card, "fill卡")
            assert_sources(card, "fill卡")
            check("fill卡progressed", card["page_outcome"] == "progressed", card.get("page_outcome"))
            seq = card["evidence_seq"]

            # ── 6a. world_outcome 幂等重读一致 ──
            print("\n[2] world_outcome 幂等重读")
            out = await call(session, "world_outcome", {"world_id": wid})
            check("重读同序号", out.get("evidence_seq") == seq,
                  f"{out.get('evidence_seq')} vs {seq}")
            check("重读同主标签", out.get("page_outcome") == card["page_outcome"],
                  out.get("page_outcome"))
            none_card = await call(session, "world_outcome", {"world_id": wid, "since": seq})
            check("since无新卡回none", none_card.get("page_outcome") == "none",
                  none_card.get("page_outcome"))
            await call(session, "world_close", {"world_id": wid})

            # ── 2. click 负例 → unchanged ──
            print("\n[3] world_act click 负例 → unchanged")
            d = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = d["world_id"]
            ents = await call(session, "world_entities",
                              {"world_id": wid, "role": "heading",
                               "text": "动态测试页", "max_results": 5})
            es = ents.get("entities", [])
            assert es, "夹具缺动态测试页标题"
            card = await call(session, "world_act",
                              {"world_id": wid, "kind": "click", "id": es[0]["id"]})
            assert_receipt(card, "负例卡")
            check("负例unchanged不谎报", card["page_outcome"] == "unchanged",
                  card.get("page_outcome"))
            await call(session, "world_close", {"world_id": wid})

            # ── 3. 提交 → challenged + handoff ──
            print("\n[4] world_click 提交 → challenged + handoff")
            d = await call(session, "world_open", {"url": CHALLENGE_URI, "wait_ms": 1200})
            wid = d["world_id"]
            ents = await call(session, "world_entities",
                              {"world_id": wid, "role": "button", "max_results": 10})
            btn = next((e for e in ents.get("entities", [])
                        if "continue" in (e.get("text") or "").lower()), None)
            assert btn, "找不到 Continue 按钮"
            card = await call(session, "world_click", {"world_id": wid, "id": btn["id"]})
            assert_receipt(card, "挑战卡")
            assert_sources(card, "挑战卡")
            check("挑战卡challenged", card["page_outcome"] == "challenged",
                  card.get("page_outcome"))
            ho = card.get("handoff") or {}
            check("handoff.required=true", ho.get("required") is True, ho)
            check("handoff有恢复条件", bool(ho.get("resume_condition")),
                  ho.get("resume_condition"))
            await call(session, "world_close", {"world_id": wid})

            # ── 4. 非法 id → errored 结构卡 ──
            print("\n[5] world_act 非法 id → errored")
            d = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = d["world_id"]
            card = await call(session, "world_act",
                              {"world_id": wid, "kind": "click", "id": "el_99999"})
            check("errored结构卡", card.get("page_outcome") == "errored",
                  card.get("page_outcome"))
            check("errored带evidence_seq", isinstance(card.get("evidence_seq"), int),
                  card.get("evidence_seq"))
            await call(session, "world_close", {"world_id": wid})

            # ── 5. navigate → epoch+1 + target.id=null ──
            print("\n[6] world_navigate → epoch 失效语义")
            d = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = d["world_id"]
            before = (await call(session, "world_outcome", {"world_id": wid})).get("world_epoch", 0)
            card = await call(session, "world_navigate", {"world_id": wid, "url": TABS_URI})
            check("导航progressed", card.get("page_outcome") == "progressed",
                  card.get("page_outcome"))
            check("target.id置空", (card.get("target") or {}).get("id") is None,
                  card.get("target"))
            check("world_epoch+1", card.get("world_epoch") == before + 1,
                  f"{card.get('world_epoch')} vs {before}+1")
            await call(session, "world_close", {"world_id": wid})

            # ── 7. steps 全成功 → 整单 progressed + 聚合记账 ──
            print("\n[7] world_act steps 两填表 → 整单 progressed")
            d = await call(session, "world_open", {"url": FORM_URI, "wait_ms": 800})
            wid = d["world_id"]
            f1 = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
            f2 = await call(session, "world_find", {"world_id": wid, "q": "邮箱"})
            id1 = next((m["id"] for m in f1.get("matches", []) if m.get("interactive")))
            id2 = next((m["id"] for m in f2.get("matches", []) if m.get("interactive")))
            card = await call(session, "world_act", {"world_id": wid, "steps": [
                {"kind": "fill", "id": id1, "text": "alice"},
                {"kind": "fill", "id": id2, "text": "a@example.com"},
            ]})
            check("整单progressed", card.get("page_outcome") == "progressed",
                  card.get("page_outcome"))
            check("step_count=2", card.get("step_count") == 2, card.get("step_count"))
            check("all_progressed=true", card.get("all_progressed") is True,
                  card.get("all_progressed"))
            check("first_failure_idx=null", card.get("first_failure_idx") is None,
                  card.get("first_failure_idx"))
            check("step_outcomes全progressed",
                  card.get("step_outcomes") == ["progressed", "progressed"],
                  card.get("step_outcomes"))
            seqs = [c.get("evidence_seq") for c in card.get("steps", [])]
            check("子卡序号严格递增", seqs == sorted(seqs) and len(set(seqs)) == len(seqs),
                  seqs)
            check("seq_range闭合", card.get("seq_range") == {"first": seqs[0], "last": seqs[-1]},
                  card.get("seq_range"))
            await call(session, "world_close", {"world_id": wid})

            # ── 8. steps 部分失败 → 整单 errored + 首败下标 ──
            print("\n[8] world_act steps 一成一坏 → 整单 errored")
            d = await call(session, "world_open", {"url": FORM_URI, "wait_ms": 800})
            wid = d["world_id"]
            f1 = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
            id1 = next((m["id"] for m in f1.get("matches", []) if m.get("interactive")))
            card = await call(session, "world_act", {"world_id": wid, "steps": [
                {"kind": "fill", "id": id1, "text": "alice"},
                {"kind": "click", "id": "el_99999"},
            ]})
            check("整单errored", card.get("page_outcome") == "errored",
                  card.get("page_outcome"))
            check("step_count=2(坏步也记)", card.get("step_count") == 2,
                  card.get("step_count"))
            check("all_progressed=false", card.get("all_progressed") is False,
                  card.get("all_progressed"))
            check("first_failure_idx=1", card.get("first_failure_idx") == 1,
                  card.get("first_failure_idx"))
            check("step_outcomes记录部分成功",
                  card.get("step_outcomes") == ["progressed", "errored"],
                  card.get("step_outcomes"))
            # errored 子卡序号不得与成功子卡重复(对账黑洞已堵)
            seqs = [c.get("evidence_seq") for c in card.get("steps", [])]
            check("坏步序号独立不重复", len(set(seqs)) == len(seqs), seqs)
            await call(session, "world_close", {"world_id": wid})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())
