# -*- coding: utf-8 -*-
"""最小验证2:动作驱动的 runtime 流——GF 上执行真实操作,观察流增量。

核心演示:
  1. 动作(填表/点击)产生哪些数据请求(接口 URL + 时序)
  2. JSON 接口响应的摘要(数据形态,如航班价格)
  3. "前提绑定"雏形:流事件 vs DOM 变化的时间先后(数据先到,渲染在后)
"""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
URL = "https://www.google.com/travel/flights"


def norm_url(url):
    try:
        p = urlsplit(str(url))
        return f"{p.netloc}{p.path or '/'}"[:160]
    except Exception:
        return str(url)[:160]


def main():
    events = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def ev(e):
            events.append(e)

        page.on("request", lambda r: ev({
            "t": round(time.time() * 1000), "type": "request",
            "url": norm_url(r.url), "method": r.method, "rtype": r.resource_type,
        }))
        page.on("response", lambda r: ev({
            "t": round(time.time() * 1000), "type": "response",
            "url": norm_url(r.url), "status": r.status,
            "ctype": str(r.headers.get("content-type", ""))[:60],
            "body_hint": _body_hint(r),
        }))
        page.on("requestfailed", lambda r: ev({
            "t": round(time.time() * 1000), "type": "failed", "url": norm_url(r.url),
            "error": (r.failure or "")[:100],
        }))

        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        t_base = time.time() * 1000
        events.append({"t": round(t_base), "type": "marker", "label": "PAGE_READY"})

        # 动作1:点乘客按钮
        try:
            page.click('[aria-label*="passenger" i], [aria-label*="Passenger"]', timeout=8000)
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": "CLICK_PASSENGERS"})
            page.keyboard.press("Escape")  # 关掉弹出的对话框
        except Exception as e:
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": f"CLICK_PASSENGERS_FAIL {str(e)[:60]}"})
        page.wait_for_timeout(1500)

        # 动作2:点出发地输入框并输入 Tokyo(用 placeholder 定位)
        try:
            page.click('input[placeholder*="Where from"]', timeout=8000)
            page.keyboard.type("Tokyo", delay=60)
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": "TYPE_TOKYO"})
        except Exception as e:
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": f"TYPE_TOKYO_FAIL {str(e)[:60]}"})
        page.wait_for_timeout(2500)
        # 选择第一个建议
        try:
            page.click('[role="option"]:has-text("Tokyo")', timeout=5000)
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": "PICK_SUGGESTION"})
        except Exception:
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": "NO_SUGGESTION"})
        page.wait_for_timeout(1500)

        # 动作3:目的地 Osaka
        try:
            page.click('input[placeholder*="Where to"]', timeout=8000)
            page.keyboard.type("Osaka", delay=60)
            page.wait_for_timeout(2500)
            page.click('[role="option"]:has-text("Osaka")', timeout=5000)
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": "DEST_OSAKA"})
        except Exception as e:
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": f"DEST_FAIL {str(e)[:60]}"})
        page.wait_for_timeout(1500)

        # 动作4:点搜索按钮
        try:
            page.click('[aria-label*="Search"], button:has-text("Search")', timeout=8000)
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": "CLICK_SEARCH"})
        except Exception as e:
            events.append({"t": round(time.time() * 1000), "type": "marker", "label": f"CLICK_SEARCH_FAIL {str(e)[:60]}"})

        # 观察搜索结果数据流 25 秒
        page.wait_for_timeout(25000)
        browser.close()

    # ── 分析 ──
    reqs = [e for e in events if e["type"] == "request"]
    resps = [e for e in events if e["type"] == "response"]
    markers = [e for e in events if e["type"] == "marker"]
    print(f"事件总数: {len(events)} (请求 {len(reqs)} / 响应 {len(resps)} / 标记 {len(markers)})")
    print("\n标记时间线:")
    for m in markers:
        print(f"  +{(m['t'] - t_base) / 1000:6.1f}s {m['label']}")

    print("\n动作后的数据请求(标记之后的新请求, XHR/fetch):")
    first_marker = markers[1]["t"] if len(markers) > 1 else t_base
    post = [e for e in reqs if e["t"] >= first_marker and e.get("rtype") in ("xhr", "fetch")]
    seen = {}
    for e in post:
        key = e["url"]
        seen.setdefault(key, []).append(round((e["t"] - t_base) / 1000, 1))
    for url, ts in sorted(seen.items()):
        print(f"  {url}")
        print(f"    at {ts}")

    print("\nJSON 响应摘要(动作后):")
    api_hits = 0
    for e in resps:
        if e["t"] < first_marker or "json" not in e.get("ctype", ""):
            continue
        api_hits += 1
        print(f"  +{(e['t'] - t_base) / 1000:6.1f}s {e['url']}")
        if e.get("body_hint"):
            print(f"    body: {e['body_hint'][:180]}")
    print(f"\n动作后 JSON 接口响应数: {api_hits}")

    out = ARTIFACTS / "runtime_stream_actions.json"
    out.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n原始事件已存: {out}")


def _body_hint(resp):
    """对 JSON 响应提取摘要(价格/数字字段为主,截断保护)。"""
    try:
        ct = str(resp.headers.get("content-type", "")).lower()
        if "json" not in ct:
            return None
        text = resp.text()
        if not text or len(text) > 400000:
            return {"size": len(text or "")}
        data = json.loads(text)
        # 递归找价格/金额字段
        hits = []
        def walk(obj, path=""):
            if len(hits) >= 5:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        walk(v, f"{path}.{k}")
                    elif isinstance(v, (int, float)) and 10 <= abs(v) <= 100000 and any(
                            s in k.lower() for s in ("price", "amount", "value", "cost", "fare", "total")):
                        hits.append(f"{path}.{k}={v}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj[:20]):
                    walk(v, f"{path}[{i}]")
        walk(data)
        return {"size": len(text), "price_fields": hits[:5]}
    except Exception:
        return None


if __name__ == "__main__":
    main()
