# -*- coding: utf-8 -*-
"""P0-2 一次性校准:视觉 RMS 阈值实测(不进质检流水线,只出数据)。

分级电池(tests/fixtures/visual_calib.html)×3 重复,记录每例 verdict +
visual_diff_raw(全量记录,含阈值下样本),输出候选阈值下的 FP/FN 表。
真值:strong/overlay/subtle/dot=正(确有视觉变化);
     noop/jitter=负(jitter 测区域内持续动画的噪声底)。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "visual_calib.html").as_uri()

CASES = [
    ("strong", "c-strong", True),
    ("overlay", "c-overlay", True),
    ("subtle", "c-subtle", True),
    ("dot", "c-dot", True),
    ("noop", "c-noop", False),
    ("jitter", "c-jitter", False),
]
REPEATS = 3
CANDIDATES = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def css_id(session, wid, cid):
    r = await call(session, "world_eval", {"world_id": wid, "expression": f"""() => {{
        const el = document.getElementById({json.dumps(cid)});
        if (!el) return null;
        for (const e of agentWorld._runtime.world.elements.values()) {{
            if (e._el === el) return e.id;
        }}
        return null;
    }}"""})
    res = r.get("result")
    if isinstance(res, str) and res.startswith('"'):
        res = json.loads(res)
    assert res, f"找不到 #{cid}"
    return res


async def main():
    samples = []  # (case, positive, verdict, raw_or_None)
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            for rnd in range(1, REPEATS + 1):
                for name, cid, pos in CASES:
                    d = await call(session, "world_open", {"url": URI, "wait_ms": 800})
                    wid = d["world_id"]
                    eid = await css_id(session, wid, cid)
                    card = await call(session, "world_click",
                                      {"world_id": wid, "id": eid, "visual_evidence": True})
                    eff = card.get("effect", {})
                    raw = eff.get("visual_diff_raw", eff.get("visual_diff_score"))
                    samples.append((name, pos, eff.get("verdict"), raw))
                    print(f"R{rnd} {name:8s} pos={int(pos)} verdict={eff.get('verdict'):15s} raw={raw}")
                    await call(session, "world_close", {"world_id": wid})

    print("\n===== 原始分分布 =====")
    for name, _, _ in CASES:
        vals = sorted(s[3] for s in samples if s[0] == name and s[3] is not None)
        print(f"{name:8s} raw={vals}")

    print("\n===== 候选阈值 FP/FN(raw>阈值判正;verdict 保持现行 1.5 逻辑) =====")
    print(f"{'阈值':>6} {'FP':>4} {'FN':>4}  错判明细")
    for t in CANDIDATES:
        fp = [(n, v) for n, p, _, v in samples if not p and v is not None and v > t]
        fn = [(n, v) for n, p, _, v in samples if p and (v is None or v <= t)]
        detail = ""
        if fp:
            detail += "FP:" + ",".join(f"{n}({v})" for n, v in fp) + " "
        if fn:
            detail += "FN:" + ",".join(f"{n}({v})" for n, v in fn)
        print(f"{t:>6} {len(fp):>4} {len(fn):>4}  {detail.strip() or '全对'}")


asyncio.run(main())
