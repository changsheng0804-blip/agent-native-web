# -*- coding: utf-8 -*-
"""真站信道体检:四条页面信道的真实网站验证(接分支文档第十四节的扩展)。

场景:
  A. Google Flights:world_state 初始 → world_click 乘客(弹窗)→ world_state 看到弹窗
     → world_change_digest 摘要(不含原始事件)→ world_evidence 记录操作
  B. GitHub 仓库:world_guide 找 Pull requests → world_click 进入 → world_state 确认 URL 跳转
     → world_guide 刷新:新 URL + 最近证据

真站测试可能受网络/反爬影响,world_open 或定位失败时输出 SKIP 并退出 0(环境问题);
信道断言失败则退出 1(代码问题)。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
GF_URL = "https://www.google.com/travel/flights"
GH_URL = "https://github.com/git/git"

FAILED = []


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


def check(cond, msg):
    if cond:
        print(f"    ✓ {msg}")
    else:
        FAILED.append(msg)
        print(f"    ✗ {msg}")


async def case_a_gf(session):
    print("A. Google Flights 弹窗闭环")
    try:
        opened = await call(session, "world_open", {"url": GF_URL, "wait_ms": 4500})
    except Exception as e:
        print(f"    SKIP world_open 失败: {type(e).__name__}: {str(e)[:100]}")
        return None
    wid = opened["world_id"]

    st = await call(session, "world_state", {"world_id": wid})
    check(st["channel"] == "page-state", "world_state 返回信道标识")
    check(st["state"]["url"].startswith("https://www.google.com"), f"状态信道 URL: {st['state']['url'][:60]}")
    seq0 = st["state"].get("changes_seq", 0)

    # 定位乘客按钮并点击
    ents = await call(session, "world_entities", {
        "world_id": wid, "name": "passenger", "max_results": 8,
    })
    passenger = next((e for e in ents.get("entities", []) if e.get("interactive")), None)
    if not passenger:
        print("    SKIP 找不到乘客按钮(页面结构变化/反爬)")
        await call(session, "world_close", {"world_id": wid})
        return None
    clicked = await call(session, "world_click", {"world_id": wid, "id": passenger["id"]})
    eff = clicked.get("effect", {})
    fb = clicked.get("feedback", {})
    check(eff.get("verdict") == "effected", f"click effect={eff.get('verdict')}/{eff.get('confidence')}")
    check(fb.get("overlays", {}).get("changed") is True, f"整体反馈检测到覆盖层变化: {json.dumps(fb.get('overlays', {}), ensure_ascii=False)[:120]}")
    check("page" in fb, "click 返回 feedback.page(整体页面反馈)")

    st2 = await call(session, "world_state", {"world_id": wid})
    check(len(st2["state"].get("dialogs", [])) >= 1, f"状态信道看到弹窗: {[d.get('name') for d in st2['state'].get('dialogs', [])][:2]}")
    check(st2["state"].get("changes_seq", 0) >= seq0, "状态信道变化序号推进")

    digest = await call(session, "world_change_digest", {"world_id": wid, "since": seq0})
    check(digest["channel"] == "change-digest", "变化摘要信道标识")
    check("events" not in digest, "变化摘要不含原始事件")
    check("counts" in digest, "变化摘要含 counts")
    check(digest.get("events_seen", 0) > 0, f"摘要看到事件: {digest.get('events_seen')}")

    ev = await call(session, "world_evidence", {"world_id": wid, "since": 0, "limit": 10})
    rows = ev.get("evidence", [])
    check(any(x.get("verdict") == "effected" for x in rows), f"证据信道记录了成功操作({len(rows)} 条)")
    await call(session, "world_close", {"world_id": wid})
    return wid


async def case_b_github(session):
    print("B. GitHub 仓库导览闭环")
    try:
        opened = await call(session, "world_open", {"url": GH_URL, "wait_ms": 4000})
    except Exception as e:
        print(f"    SKIP world_open 失败: {type(e).__name__}: {str(e)[:100]}")
        return None
    wid = opened["world_id"]
    url0 = opened["url"]

    guide = await call(session, "world_guide", {
        "world_id": wid, "task": "进入仓库的 Pull requests 列表页",
        "max_candidates": 8,
    })
    check(guide["channel"] == "task-guide", "world_guide 信道标识")
    check(guide.get("state", {}).get("url", "").startswith("https://github.com"), "导览读到当前 URL")
    check("candidates" in guide and "routes" in guide, "导览返回候选与路径")
    cand = next((c for c in guide.get("candidates", []) if "pull" in (c.get("text") or "").lower() or "pull" in (c.get("name") or "").lower()), None)
    if not cand:
        print("    SKIP 导览未找到 Pull requests 候选(页面结构变化/反爬)")
        await call(session, "world_close", {"world_id": wid})
        return None
    print(f"    导览候选: {cand.get('text') or cand.get('name')} (relation={cand.get('relation')})")

    clicked = await call(session, "world_click", {"world_id": wid, "id": cand["id"]})
    eff = clicked.get("effect", {})
    fb = clicked.get("feedback", {})
    url_after = (fb.get("page", {}) or {}).get("after_url") or ""
    print(f"    click effect={eff.get('verdict')} url: {url0[:40]} → {url_after[:40]}")
    check(eff.get("verdict") == "effected", "点击后 effect=effected")
    check("/pulls" in url_after or "pulls" in url_after.lower(), "整体反馈的 URL 已进入 pulls(全局事实优先于局部)")

    st = await call(session, "world_state", {"world_id": wid})
    check("pulls" in st["state"]["url"].lower(), f"状态信道确认 URL: {st['state']['url'][:60]}")

    # 导览刷新:应看到新 URL + 最近证据
    guide2 = await call(session, "world_guide", {
        "world_id": wid, "task": "确认已进入 Pull requests",
        "change_since": guide.get("next_cursors", {}).get("change_since", 0),
        "evidence_since": guide.get("next_cursors", {}).get("evidence_since", 0),
        "max_candidates": 6,
    })
    check("pulls" in guide2.get("state", {}).get("url", "").lower(), "刷新导览读到新 URL")
    check(guide2.get("recent_evidence"), "刷新导览携带最近操作证据")
    await call(session, "world_close", {"world_id": wid})
    return wid


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            n = 0
            for fn in (case_a_gf, case_b_github):
                r = await fn(session)
                if r is not None:
                    n += 1
            if FAILED:
                print(f"\n❌ 信道真站体检失败 {len(FAILED)} 项(场景完成 {n}/2):")
                for f in FAILED:
                    print(f"  - {f}")
                sys.exit(1)
            if n == 0:
                print("\n⚠️ 两个场景均 SKIP(环境问题),无可断言结果")
                sys.exit(0)
            print(f"\n✅ 信道真站体检通过({n}/2 场景,全部断言成立)")


if __name__ == "__main__":
    asyncio.run(main())