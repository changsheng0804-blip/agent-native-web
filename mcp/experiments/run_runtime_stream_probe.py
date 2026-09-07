# -*- coding: utf-8 -*-
"""最小验证:真实站点 runtime 流态信息捕获与分析。

阶段1(采集):playwright 直接打开真实站点,挂全量监听器收集 45 秒完整流:
  request / response / requestfailed / console
阶段2(分析):事件量分布、轮询模式识别、数据接口识别、错误序列、压缩比演示。

回答三个问题:
  A. runtime 流真实可捕获吗?捕获到什么?
  B. 会爆炸吗?(原始事件量)
  C. 能压缩成对 agent 有价值的模式吗?(轮询节奏/数据接口/错误 → 少数几条)

运行: python mcp/experiments/run_runtime_stream_probe.py
"""
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
SITES = [
    ("google-flights", "https://www.google.com/travel/flights", 45),
    ("hacker-news", "https://news.ycombinator.com/", 30),
]
CAP = 2000  # 每站点原始事件上限(防意外爆炸)


def norm_url(url):
    """URL 归一化:去 query/hash,只留 host+path,便于聚合。"""
    try:
        p = urlsplit(str(url))
        return f"{p.netloc}{p.path or '/'}"[:160]
    except Exception:
        return str(url)[:160]


def collect_site(name, url, seconds):
    from playwright.sync_api import sync_playwright

    events = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def ev(e):
            if len(events) < CAP:
                events.append(e)

        page.on("request", lambda r: ev({
            "t": round(time.time() * 1000), "type": "request",
            "url": norm_url(r.url), "method": r.method,
            "rtype": r.resource_type,
        }))
        page.on("response", lambda r: ev({
            "t": round(time.time() * 1000), "type": "response",
            "url": norm_url(r.url), "status": r.status,
            "ctype": str(r.headers.get("content-type", ""))[:60],
        }))
        page.on("requestfailed", lambda r: ev({
            "t": round(time.time() * 1000), "type": "requestfailed",
            "url": norm_url(r.url),
            "error": (r.failure or "")[:120],
        }))
        page.on("console", lambda m: ev({
            "t": round(time.time() * 1000), "type": "console",
            "level": m.type, "text": m.text[:150],
        }))
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        t0 = time.time()
        while time.time() - t0 < seconds and len(events) < CAP:
            page.wait_for_timeout(500)
        browser.close()
    return events


def analyze(name, events):
    out = {"site": name, "total": len(events)}
    by_type = Counter(e["type"] for e in events)
    out["by_type"] = dict(by_type)

    # 1. 轮询模式识别:同一 URL 的请求间隔稳定(CV < 0.3 且 >= 2 次) → 轮询
    reqs = defaultdict(list)
    for e in events:
        if e["type"] == "request" and e.get("rtype") in ("xhr", "fetch"):
            reqs[e["url"]].append(e["t"])
    polls = []
    for url, ts in reqs.items():
        if len(ts) < 2:
            continue
        gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue
        mean = sum(gaps) / len(gaps)
        var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
        cv = (var ** 0.5) / mean if mean else 0
        if cv < 0.3 and mean < 60000:
            polls.append({"url": url, "interval_s": round(mean / 1000, 1),
                          "count": len(ts), "cv": round(cv, 2)})
    out["poll_patterns"] = polls

    # 2. 数据接口识别:JSON 响应的 XHR/fetch
    api_urls = set()
    for e in events:
        if e["type"] == "response" and "json" in e.get("ctype", ""):
            api_urls.add(e["url"])
    out["api_endpoints"] = sorted(api_urls)[:20]

    # 3. 错误序列
    errors = [{"t": e["t"], "url": e["url"], "status": e.get("status"), "error": e.get("error")}
              for e in events if e["type"] in ("requestfailed",) or (e["type"] == "response" and e.get("status", 0) >= 400)]
    out["errors"] = errors[:10]
    out["error_count"] = len(errors)

    # 4. console 摘要
    cons = [e for e in events if e["type"] == "console"]
    out["console"] = {"count": len(cons),
                      "levels": dict(Counter(c["level"] for c in cons)),
                      "samples": [c["text"] for c in cons[:5]]}

    # 5. 压缩比演示:原始条数 vs 模式摘要条数
    summary_rows = len(polls) + len(api_urls) + min(len(errors), 10) + min(len(cons), 5)
    out["compression"] = {"raw": len(events), "pattern_rows": summary_rows,
                          "ratio": round(len(events) / max(summary_rows, 1), 1)}
    return out


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    results = []
    for name, url, seconds in SITES:
        print(f"\n===== 采集 {name} ({url}) {seconds}s =====", flush=True)
        events = collect_site(name, url, seconds)
        print(f"采集到 {len(events)} 条事件", flush=True)
        analysis = analyze(name, events)
        results.append(analysis)
        print(json.dumps(analysis, ensure_ascii=False, indent=1)[:2500], flush=True)
    out = ARTIFACTS / "runtime_stream_probe.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
