# -*- coding: utf-8 -*-
"""渠道对照演示:同一页面、同一次点击,四种读法

  A 朴素 DOM 读法     —— 只读页面可见文本(模拟没有验证层的 agent)
  P Playwright MCP    —— 官方 @playwright/mcp 真实调用(默认配置,原始返回全量引用)
  B 后果卡读法        —— 本项目的 page_outcome / effect / errors
  C 真值裁判          —— 隐藏的 ground truth:后端实际返回了什么

与 demo_false_success.py 的区别:新增 P 臂(官方竞品真实通道),报告双语文,
并把"到达真相需要几次额外调用"作为一等公民指标。

诚实性设计(不冤枉竞品):
  - P 臂的点击返回**全量原文引用**,不做删改;
  - 报告明确承认 P 臂返回中的 console 错误计数是间接信号(且含 favicon 404 噪声);
  - 对比点是信道形态(推 vs 拉),不是模型能力;
  - 快照以文件链接返回是本机 v0.0.82 默认配置的实测行为,可能随客户端/配置变化。

用法:
  python mcp/demo_showdown.py                     # 跑全部场景,打印中文报告
  python mcp/demo_showdown.py --report            # 同时写 docs/对照演示-渠道对照.md 与 docs/en/showdown-false-success.md
  python mcp/demo_showdown.py --case 1            # 只跑场景 1
  python mcp/demo_showdown.py --pw-version 0.0.82 # 指定 @playwright/mcp 版本(默认 0.0.82)
  python mcp/demo_showdown.py --skip-playwright   # 跳过 P 臂(离线环境;报告标注 SKIPPED)

依赖:P 臂需要 node/npx 与 chromium(chrome-for-testing)。若报
"Browser ... not installed",先执行:
  npx @playwright/mcp install-browser chrome-for-testing
"""
import argparse
import asyncio
import json
import re
import socket
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_false_success as dfs  # noqa: E402

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = dfs.ROOT
PW_VERSION_DEFAULT = "0.0.82"

# P 臂各场景的点击目标(fixture 页内唯一 CSS)
PW_TARGETS = {"1": "#btn1", "2": "#btn2", "3": "#btn3"}
# 窗口内即可见失败的场景(其状态码应当"不在 P 臂返回里、而在 B 臂返回里")
IN_WINDOW_FAILURE = {"1": 422, "2": 500}


async def pw_call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return bool(r.isError), (r.content[0].text if r.content else "")


def _read_artifact(outdir: Path, click_text: str, kind: str):
    """从 P 臂点击返回文本里解析被链接的工件文件名并读取内容(快照/控制台日志)。"""
    pat = re.compile(kind + r"-[\d\-TZ.:]+\.(" + ("yml" if kind == "page" else "log") + ")")
    m = pat.search(click_text)
    if not m:
        return None
    f = outdir / Path(m.group(0)).name
    return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else None


async def run_playwright_arm(case_id: str, url: str, pw_version: str):
    """P 臂:官方 @playwright/mcp,默认配置,真实调用。返回原始证据。"""
    outdir = Path(tempfile.mkdtemp(prefix="pw_mcp_showdown_"))
    params = StdioServerParameters(
        command="cmd",
        args=["/c", "npx", "-y", f"@playwright/mcp@{pw_version}",
              "--isolated", "--browser", "chromium", "--headless",
              "--output-dir", str(outdir)],
    )
    case = dfs.CASES[case_id]
    evidence = {"version": pw_version, "url": url, "output_dir": str(outdir)}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await asyncio.wait_for(s.initialize(), timeout=240)

            err, text = await pw_call(s, "browser_navigate", {"url": url})
            if err and "not installed" in text:
                raise RuntimeError(
                    "P 臂浏览器未安装。先执行: npx @playwright/mcp install-browser chrome-for-testing\n"
                    + text[:300])

            # ---- 同一次点击 ----
            err, text = await pw_call(s, "browser_click",
                                      {"target": PW_TARGETS[case_id],
                                       "element": f"{case['button']} button"})
            evidence["click_is_error"] = err
            evidence["click_text"] = text
            evidence["console_log"] = _read_artifact(outdir, text, "console")
            evidence["snapshot_yml"] = _read_artifact(outdir, text, "page")

            # ---- 追查第 1 跳:网络请求列表(拉式) ----
            err, text = await pw_call(s, "browser_network_requests", {})
            evidence["net_text"] = text

            # ---- 场景 2/3:3.5 秒后页面自身翻脸,若 agent 主动回看可见 ----
            if case_id in ("2", "3"):
                await asyncio.sleep(3.5)
                err, text = await pw_call(s, "browser_wait_for", {"text": "❌"})
                evidence["later_wait_ok"] = not err

            await pw_call(s, "browser_close", {})
    return evidence


def _naive_verdict(text: str) -> str:
    if any(k in text for k in ("✅", "处理中", "排队中", "成功")):
        return "无失败信号 → 继续下一步"
    if "❌" in text or "失败" in text:
        return "看到失败(但无法区分前后端)"
    return "无信号"


def render_case(r, lang: str) -> str:
    case, card = r["case"], r["card"]
    p = r["pw"]
    gt = r["ground_truth"]
    gt_desc = "; ".join(f"{x['path']} → HTTP {x['status']}" for x in gt) or "(无请求)"
    po = card["page_outcome"]

    if lang == "zh":
        a_row = (f"| **A 朴素 DOM** | `{r['naive_text']}` | 永远到不了(页面从不显示失败) |"
                 if r["case_id"] == "1" else
                 f"| **A 朴素 DOM** | `{r['naive_text']}` | 等待并重读页面(3.5 秒后页面翻脸) |")
        L = {
            "title": f"## 场景:{case['title']}",
            "table": ["| 读法 | 动作返回时刻所见 | 到达真相需要的额外动作 |", "|---|---|---|"],
            "a": a_row,
            "p_head": f"| **P Playwright MCP** (v{p['version']}) | `isError=false` + `Console: "
                      f"{_console_count(p['click_text'])}`(计数,含 favicon 404 噪声)+ 快照/日志为**文件链接** | "
                      f"≥2 跳:读日志文件或 `browser_network_requests`,再 `browser_network_request` 取响应体 |",
            "b": f"| **B 后果卡**(本项目) | `page_outcome={po}` / `{card['why']}` | **0**(判定与原因就在返回里) |",
            "c": f"| **C 真值裁判**(agent 不可见) | `{gt_desc}` | — |",
            "click": "### P 臂点击返回(原文全量引用)",
            "net": "### P 臂第 1 跳:browser_network_requests(需模型主动调用)",
            "log": "### P 臂被链接的控制台日志文件(需另一次读取)",
            "card": "### B 臂后果卡关键字段",
        }
    else:
        a_row = (f"| **A Naive DOM** | `{r['naive_text']}` | never (the page never shows the failure) |"
                 if r["case_id"] == "1" else
                 f"| **A Naive DOM** | `{r['naive_text']}` | wait and re-read the page (it flips at ~3 s) |")
        L = {
            "title": f"## Case: {_en_case_title(r['case_id'])}",
            "table": ["| Readout | What the action response contains | Extra calls needed to reach the truth |", "|---|---|---|"],
            "a": a_row,
            "p_head": f"| **P Playwright MCP** (v{p['version']}) | `isError=false` + `Console: "
                      f"{_console_count(p['click_text'])}` (count only, incl. a favicon-404 noise line) "
                      f"+ snapshot/console linked as **files** | ≥2 hops: read the log file or call "
                      f"`browser_network_requests`, then `browser_network_request` for the body |",
            "b": f"| **B Receipt** (this project) | `page_outcome={po}` / `{card['why']}` | **0** (verdict + cause inside the response) |",
            "c": f"| **C Ground truth** (invisible to agent) | `{gt_desc}` | — |",
            "click": "### Arm P: raw click response (verbatim)",
            "net": "### Arm P, hop 1: browser_network_requests (requires the model to call it)",
            "log": "### Arm P: linked console log file (requires another read)",
            "card": "### Arm B: key receipt fields",
        }

    lines = [L["title"], ""]
    lines += L["table"]
    lines += [L["a"], L["p_head"], L["b"], L["c"], ""]

    if r.get("naive_text_later") is not None:
        pw_later = ""
        if not r.get("pw_skipped") and r["pw"].get("later_wait_ok") is not None:
            ok = r["pw"]["later_wait_ok"]
            pw_later = ("P 臂主动回看实测(`browser_wait_for ❌`):" + ("可见 ✓" if ok else "不可见 ✗")) \
                if lang == "zh" else \
                ("P-arm deliberate re-check (`browser_wait_for ❌`): " + ("visible ✓" if ok else "not visible ✗"))
        later = "3.5 秒后页面文本翻脸:`{}`(异步结果不在动作窗口内;A/P 臂若主动回看才可见){}".format(
            r["naive_text_later"], ("。 " + pw_later) if pw_later else "") if lang == "zh" else \
            "3.5 s later the page itself flips to `{}` (outside the action window; visible to A/P only on a deliberate re-check){}".format(
                r["naive_text_later"], (". " + pw_later) if pw_later else "")
        lines += [later, ""]

    # 原文里自带 ```js 围栏,外层必须用四反引号,否则渲染截断
    lines += [L["click"], "", "````", p["click_text"].strip(), "````", ""]
    if r.get("pw_ground_truth"):
        pw_gt = "; ".join(f"{x['path']} → HTTP {x['status']}" for x in r["pw_ground_truth"])
        note = ("P 臂浏览器触发同一故障(服务端记录):" if lang == "zh"
                else "P arm's browser triggered the same fault (server-side record):")
        lines += [f"> {note} `{pw_gt}`", ""]
    lines += [L["net"], "", "```", p["net_text"].strip()[:1200], "```", ""]
    if p.get("console_log"):
        lines += [L["log"], "", "```", p["console_log"].strip()[:1200], "```", ""]
    lines += [L["card"], "", "```json", json.dumps(
        {"page_outcome": po, "situation": card["situation"], "why": card["why"],
         "errors": card.get("errors")},
        ensure_ascii=False, indent=1)[:800], "```", ""]
    return "\n".join(lines)


def _console_count(click_text: str) -> str:
    m = re.search(r"Console: (\d+ errors?(?:, \d+ warnings?)?)", click_text)
    return m.group(1) if m else "(none)"


_EN_CASE_TITLES = {
    "1": "Page shows success, backend returns 422",
    "2": "Button greys out, request rolls back (500)",
    "3": "202 accepted, async task fails 3 s later",
}


def _en_case_title(cid: str) -> str:
    return _EN_CASE_TITLES.get(cid, dfs.CASES[cid]["title"])


def render_report(results, pw_version, skip_pw, lang: str) -> str:
    date = _today()
    if lang == "zh":
        head = (
            "# 渠道对照演示:假成功(同页同点击,四种读法)\n\n"
            "> 同一个页面、同一次点击:A 朴素 DOM / P 官方 Playwright MCP / B 后果卡 / C 隐藏真值。\n"
            f"> P 臂为**真实调用** @playwright/mcp v{pw_version} 默认配置,原始返回全量引用,可复核。\n"
            "> 复现命令:`python mcp/demo_showdown.py --case all --report`\n\n"
            "## 结论一览\n\n"
            "| 场景 | 后端实际 | P 臂返回里有失败原因吗 | B 臂判定 |\n|---|---|---|---|\n"
        )
        fair = (
            "\n## 诚实性说明(必读)\n\n"
            "1. **P 臂并非毫无信号**:点击返回带有 console 错误**计数**(本页含一条 favicon 404 常驻噪声)。"
            "强模型可以据此发起追查;但失败的具体状态码与原因(422/账号已存在)不在返回文本里,"
            "需要模型**额外主动调用** 1-3 次工具(读日志文件 → `browser_network_requests` → "
            "`browser_network_request` 取响应体)才能拼出。\n"
            "2. **B 臂把判定与结构化原因放进动作返回本身**。对比点是**信道形态(推 vs 拉)**,不是模型能力——"
            "这与本项目 A/B 实验结论一致:依赖模型主动调用验证工具的模式不成立(15 轮仅 1 次)。\n"
            "3. 快照以**文件链接**返回是 v0.0.82 默认配置在本机的实测行为,可能随客户端/配置而变;"
            "本报告全部引用原始返回,不依赖该行为成立与否。\n"
            "4. 场景 3(202 后异步失败)B 臂同样只给 `unchanged/uncertain`:异步结果追踪是已登记的待补项,"
            "两臂在此场景都同样失明,谁也不比谁强。\n"
        )
        tail = "\n---\n\n"
    else:
        head = (
            "# Channel Showdown: False Success (same page, same click, four readouts)\n\n"
            "> Same page, same click: A naive DOM / P official Playwright MCP / B receipt (this project) / C hidden ground truth.\n"
            f"> Arm P is a **real invocation** of @playwright/mcp v{pw_version} with default config; raw responses quoted verbatim.\n"
            "> Reproduce: `python mcp/demo_showdown.py --case all --report`\n\n"
            "## Summary\n\n"
            "| Case | Backend reality | Failure cause in P's response? | B verdict |\n|---|---|---|---|\n"
        )
        fair = (
            "\n## Fairness notes (read this)\n\n"
            "1. **Arm P is not signal-free**: the click response carries a console error **count** "
            "(this page always includes a favicon-404 noise line). A strong model may chase it; but the concrete "
            "status code and cause (422 / email taken) are absent from the response text and require **1-3 additional, "
            "model-initiated calls** (read the log file → `browser_network_requests` → `browser_network_request` body).\n"
            "2. **Arm B puts the verdict and the structured cause inside the action response itself.** The contrast is "
            "**channel shape (push vs pull)**, not model capability — consistent with this project's own A/B finding "
            "that model-initiated verification calls don't happen (1 call in 15 turns).\n"
            "3. Snapshots being returned as **file links** is the observed default-config behavior of v0.0.82 on this "
            "machine and may vary by client/config; every response here is quoted verbatim, so the report does not "
            "depend on that behavior.\n"
            "4. Case 3 (202 + async failure): arm B also only reports `unchanged/uncertain` — async-result tracking is a "
            "registered gap. Both arms are blind here; neither is better.\n"
        )
        tail = "\n---\n\n"

    rows = []
    for r in results:
        gt = "; ".join(f"HTTP {x['status']}" for x in r["ground_truth"]) or "(none)"
        if r.get("pw_skipped"):
            p_col = "SKIPPED"
        else:
            status = IN_WINDOW_FAILURE.get(r["case_id"])
            pushed = status is not None and str(status) in (r["pw"]["click_text"] or "")
            p_col = ("是(异常)" if pushed else "否(仅 console 计数)") if lang == "zh" else \
                ("yes (unexpected)" if pushed else "no (console count only)")
        rows.append(f"| {r['case']['title'] if lang == 'zh' else _en_case_title(r['case_id'])}"
                    f" | {gt} | {p_col} | `{r['card']['page_outcome']}` |")

    parts = [render_case(r, lang) for r in results]
    return head + "\n".join(rows) + fair + tail + tail.join(parts)


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all", choices=sorted(dfs.CASES) + ["all"])
    ap.add_argument("--report", action="store_true",
                    help="写 docs/对照演示-渠道对照.md 与 docs/en/showdown-false-success.md")
    ap.add_argument("--pw-version", default=PW_VERSION_DEFAULT)
    ap.add_argument("--skip-playwright", action="store_true", help="离线环境跳过 P 臂")
    a = ap.parse_args()

    # 运行环境自检:server.py 需要 playwright
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("❌ 当前解释器缺少 playwright。请用装有 mcp+playwright 的 Python(项目 CI 为 3.10/3.12):")
        print("   C:\\Users\\<你>\\AppData\\Local\\Programs\\Python\\Python312\\python.exe mcp\\demo_showdown.py")
        raise SystemExit(2)

    case_ids = sorted(dfs.CASES) if a.case == "all" else [a.case]

    # 单一共享页面服务器:A/B/P 三臂同一 URL、同一后端故障注入(真值裁判统一直播)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), dfs.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shared_url = f"http://127.0.0.1:{port}/external_facts.html"

    results = []
    try:
        for cid in case_ids:
            # ---- A+B 臂(复用原演示:同一动作,朴素读法与后果卡) ----
            dfs.GROUND_TRUTH["requests"] = []
            r = await dfs.run_demo(cid, url=shared_url)
            r["case_id"] = cid

            # ---- P 臂(官方 Playwright MCP,独立浏览器,同一页面重新加载) ----
            dfs.GROUND_TRUTH["requests"] = []
            if a.skip_playwright:
                r["pw_skipped"] = True
            else:
                try:
                    r["pw"] = await run_playwright_arm(cid, shared_url, a.pw_version)
                except RuntimeError as e:
                    print(f"❌ P 臂失败(场景 {cid}):{e}")
                    raise SystemExit(2)
                r["pw_ground_truth"] = list(dfs.GROUND_TRUTH["requests"])
            results.append(r)
    finally:
        httpd.shutdown()

    zh = render_report(results, a.pw_version, a.skip_playwright, "zh")
    en = render_report(results, a.pw_version, a.skip_playwright, "en")
    print(zh)

    if a.report:
        out_zh = ROOT / "docs" / "对照演示-渠道对照.md"
        out_en = ROOT / "docs" / "en" / "showdown-false-success.md"
        out_en.parent.mkdir(parents=True, exist_ok=True)
        out_zh.write_text(zh, encoding="utf-8")
        out_en.write_text(en, encoding="utf-8")
        print(f"\n报告已写入:\n  {out_zh}\n  {out_en}")

    # ---- 自检门(演示不许"因错误的原因好看") ----
    bad = []
    for r in results:
        po = r["card"]["page_outcome"]
        if po == "progressed":
            bad.append(f"场景 {r['case_id']}: B 臂判 progressed(假成功红线)")
        if not r.get("pw_skipped"):
            p = r["pw"]
            if p.get("click_is_error"):
                bad.append(f"场景 {r['case_id']}: P 臂点击本身报错(演示无效,检查环境)")
            status = IN_WINDOW_FAILURE.get(r["case_id"])
            if status is not None:
                if str(status) in (p["click_text"] or ""):
                    bad.append(f"场景 {r['case_id']}: P 臂返回中出现了 HTTP {status}(与'拉式'论断不符,报告须改写)")
                card_blob = json.dumps(r["card"], ensure_ascii=False)
                if str(status) not in card_blob:
                    bad.append(f"场景 {r['case_id']}: B 臂未推送 HTTP {status}(推式承诺未兑现)")
    if bad:
        print("\n❌ 自检未过:")
        for b in bad:
            print("   -", b)
        raise SystemExit(1)
    print(f"\n✅ 自检通过:{len(results)} 个场景——B 臂零假成功;窗口内失败事实在 P 臂返回中缺席、在 B 臂返回中在场"
          f"(场景 3 异步失败两臂都只见 uncertain/unchanged,如实呈现)")
    raise SystemExit(0)


if __name__ == "__main__":
    asyncio.run(main())
