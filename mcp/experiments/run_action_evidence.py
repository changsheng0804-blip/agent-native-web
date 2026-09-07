# -*- coding: utf-8 -*-
"""最小验证:真实站点"动作 → runtime 流 → 结构化动作证据卡"闭环。

站点:github.com/git/git(接口开放、可操作、已验证可访问)
动作:
  1. 点击 Pull requests tab   → 导航 + 数据接口请求(成功场景)
  2. 点击 Star(未登录)        → 重定向 /login(DOM 几乎无反馈,runtime 清晰)
  3. 顶部搜索框输入            → 联想建议接口请求(输入动作反馈)

每个动作生成"动作证据卡":动作窗口内的请求/响应/重定向/失败清单 + 规则化决策建议。
"""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
URL = "https://github.com/git/git"
WINDOW_BEFORE_MS = 500
WINDOW_AFTER_MS = 3500


def norm_url(url):
    try:
        p = urlsplit(str(url))
        return f"{p.netloc}{p.path or '/'}"[:160]
    except Exception:
        return str(url)[:160]


def build_evidence_card(events, t0_ms):
    """从动作窗口事件构建结构化证据卡。"""
    start = t0_ms - WINDOW_BEFORE_MS
    end = t0_ms + WINDOW_AFTER_MS
    window = [e for e in events if start <= e["t"] <= end]
    reqs = [e for e in window if e["type"] == "request"]
    resps = [e for e in window if e["type"] == "response"]
    fails = [e for e in window if e["type"] == "failed"]
    cons = [e for e in window if e["type"] == "console" and e.get("level") == "error"]

    # 请求-响应配对(按 URL+时间最近)
    by_url = {}
    for r in reqs:
        by_url.setdefault(r["url"], []).append(r)
    card = {
        "request_count": len(reqs),
        "requests": [],
        "redirects": [],
        "api_responses": [],
        "failures": [{"url": f["url"], "error": f.get("error")} for f in fails[:5]],
        "console_errors": [c["text"] for c in cons[:3]],
    }
    for r in reqs:
        entry = {"url": r["url"], "method": r.get("method"), "rtype": r.get("rtype"),
                 "at_ms": r["t"] - t0_ms}
        # 找最近的同 URL 响应
        cands = [x for x in resps if x["url"] == r["url"] and x["t"] >= r["t"]]
        if cands:
            resp = min(cands, key=lambda x: x["t"])
            entry["status"] = resp["status"]
            entry["resp_at_ms"] = resp["t"] - t0_ms
            if resp["status"] in (301, 302, 303, 307, 308):
                card["redirects"].append({"url": r["url"], "status": resp["status"]})
            if "json" in resp.get("ctype", ""):
                card["api_responses"].append({"url": r["url"], "status": resp["status"]})
        card["requests"].append(entry)
    return card


def decision_from_card(card, action_label):
    """规则化决策建议:证据 → agent 下一步行动。"""
    tips = []
    # 最高优先级:document 导航到登录页(playwright 折叠重定向,状态码检测不到)
    doc_urls = [r["url"] for r in card["requests"] if r.get("rtype") == "document"]
    if any("/login" in u for u in doc_urls):
        tips.append(f"⚠ 动作'{action_label}'被重定向到登录页({[u for u in doc_urls if '/login' in u][0]})"
                    f"→ 决策:需要登录态,该动作不能继续,转人工/登录流程")
        return tips
    if card["redirects"]:
        for rd in card["redirects"]:
            if "login" in rd["url"]:
                tips.append(f"⚠ 动作'{action_label}'被重定向到登录页(未登录/需要认证)→ 决策:该动作需要登录态,不能继续")
            else:
                tips.append(f"⚠ 动作'{action_label}'发生重定向({rd['status']})→ 决策:页面被转移,检查新位置")
    if card["failures"]:
        tips.append(f"⚠ 动作'{action_label}'有 {len(card['failures'])} 个请求失败 → 决策:网络/资源问题,重试前先确认")
    if card["console_errors"]:
        tips.append(f"⚠ 动作'{action_label}'触发 {len(card['console_errors'])} 个控制台错误 → 决策:页面脚本异常")
    statuses = [r.get("status") for r in card["requests"] if r.get("status")]
    if statuses and all(s and 200 <= s < 300 for s in statuses):
        tips.append(f"✅ 动作'{action_label}'的请求全部 2xx → 决策:动作已生效,继续下一步")
    elif statuses and any(s and s >= 400 for s in statuses):
        bad = [r["url"] for r in card["requests"] if r.get("status") and r["status"] >= 400]
        tips.append(f"⚠ 动作'{action_label}'有 {len(bad)} 个 4xx/5xx 请求 → 决策:服务端拒绝,读取错误后修正")
    if card["api_responses"]:
        tips.append(f"ℹ 动作'{action_label}'触发了 {len(card['api_responses'])} 个数据接口响应 → 决策:页面状态已迁移,数据已更新")
    if not card["requests"]:
        tips.append(f"❓ 动作'{action_label}'窗口内无任何请求 → 决策:点击可能未生效/被拦截/纯前端行为,需 DOM 确认")
    return tips


def main():
    events = []
    cards = []

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
        }))
        page.on("requestfailed", lambda r: ev({
            "t": round(time.time() * 1000), "type": "failed",
            "url": norm_url(r.url), "error": (r.failure or "")[:100],
        }))
        page.on("console", lambda m: ev({
            "t": round(time.time() * 1000), "type": "console",
            "level": m.type, "text": m.text[:120],
        }))

        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)

        def do_action(label, fn):
            t0 = time.time() * 1000
            try:
                fn()
                ok = True
            except Exception as e:
                ok = False
                print(f"  动作执行异常: {str(e)[:80]}")
            page.wait_for_timeout(WINDOW_AFTER_MS)
            card = build_evidence_card(events, t0)
            card["action"] = label
            card["executed"] = ok
            tips = decision_from_card(card, label)
            cards.append({"card": card, "tips": tips})
            print(f"\n===== 动作: {label} =====")
            print(json.dumps(card, ensure_ascii=False, indent=1)[:1600])
            for tip in tips:
                print(f"  {tip}")

        # 动作1:点击 Pull requests tab(成功导航场景)
        do_action("点击 Pull requests tab", lambda: page.click(
            'a[href="/git/git/pulls"], a:has-text("Pull requests")', timeout=8000))
        page.wait_for_timeout(3000)

        # 动作2:点击 Star(未登录 → 重定向 /login)
        do_action("点击 Star(未登录)", lambda: page.click(
            '#repo-stars-counter-star, button[aria-label*="Star this repository"], button:has-text("Star")', timeout=8000))
        page.wait_for_timeout(3000)

        # 回到仓库页,动作3:顶部搜索框输入(联想建议请求)——搜索框需先展开
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            def search_action():
                try:
                    page.click('[data-target="qbsearch-input.inputButton"], button[aria-label*="Search"], #query-builder-test', timeout=5000)
                except Exception:
                    pass
                page.click('input#query-builder-test, input[name="q"], input[type="search"]', timeout=8000)
                page.keyboard.type("git", delay=80)
            do_action("搜索框输入 git", search_action)
        except Exception as e:
            print(f"动作3 准备失败: {str(e)[:80]}")
        browser.close()

    out = ARTIFACTS / "action_evidence_demo.json"
    out.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
