# -*- coding: utf-8 -*-
"""关键测试:真实站点 runtime 流 → 接口绑定 → 变化检测 → 通知(全流程拉通)。

场景:NodeBits shops(真实电商聚合站)
  1. 捕获 JSON 接口流,识别数据接口(/api/shops 等)
  2. 绑定 /api/shops 响应指纹(world_assume 绑定接口的等价实现)
  3. 点击排序控件(默认→价格)→ 服务端返回新数据 → 指纹变化 → 通知
  4. 量化:接口响应到达 vs DOM 更新的时间差(数据先到,渲染在后)
"""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
URL = "https://nodebits.xyz/shops"


def norm_url(url):
    try:
        p = urlsplit(str(url))
        return f"{p.netloc}{p.path or '/'}"[:150]
    except Exception:
        return str(url)[:150]


def find_prices(obj, path="", hits=None, depth=0):
    if hits is None:
        hits = []
    if depth > 7 or len(hits) >= 10:
        return hits
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (dict, list)):
                find_prices(v, f"{path}.{k}", hits, depth + 1)
            elif isinstance(v, (int, float)) and 10 <= abs(v) <= 10000000 and any(
                    s in kl for s in ("price", "amount", "cost", "total", "value", "sales", "stock", "fee")):
                hits.append({"field": f"{path}.{k}", "value": v})
            elif isinstance(v, str) and "¥" in v and len(v) < 30:
                hits.append({"field": f"{path}.{k}", "value": v})
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:40]):
            find_prices(v, f"{path}[{i}]", hits, depth + 1)
    return hits


def fingerprint(data):
    """接口响应指纹:数值字段 + 条目 id 顺序(变化检测的依据)。"""
    items = data if isinstance(data, list) else next(
        (v for v in data.values() if isinstance(v, list)), [])
    nums = []
    ids = []
    for it in items[:25]:
        if isinstance(it, dict):
            for k, v in it.items():
                if isinstance(v, (int, float)):
                    nums.append(f"{k}={v}")
            if it.get("id"):
                ids.append(str(it.get("id"))[:12])
    return {"nums": nums[:15], "ids": ids[:15], "count": len(items)}


def main():
    log = []          # 全流程事件日志
    bound = None      # 绑定的接口
    last_fp = None
    notices = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # DOM 变化观察者:记录商品列表区域的 DOM 变化时刻(与接口到达时刻对比)
        page.add_init_script("""
            window.__domChanges = [];
            window.__watchDom = () => {
                const targets = [...document.querySelectorAll('main, #__next, [class*="grid"]')].slice(0, 3);
                targets.forEach(t => {
                    new MutationObserver(() => {
                        window.__domChanges.push(Date.now());
                    }).observe(t, {childList: true, subtree: true, characterData: true});
                });
            };
            setInterval(() => { if (!window.__domChanges) window.__domChanges = []; window.__watchDom(); }, 1000);
        """)

        def on_response(resp):
            nonlocal bound, last_fp
            ct = str(resp.headers.get("content-type", "")).lower()
            if "json" not in ct:
                return
            url = norm_url(resp.url)
            try:
                text = resp.text()
                if len(text) > 300000 or not text:
                    return
                data = json.loads(text)
            except Exception:
                return
            now_ms = time.time() * 1000
            # 接口识别:数据型接口 = 顶层是 list,或 dict 里含 shops/activities/data/items/products 列表
            n = 0
            if isinstance(data, list):
                n = len(data)
            elif isinstance(data, dict):
                for key in ("shops", "activities", "data", "items", "products", "list"):
                    if isinstance(data.get(key), list):
                        n = len(data[key])
                        break
            if bound is None:
                if n >= 3:
                    bound = {"url": url, "bound_at_ms": now_ms}
                    last_fp = fingerprint(data)
                    log.append(f"[绑定] 接口 {url} (条目 {n}) 指纹 {last_fp}")
            # 变化检测:已绑定接口的后续响应(含搜索等 query 变化)
            elif url.split("?")[0] == bound["url"].split("?")[0]:
                fp = fingerprint(data)
                if fp != last_fp:
                    notice = {"t_ms": now_ms, "api": url,
                              "before": last_fp, "after": fp,
                              "delay_from_action_ms": round(now_ms - action_t0_ms)}
                    notices.append(notice)
                    log.append(f"[通知] 接口数据变化: {last_fp} -> {fp}")
                    last_fp = fp

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)

        # 动作:搜索框输入关键词(触发新的数据请求,响应内容变化)
        action_t0_ms = time.time() * 1000
        log.append(f"[动作] 搜索框输入 Claude (t0={round(action_t0_ms)}ms)")
        try:
            page.fill("input", "Claude", timeout=8000)
            page.keyboard.press("Enter")
            log.append("[动作] Enter")
        except Exception as e:
            log.append(f"[动作] 搜索失败: {str(e)[:80]}")
        page.wait_for_timeout(10000)

        # 动作2:点击排序控件(默认→价格),若搜索未触发请求
        if not notices:
            log.append("[动作] 备用:点击排序控件")
            try:
                page.click('button:has-text("默认")', timeout=6000)
                page.wait_for_timeout(1200)
                for label in ("价格", "价格从低到高", "最新"):
                    try:
                        page.click(f'[role="menuitem"]:has-text("{label}"), [role="option"]:has-text("{label}"), button:has-text("{label}")', timeout=3000)
                        log.append(f"[动作] 选择 {label}")
                        break
                    except Exception:
                        continue
            except Exception as e:
                log.append(f"[动作] 排序失败: {str(e)[:80]}")
            page.wait_for_timeout(8000)

        # 读取 DOM 变化记录
        dom_changes = page.evaluate("() => window.__domChanges || []")
        browser.close()

    # ── 输出 ──
    print("\n".join(log))
    print(f"\n绑定接口: {bound}")
    print(f"变化通知: {len(notices)} 次")
    for n in notices:
        print(f"  {json.dumps(n, ensure_ascii=False)}")
    # 时间差:接口通知时刻 vs 最近的 DOM 变化时刻
    if notices and dom_changes:
        api_ms = notices[0]["t_ms"]
        after = [d for d in dom_changes if d >= api_ms - 3000]
        if after:
            dom_ms = min(after)
            print(f"\n时间差: 接口数据到达 +{(api_ms - action_t0_ms) / 1000:.2f}s, "
                  f"DOM 更新 +{(dom_ms - action_t0_ms) / 1000:.2f}s, "
                  f"接口领先 {(dom_ms - api_ms) / 1000:.2f}s")
        else:
            print(f"\n接口数据到达 +{(api_ms - action_t0_ms) / 1000:.2f}s (DOM 变化未捕获)")
    out = ARTIFACTS / "runtime_bind_demo.json"
    out.write_text(json.dumps({"log": log, "bound": bound, "notices": notices,
                               "dom_changes": dom_changes[:50]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
