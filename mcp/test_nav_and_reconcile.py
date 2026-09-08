# -*- coding: utf-8 -*-
"""主线 A 回归守护:两条已复现的假绿/对账缺陷。

A1 纯 hash 片段变化不得判成导航(progressed/navigation)。
   缺陷:URL 整串比较把 `/page` → `/page#tab2` 当导航,agent 会误以为已跳转。
   夹具:fixtures/hash_anchor.html

A2 异常路径的 errored 卡必须能被 world_outcome 重读(对账黑洞)。
   缺陷:_errored_card 已 mint evidence_seq 但不写缓存,world_outcome 返回 none,
        弱模型以为"什么都没发生"并重复执行。
"""
import asyncio, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
HASH_URI = (FIX / "hash_anchor.html").as_uri()
DYN_URI = (FIX / "dyn.html").as_uri()

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


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ===== A1:纯 hash 锚点点击 =====
            print("[A1] 纯 hash 片段变化不得判导航")
            d = await call(session, "world_open", {"url": HASH_URI, "wait_ms": 1200})
            wid = d["world_id"]
            f = await call(session, "world_find", {"world_id": wid, "q": "跳到第二段"})
            matches = f.get("matches") or []
            check("A1 找到 hash 锚点链接", bool(matches), f"matches={len(matches)}")
            if matches:
                card = await call(session, "world_act", {"world_id": wid, "kind": "click", "id": matches[0]["id"]})
                po = card.get("page_outcome")
                sit = (card.get("situation") or {}).get("type")
                page = card.get("page") or {}
                check("A1 不得判 progressed", po != "progressed", f"page_outcome={po}")
                check("A1 situation 不得为 navigation", sit != "navigation", f"situation={sit}")
                check("A1 page.url_changed 应为 false", page.get("url_changed") is False,
                      f"url_changed={page.get('url_changed')} before={page.get('before_url')} after={page.get('after_url')}")
            await call(session, "world_close", {"world_id": wid})

            # ===== A2:errored 卡进对账 =====
            print("\n[A2] errored 卡必须可被 world_outcome 重读")
            d2 = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 1000})
            wid2 = d2["world_id"]
            ents = await call(session, "world_entities", {"world_id": wid2, "max_results": 10})
            eid = (ents.get("entities") or [{}])[0].get("id")
            if eid:
                ok = await call(session, "world_click", {"world_id": wid2, "id": eid})
                check("A2 先产生一张正常卡", ok.get("page_outcome") is not None,
                      f"page_outcome={ok.get('page_outcome')}")

            bad = await call(session, "world_click", {"world_id": wid2, "id": "el_99999"})
            check("A2 无效 id 返回 errored 卡", bad.get("page_outcome") == "errored",
                  f"page_outcome={bad.get('page_outcome')}")
            bad_seq = bad.get("evidence_seq")

            after = await call(session, "world_outcome", {"world_id": wid2, "since": 0})
            check("A2 world_outcome 能读到该 errored 卡",
                  after.get("page_outcome") == "errored",
                  f"world_outcome={after.get('page_outcome')} (seq={after.get('evidence_seq')}, 期望 {bad_seq})")
            check("A2 序号一致(不是上一张成功卡)", after.get("evidence_seq") == bad_seq,
                  f"after_seq={after.get('evidence_seq')} errored_seq={bad_seq}")
            await call(session, "world_close", {"world_id": wid2})

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
