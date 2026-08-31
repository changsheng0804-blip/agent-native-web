# -*- coding: utf-8 -*-
"""真网站稳定指纹验证:同一站点两次 world_open,指纹可重算性/命中率/撞车率
目标:验证 fingerprint 在真实动态站点(懒加载/随机class/重渲染)上的认路价值
指标:
  - 指纹命中率:第一次拿到的指纹,第二次按 fingerprint 查询能否命中(越高越好)
  - 核心控件命中率:仅 interactive 元素(按钮/链接/输入框)的命中率(更重要)
  - 撞车:一个指纹对应多个元素(应趋近 0)
注意:部分站点反爬/重定向导致注入失败,每站独立容错(SKIP 不拖垮整体)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")

SITES = [
    ("wiki", "https://en.wikipedia.org/wiki/Web_Model_Context_Protocol", 3500),
    ("baidu", "https://www.baidu.com/", 3000),
    ("amazon", "https://www.amazon.com/", 4000),
    ("bbc", "https://www.bbc.com/", 4000),
    ("github", "https://github.com/git/git", 4000),
    ("gf", "https://www.google.com/travel/flights", 4500),
]

SAMPLE = 80  # 每站采样指纹数(逐指纹查询较慢,采样即可评估)


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def collect(session, url, wait_ms):
    """打开站点,收集元素指纹清单。返回 {wid, rows} 或 None(打开失败)"""
    try:
        r = await call(session, "world_open", {"url": url, "wait_ms": wait_ms})
        wid = r["world_id"]
        rr = await call(session, "world_entities", {"world_id": wid, "maxResults": 300})
        rows = []
        for e in rr.get("entities", []):
            fp = e.get("fingerprint")
            if fp:
                rows.append({"id": e["id"], "fingerprint": fp,
                             "semantic": e.get("semantic"), "interactive": e.get("interactive", False),
                             "name": e.get("name", "")})
        return {"wid": wid, "rows": rows}
    except Exception as e:
        try:
            if "wid" in locals() and wid:
                await call(session, "world_close", {"world_id": wid})
        except Exception:
            pass
        return None


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            print("=== 真网站稳定指纹验证 ===", flush=True)
            print(f"{'站点':<10s} {'首次元素':>6s} {'采样':>4s} {'命中':>4s} {'命中率':>6s} {'核心命中':>8s} {'撞车':>4s}", flush=True)
            summary = []
            for name, url, wait in SITES:
                # 第一次进站
                c1 = await collect(session, url, wait)
                if not c1 or not c1["rows"]:
                    print(f"{name:<10s} [SKIP] 首次进站失败/无指纹", flush=True)
                    continue
                # 第二次进站
                c2 = await collect(session, url, wait)
                if not c2:
                    print(f"{name:<10s} [SKIP] 二次进站失败", flush=True)
                    continue
                # 采样(去重指纹,优先 interactive)
                rows = c1["rows"]
                uniq = {}
                for item in rows:
                    uniq.setdefault(item["fingerprint"], item)
                sampled = list(uniq.values())
                sampled.sort(key=lambda x: (not x["interactive"]))  # 核心控件优先
                sampled = sampled[:SAMPLE]
                # 逐指纹在第二次世界查询
                hit = 0
                hit_core = 0
                core_total = sum(1 for s in sampled if s["interactive"])
                dup = 0
                for item in sampled:
                    try:
                        rr = await call(session, "world_entities", {"world_id": c2["wid"], "fingerprint": item["fingerprint"], "maxResults": 5})
                        n = len(rr.get("entities", []))
                    except Exception:
                        n = 0
                    if n == 1:
                        hit += 1
                        if item["interactive"]:
                            hit_core += 1
                    elif n > 1:
                        dup += 1
                rate = hit / len(sampled) * 100 if sampled else 0
                core_rate = hit_core / core_total * 100 if core_total else 0
                print(f"{name:<10s} {len(rows):>6d} {len(sampled):>4d} {hit:>4d} {rate:>5.0f}% {core_rate:>7.0f}% {dup:>4d}", flush=True)
                summary.append({"name": name, "rate": rate, "core": core_rate, "dup": dup})
                await call(session, "world_close", {"world_id": c2["wid"]})

            print("\n=== 汇总 ===", flush=True)
            if not summary:
                print("全部站点失败,无数据")
                return
            avg_rate = sum(s["rate"] for s in summary) / len(summary)
            avg_core = sum(s["core"] for s in summary) / len(summary)
            tot_dup = sum(s["dup"] for s in summary)
            print(f"站点均命中率 {avg_rate:.0f}%, 核心控件均命中率 {avg_core:.0f}%, 总撞车 {tot_dup}", flush=True)
            if avg_core >= 70 and tot_dup <= 5:
                print("✅ 真站指纹可用:核心控件命中率 ≥70% 且撞车少", flush=True)
            else:
                print("⚠️ 真站指纹有短板:核心命中率偏低或撞车多,需针对性优化", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
