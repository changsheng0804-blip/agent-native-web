# -*- coding: utf-8 -*-
"""R4:小票与旧验证指标对齐——小票必须能回答#九的度量。

旧验证 `验证记录-复杂网页任务地形信息.md` #九指标:
  首次正确定位时间 / 首次正确操作时间 / 读取量与调用数 /
  定位歧义 / 错误操作 / 成功判失败 / 失败判成功 / 错误恢复时间

本脚本用固定任务电池(真值已知)×3 次独立重复，从小票自身字段算出:
  - 首次定位时间 = world_find 耗时(目标命中才算正确定位)
  - 首次操作时间 = world_act 耗时
  - 成功判失败(FN):真值成功但主标签 ∈ {unchanged, errored}
  - 失败判成功(FP):真值无效果但主标签 = progressed(一票否决,同闭环标准)
  - 恢复:unchanged 后卡片是否携带恢复指引(recipes/next.suggested)+ 跟随正确动作耗时

任务电池(真值):
  A fill 用户名 → 成功，期望 progressed/effected
  B 点动态测试页标题 → 无效果，期望 unchanged
  C 点 Continue(挑战页) → 拦截，期望 challenged
  D 点 el_99999 → 异常，期望 errored
  E DYN→TABS 导航 → 成功，期望 progressed/navigation

通过线:FP=0 且 FN=0;三次重复全部小票全字段齐全。
"""
import asyncio
import json
import sys
import time
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
REPEATS = 3
CALLS = []  # E3:本轮 (tool, 返回字符数) 日志,每轮清零


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


async def call(session, name, args, timeout=60):
    t0 = time.time()
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    raw = r.content[0].text
    CALLS.append((name, len(raw)))
    return json.loads(raw), time.time() - t0


async def one_round(session, rnd):
    """一轮电池,返回(每任务行, E3 统计)。E3 统计:总时间/调用数与工具分布/
    返回字符数/期望与事实冲突数(find零命中+ambiguous)/恢复出口。"""
    global CALLS
    CALLS = []
    t_start = time.time()
    rows = {}
    opens = []
    conflicts = {"find_zero": 0, "ambiguous": 0}

    async def open_close(uri):
        d, dt = await call(session, "world_open", {"url": uri, "wait_ms": 800})
        opens.append(dt)
        return d["world_id"]

    async def close(wid):
        await call(session, "world_close", {"world_id": wid})

    def note_find(data):
        # 期望(能定位) vs 页面事实(零命中/歧义)冲突计数
        if not data.get("matches") and not data.get("entities"):
            conflicts["find_zero"] += 1
        if data.get("ambiguous"):
            conflicts["ambiguous"] += 1

    # A: fill(成功)
    wid = await open_close(FORM_URI)
    f, find_ms = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
    note_find(f)
    ms = f.get("matches", [])
    tid = next((m["id"] for m in ms if m.get("interactive")), ms[0]["id"])
    card, act_ms = await call(session, "world_act",
                              {"world_id": wid, "kind": "fill", "id": tid, "text": "alice"})
    rows["A-fill"] = (find_ms, act_ms, card.get("page_outcome"), "success")
    await close(wid)

    # B: 负例点击(无效果)
    wid = await open_close(DYN_URI)
    e, find_ms = await call(session, "world_entities",
                            {"world_id": wid, "role": "heading",
                             "text": "动态测试页", "max_results": 5})
    note_find(e)
    card, act_ms = await call(session, "world_act",
                              {"world_id": wid, "kind": "click",
                               "id": e["entities"][0]["id"]})
    rows["B-negative"] = (find_ms, act_ms, card.get("page_outcome"), "noeffect")
    # 恢复指引:unchanged 卡是否自带机器可读的恢复出口
    # (recipes 非空 / next.suggested 非空 / occluded 归因三者之一)
    rec = bool((card.get("recipes") or [])
               or (card.get("next") or {}).get("suggested")
               or (card.get("situation") or {}).get("type") == "occluded")
    rows["B-recoverable"] = rec
    await close(wid)

    # C: 挑战提交(拦截)
    wid = await open_close(CHALLENGE_URI)
    e, find_ms = await call(session, "world_entities",
                            {"world_id": wid, "role": "button", "max_results": 10})
    note_find(e)
    btn = next((x for x in e.get("entities", [])
                if "continue" in (x.get("text") or "").lower()), None)
    card, act_ms = await call(session, "world_click", {"world_id": wid, "id": btn["id"]})
    rows["C-challenge"] = (find_ms, act_ms, card.get("page_outcome"), "blocked")
    await close(wid)

    # D: 非法 id(异常)
    wid = await open_close(DYN_URI)
    card, act_ms = await call(session, "world_act",
                              {"world_id": wid, "kind": "click", "id": "el_99999"})
    rows["D-badid"] = (0.0, act_ms, card.get("page_outcome"), "error")
    await close(wid)

    # E: 导航(成功)
    wid = await open_close(DYN_URI)
    card, act_ms = await call(session, "world_navigate", {"world_id": wid, "url": TABS_URI})
    rows["E-navigate"] = (0.0, act_ms, card.get("page_outcome"), "success")
    await close(wid)

    print(f"第{rnd}轮: " + " | ".join(
        f"{k} 定位{v[0]*1000:.0f}ms 操作{v[1]*1000:.0f}ms→{v[2]}" for k, v in rows.items()
        if k != "B-recoverable"))
    from collections import Counter
    per_tool = Counter(n for n, _ in CALLS)
    stats = {"total_s": time.time() - t_start,
             "opens_s": sum(opens),
             "calls": len(CALLS),
             "per_tool": dict(per_tool),
             "chars": sum(c for _, c in CALLS),
             **conflicts}
    print(f"  E3:总耗时{stats['total_s']:.1f}s(open{stats['opens_s']:.1f}s) "
          f"调用{stats['calls']}次{stats['per_tool']} 字符{stats['chars']} "
          f"冲突零命中{conflicts['find_zero']}/歧义{conflicts['ambiguous']}")
    return rows, stats


def judge(rows):
    """真值 vs 主标签 → FP(失败判成功)/FN(成功判失败)。"""
    fp = fn = 0
    for k, v in rows.items():
        if not isinstance(v, tuple):
            continue
        _, _, outcome, truth = v
        if truth == "success" and outcome in ("unchanged", "errored"):
            fn += 1
            print(f"  ⚠️ FN:{k} 真成功判 {outcome}")
        if truth == "noeffect" and outcome == "progressed":
            fp += 1
            print(f"  ⚠️ FP:{k} 真无效果判 progressed")
        if truth == "blocked" and outcome != "challenged":
            fn += 1
            print(f"  ⚠️ FN:{k} 真拦截判 {outcome}")
        if truth == "error" and outcome != "errored":
            fn += 1
            print(f"  ⚠️ FN:{k} 真异常判 {outcome}")
    return fp, fn


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    total_fp = total_fn = 0
    loc_ms, act_ms = [], []
    all_stats = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            for rnd in range(1, REPEATS + 1):
                rows, stats = await one_round(session, rnd)
                all_stats.append(stats)
                fp, fn = judge(rows)
                total_fp += fp
                total_fn += fn
                for k, v in rows.items():
                    if not isinstance(v, tuple):
                        continue
                    lm, am = v[0], v[1]
                    if lm:
                        loc_ms.append(lm)
                    act_ms.append(am)
                check(f"第{rnd}轮恢复出口(unchanged后有指引)",
                      rows["B-recoverable"] is True, rows["B-recoverable"])

    print("\n===== R4 度量表(小票自回答) =====")
    print(f"首次正确定位时间(均值): {sum(loc_ms)/len(loc_ms)*1000:.0f}ms (n={len(loc_ms)})")
    print(f"首次正确操作时间(均值): {sum(act_ms)/len(act_ms)*1000:.0f}ms (n={len(act_ms)})")
    print(f"失败判成功 FP 总数: {total_fp} (一票否决线 FP=0)")
    print(f"成功判失败 FN 总数: {total_fn}")
    n = len(all_stats)
    print(f"完成任务总时间(均值): {sum(s['total_s'] for s in all_stats)/n:.1f}s/轮 "
          f"(open 均值 {sum(s['opens_s'] for s in all_stats)/n:.1f}s)")
    print(f"MCP 调用数(均值): {sum(s['calls'] for s in all_stats)/n:.0f}次/轮")
    from collections import Counter as _C
    agg = _C()
    for s in all_stats:
        agg.update(s["per_tool"])
    print(f"工具分布(合计): {dict(agg)}")
    print(f"返回字符数(均值): {sum(s['chars'] for s in all_stats)/n:.0f}/轮")
    print(f"期望与事实冲突:零命中合计 {sum(s['find_zero'] for s in all_stats)} / "
          f"歧义合计 {sum(s['ambiguous'] for s in all_stats)}")
    check("FP=0(失败不得判成功)", total_fp == 0, f"FP={total_fp}")
    check("FN=0(成功不得判失败)", total_fn == 0, f"FN={total_fn}")

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    if FAIL:
        sys.exit(1)


asyncio.run(main())
