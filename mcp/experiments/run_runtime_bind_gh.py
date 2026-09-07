# -*- coding: utf-8 -*-
"""关键测试:真实站点 runtime 流 → 接口绑定 → 变化检测 → 通知(全流程拉通)。

站点:github.com/git/git(活跃仓库,接口可解析,有真实外部变化源)
  1. 绑定接口指纹:latest-commit(oid/date) + _sidebar(usedBy/contributors)
  2. 观察 150 秒:git/git 仓库高活跃,等待真实外部变化(他人 push)
  3. 确定性演示:切换分支 → overview-files 响应变化 → 通知
  4. 时间差:接口数据到达 vs DOM 更新
"""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
URL = "https://github.com/git/git"
NATURAL_WATCH_S = 150


def norm_url(url):
    try:
        p = urlsplit(str(url))
        return f"{p.netloc}{p.path or '/'}"[:150]
    except Exception:
        return str(url)[:150]


def fingerprint(data):
    """接口响应指纹:关键字段值(变化检测依据)。"""
    if isinstance(data, dict):
        if "oid" in data:  # latest-commit
            return {"oid": str(data.get("oid"))[:12], "date": data.get("date"),
                    "msg": str(data.get("shortMessageHtmlLink", ""))[:40]}
        if "usedBy" in data:  # _sidebar
            return {"usedBy": data.get("usedBy"), "contributors": data.get("contributors"),
                    "releases": data.get("releases"), "languages": len(data.get("languages", []))}
        if "entries" in data:  # tree-commit-info
            return {"entries": len(data.get("entries", []))}
        if "files" in data:  # overview-files
            names = [f.get("name") for f in data.get("files", []) if isinstance(f, dict)]
            return {"files": names[:20]}
        return {"keys": list(data.keys())[:6]}
    return {"repr": str(data)[:60]}


def main():
    log = []
    bound = {}        # url -> last_fp
    notices = []
    dom_changes = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # DOM 变化观察(记录时间,与接口到达对比)
        page.add_init_script("""
            window.__domChanges = [];
            new MutationObserver(() => { window.__domChanges.push(Date.now()); })
                .observe(document.documentElement, {childList: true, subtree: true, characterData: true});
        """)

        def on_response(resp):
            ct = str(resp.headers.get("content-type", "")).lower()
            if "json" not in ct:
                return
            url = norm_url(resp.url)
            try:
                text = resp.text()
                if len(text) > 400000:
                    return
                data = json.loads(text)
            except Exception:
                return
            now_ms = time.time() * 1000
            # 绑定候选接口
            for key in ("latest-commit", "_sidebar", "overview-files", "tree-commit-info", "branch-and-tag-count"):
                if key in url and key not in bound:
                    bound[key] = {"url": url, "fp": fingerprint(data), "bound_at_ms": now_ms}
                    log.append(f"[绑定] {key} {url} 指纹 {bound[key]['fp']}")
            # 变化检测
            for key, entry in list(bound.items()):
                if key in url:
                    fp = fingerprint(data)
                    if fp != entry["fp"]:
                        delay = round(now_ms - action_t0) if action_t0 else None
                        notices.append({"key": key, "t_ms": now_ms,
                                        "before": entry["fp"], "after": fp,
                                        "delay_from_action_ms": delay})
                        log.append(f"[通知] {key} 数据变化: {entry['fp']} -> {fp}")
                        entry["fp"] = fp

        page.on("response", on_response)
        action_t0 = None
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        log.append("[观察] 等待自然外部变化 (150s)")

        # 阶段1:自然变化观察(缩短为 60 秒,聚焦确定性演示)
        t0 = time.time()
        while time.time() - t0 < 60 and not notices:
            page.wait_for_timeout(3000)
        log.append(f"[观察] 结束,自然变化通知 {len(notices)} 次")

        # 阶段2:确定性演示——导航到 v2.43.0 分支(overview-files 接口返回不同内容)
        action_t0 = time.time() * 1000
        log.append("[动作] 导航到 v2.43.0 分支")
        try:
            page.goto("https://github.com/git/git/tree/v2.43.0", wait_until="domcontentloaded", timeout=60000)
            log.append("[动作] 导航完成,等待数据接口响应")
        except Exception as e:
            log.append(f"[动作] 导航失败: {str(e)[:100]}")
        page.wait_for_timeout(12000)

        dom_changes = page.evaluate("() => window.__domChanges || []")
        browser.close()

    print("\n".join(log))
    print(f"\n绑定接口: {list(bound.keys())}")
    print(f"变化通知: {len(notices)} 次")
    for n in notices:
        print(f"  {json.dumps(n, ensure_ascii=False)}")
    if notices:
        api_ms = notices[0]["t_ms"]
        after = [d for d in dom_changes if d >= api_ms - 3000]
        if after:
            print(f"\n时间差: 接口数据到达 vs DOM 更新 → 接口领先 {(min(after) - api_ms) / 1000:.2f}s")
    out = ARTIFACTS / "runtime_bind_gh.json"
    out.write_text(json.dumps({"log": log, "bound": {k: v["fp"] for k, v in bound.items()},
                               "notices": notices, "dom_changes": dom_changes[:30]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
