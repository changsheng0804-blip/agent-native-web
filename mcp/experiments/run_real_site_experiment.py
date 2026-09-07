# -*- coding: utf-8 -*-
"""真实网站 A/B 实战验证:时间感知反馈 vs 现状基线(v2)。

站点与步骤(全部只读/无副作用):
  1. Google Flights: 点击乘客按钮 → 弹窗异步出现
  2. Google Flights: 先点出发地组合框,再填 Tokyo → 联想建议延时出现
  3. GitHub (git/git): 点击 Pull requests → 导航到 /pulls
  4. Wikipedia: 搜索框输入 Alan Turing → 联想建议异步出现

设计要点:
- 基线臂(A):server 现成判定;时间感知臂(B):动作锚 + 窗口内变更流(含 URL 注入)→ 引擎卡
- 导航步骤:旧文档销毁事件流重置 → B 臂 since=0 读,引擎按 [t0, t0+window] 时间窗过滤
- 真实站点脆弱:每个步骤用全新 server 会话(崩溃隔离);失败记为 skip/err 不中断
- 过滤语义(实测):text 过滤只匹配可见文本(大小写敏感);name 过滤匹配内核名(子串)

运行: python mcp/experiments/run_real_site_experiment.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_aware_engine import build_temporal_card

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RUNS = 3
GT_POLL_TOTAL_S = 8

STEPS = [
    {
        "site": "google-flights", "url": "https://www.google.com/travel/flights",
        "name": "gf-passengers-dialog",
        "desc": "点击乘客按钮 → 弹窗异步出现",
        "find": [{"role": "button", "name": "passenger"}],
        "action": "click", "window_ms": 6000, "wait_open_ms": 6000,
        "gt": {"dialog": True}, "gt_label": "弹窗出现",
    },
    {
        "site": "google-flights", "url": "https://www.google.com/travel/flights",
        "name": "gf-fill-tokyo-suggestions",
        "desc": "点出发地组合框 → 填 Tokyo → 联想建议延时出现",
        # 重型 SPA 持续重建构件(el id 秒级失效),直接传语义名,工具调用时实时解析
        "fixed_id": "combobox.where-from",
        "action": "fill", "pre_click": True, "window_ms": 9000, "wait_open_ms": 6000,
        "gt": {"option": "Tokyo, Japan"}, "gt_label": "建议 Tokyo, Japan 出现",
    },
    {
        "site": "github", "url": "https://github.com/git/git",
        "name": "gh-pulls-navigation",
        "desc": "点击 Pull requests → 导航到 /pulls",
        "find": [{"role": "link", "text": "Pull requests"}],
        "action": "click", "window_ms": 8000, "wait_open_ms": 4000,
        "gt": {"url_contains": "/pulls"}, "gt_label": "URL 变为 /pulls",
    },
    {
        "site": "wikipedia", "url": "https://www.wikipedia.org",
        "name": "wiki-search-suggestions",
        "desc": "搜索框输入 Alan Turing → 联想建议异步出现",
        "find": [{"role": "input"}],
        "action": "fill", "window_ms": 9000, "wait_open_ms": 4000,
        "gt": {"text": "图灵"}, "gt_label": "建议出现(艾伦·图灵)",
    },
]


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except Exception:
                return {"_raw_text": item.text[:300]}
    return {}


async def open_world(session, tag, url, wait_ms):
    return await call(session, "world_open", {
        "url": url, "ready_policy": "action", "wait_ms": wait_ms,
        "reuse_policy": "never", "task_id": f"real-{tag}",
    }, timeout=150)


async def find_target(session, wid, find_opts):
    for opt in find_opts:
        if "resolve" in opt:
            try:
                r = await call(session, "world_resolve", {"world_id": wid, "query": opt["resolve"]}, timeout=30)
                if isinstance(r, dict) and r.get("id"):
                    return r["id"]
            except Exception:
                pass
            continue
        args = {"world_id": wid, "max_results": 10}
        for k in ("role", "text", "name"):
            if opt.get(k):
                args[k] = opt[k]
        try:
            r = await call(session, "world_entities", args, timeout=45)
        except Exception:
            continue
        ents = r.get("entities", []) if isinstance(r, dict) else []
        if opt.get("role"):
            ents = [e for e in ents if (e.get("semantic") or "").startswith(opt["role"])] or ents
        if ents:
            return ents[0]["id"]
    return None


async def gt_check(session, wid, gt):
    deadline = time.time() + GT_POLL_TOTAL_S
    checks = {}
    while time.time() < deadline:
        try:
            if gt.get("dialog"):
                r = await call(session, "world_entities", {"world_id": wid, "role": "dialog", "max_results": 10}, timeout=45)
                checks["dialog"] = len(r.get("entities", [])) > 0
            if gt.get("option"):
                r = await call(session, "world_entities", {"world_id": wid, "role": "option", "text": gt["option"], "max_results": 10}, timeout=45)
                checks["option"] = len(r.get("entities", [])) > 0
            if gt.get("text"):
                r = await call(session, "world_entities", {"world_id": wid, "text": gt["text"], "max_results": 10}, timeout=45)
                checks["text"] = len(r.get("entities", [])) > 0
            if gt.get("url_contains"):
                r = await call(session, "world_state", {"world_id": wid}, timeout=45)
                checks["url_contains"] = gt["url_contains"] in (r.get("state") or {}).get("url", "")
        except Exception:
            pass
        if checks and all(checks.values()):
            return checks
        await asyncio.sleep(1.5)
    return checks


async def poll_url_changes(session, wid, base_url, window_end):
    """轮询 URL 直到 window_end,首个偏离基准的时刻注入事件。

    base_url: 动作前的真实 URL(动作工具可能阻塞到导航完成,轮询时才读首帧会拿不到"变化")。
    """
    first = None
    while time.time() * 1000 < window_end:
        try:
            r = await call(session, "world_state", {"world_id": wid}, timeout=30)
            url = (r.get("state") or {}).get("url", "")
        except Exception:
            url = None
        if url and base_url and url != base_url and first is None:
            first = time.time() * 1000
            break
        await asyncio.sleep(1.5)
    if first is not None:
        return [{"seq": -1, "t": first, "type": "add", "id": "page.url",
                 "name": "page.url", "semantic": "", "synthetic": "url-change"}]
    return []


async def run_step_arm(session, step, arm):
    tag = f"{step['name']}-{arm}"
    try:
        opened = await open_world(session, tag, step["url"], step["wait_open_ms"])
        if not isinstance(opened, dict) or "world_id" not in opened:
            return {"arm": arm, "step": step["name"], "status": "skip", "reason": f"world_open: {str(opened)[:120]}"}
    except Exception as e:
        return {"arm": arm, "step": step["name"], "status": "skip", "reason": f"world_open: {type(e).__name__} {str(e)[:120]}"}
    wid = opened["world_id"]
    try:
        target = step.get("fixed_id") or await find_target(session, wid, step["find"])
        if target is None:
            return {"arm": arm, "step": step["name"], "status": "skip", "reason": "目标元素未找到"}
        res = {"arm": arm, "step": step["name"], "status": "ok"}
        action_args = {"world_id": wid, "id": target}
        if step["action"] == "fill":
            action_args.update({"text": "Tokyo" if step["name"].startswith("gf") else "Alan Turing",
                                "type_delay_ms": 60})
        # B 臂:动作前取页面时钟锚 + 基准 URL(导航步骤动作会阻塞到导航完成,基准必须在动作前取)
        t0 = None
        base_url = None
        if arm == "B":
            r0 = await call(session, "world_eval", {"world_id": wid, "expression": "() => Date.now()"}, timeout=30)
            t0 = float(r0.get("result") if isinstance(r0, dict) and "result" in r0 else r0)
            try:
                st = await call(session, "world_state", {"world_id": wid}, timeout=30)
                base_url = (st.get("state") or {}).get("url", "")
            except Exception:
                base_url = None
        t_act = time.perf_counter()
        if step.get("pre_click"):
            pc = await call(session, "world_click", {"world_id": wid, "id": target}, timeout=60)
            await asyncio.sleep(0.8)
            res["pre_click_outcome"] = pc.get("page_outcome")
            # SPA 重渲染会重建构件(el id 失效);固定语义名无需重定位,el id 需重查
            if not step.get("fixed_id"):
                target = await find_target(session, wid, step["find"])
                if target is None:
                    return {"arm": arm, "step": step["name"], "status": "error", "reason": "pre-click 后目标重新定位失败"}
            if arm == "B":  # 主动作(填表)前刷新锚:排除 pre-click 噪音
                r0 = await call(session, "world_eval", {"world_id": wid, "expression": "() => Date.now()"}, timeout=30)
                t0 = float(r0.get("result") if isinstance(r0, dict) and "result" in r0 else r0)
        acted = await call(session, "world_click" if step["action"] == "click" else "world_fill",
                           action_args, timeout=150)
        res["latency_ms"] = int((time.perf_counter() - t_act) * 1000)
        why = acted.get("why")
        if not isinstance(why, str):
            why = (acted.get("effect") or {}).get("why", "")
        res["server_page_outcome"] = acted.get("page_outcome")
        res["server_effect_verdict"] = (acted.get("effect") or {}).get("verdict")
        res["server_why"] = why[:100]
        res["action_chars"] = len(json.dumps(acted, ensure_ascii=False))
        if arm == "B":
            window_ms = step["window_ms"]
            # 长阻塞动作(导航等待可达数十秒)下,效果发生时间远超 t0+window_ms:
            # 窗口终点 = max(固定窗, 动作返回时刻 + 3s 观察期)
            r_end = await call(session, "world_eval", {"world_id": wid, "expression": "() => Date.now()"}, timeout=30)
            t_end = float(r_end.get("result") if isinstance(r_end, dict) and "result" in r_end else r_end)
            window_end = max(t0 + window_ms, t_end + 3000)
            synthetic = await poll_url_changes(session, wid, base_url, window_end)
            await asyncio.sleep(max(0, (window_end - time.time() * 1000) / 1000))
            # 导航后新 runtime 可能尚未就绪,读变更流带重试
            events = []
            for _ in range(4):
                after = await call(session, "world_changes", {"world_id": wid, "since": 0}, timeout=60)
                events = after.get("events", []) if isinstance(after, dict) else []
                if events:
                    break
                await asyncio.sleep(1.0)
            events = events + synthetic
            card = build_temporal_card(f"{step['action']}:{step['name']}", t0, events,
                                       window_ms=window_ms, window_end=window_end)
            res["card_verdict"] = card.verdict
            res["card_why"] = card.why[:100]
            res["window_events"] = card.window_events
            res["stability"] = card.stability
            res["reversion"] = len(card.reversion)
            res["oscillation"] = (card.oscillation or {}).get("toggles")
            res["change_chars"] = len(json.dumps(after, ensure_ascii=False))
            res["url_change_injected"] = bool(synthetic)
        res["gt"] = await gt_check(session, wid, step["gt"])
        res["gt_label"] = step["gt_label"]
        return res
    except Exception as e:
        return {"arm": arm, "step": step["name"], "status": "error", "reason": f"{type(e).__name__}: {str(e)[:150]}"}
    finally:
        try:
            await call(session, "world_close", {"world_id": wid}, timeout=15)
        except Exception:
            pass


def gt_truth(res):
    return any((res.get("gt") or {}).values())


def match_a(res):
    truth = gt_truth(res)
    po = res.get("server_page_outcome")
    return (po == "progressed") if truth else (po == "unchanged")


def match_b(res):
    truth = gt_truth(res)
    v = res.get("card_verdict")
    if truth:
        if v in ("effected", "effected-delayed"):
            return True, "strict"
        if v == "changed":
            return False, "partial"
        return False, "strict"
    return v == "no-change", "strict"


async def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    ARTIFACTS.mkdir(exist_ok=True)
    results = []
    steps = [s for s in STEPS if only is None or s["name"] == only] or STEPS
    for step in steps:
        print(f"\n===== {step['name']} | {step['desc']} =====", flush=True)
        # 每步全新 server 会话:真实站点能把 server 拖崩,隔离崩溃
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=30)
                    for run in range(1, RUNS + 1):
                        a = await run_step_arm(session, step, "A")
                        a.update({"run": run, "expected": step["gt_label"]})
                        if a["status"] == "ok":
                            a["match"] = match_a(a)
                            print(f"[A] run{run}: outcome={a['server_page_outcome']} effect={a['server_effect_verdict']} "
                                  f"gt={a['gt']} match={a['match']} ({a['latency_ms']}ms)", flush=True)
                        else:
                            print(f"[A] run{run}: SKIP/ERR {a.get('reason', '')}", flush=True)
                        results.append(a)
                        b = await run_step_arm(session, step, "B")
                        b.update({"run": run, "expected": step["gt_label"]})
                        if b["status"] == "ok":
                            ok, mode = match_b(b)
                            b.update({"match": ok, "match_mode": mode})
                            print(f"[B] run{run}: card={b['card_verdict']} server={b['server_page_outcome']} "
                                  f"gt={b['gt']} match={ok}{'*' if mode == 'partial' else ''} "
                                  f"({b['window_events']}evts, url={b['url_change_injected']}) {b['card_why']}", flush=True)
                        else:
                            print(f"[B] run{run}: SKIP/ERR {b.get('reason', '')}", flush=True)
                        results.append(b)
        except Exception as e:
            print(f"步骤 {step['name']} server 会话异常: {type(e).__name__} {str(e)[:150]}", flush=True)
            for run in range(1, RUNS + 1):
                results.append({"arm": "A", "step": step["name"], "run": run, "status": "skip",
                                "reason": "server 会话异常"})
                results.append({"arm": "B", "step": step["name"], "run": run, "status": "skip",
                                "reason": "server 会话异常"})
    report = {"steps": len(STEPS), "runs": RUNS, "results": results,
              "summary": summarize(results)}
    out = ARTIFACTS / "real_site_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(report["summary"])
    print(f"\n报告: {out}")


def summarize(results):
    from collections import defaultdict
    agg = defaultdict(lambda: {"A": {"n": 0, "ok": 0, "partial": 0, "skip": 0},
                               "B": {"n": 0, "ok": 0, "partial": 0, "skip": 0}})
    for r in results:
        s = agg[r["step"]][r["arm"]]
        if r["status"] != "ok":
            s["skip"] += 1
            continue
        s["n"] += 1
        s["ok"] += 1 if r.get("match") else 0
        s["partial"] += 1 if r.get("match_mode") == "partial" else 0
    return {k: {"A": dict(v["A"]), "B": dict(v["B"])} for k, v in agg.items()}


def print_summary(summary):
    print("\n================ 真实站点判定命中率 ================")
    print(f"{'步骤':<32}{'基线A':<16}{'时间感知B':<16}")
    for step, v in summary.items():
        a, b = v["A"], v["B"]
        sa = f"{a['ok']}/{a['n']}" + (f"~{a['partial']}*" if a["partial"] else "") + (f"(跳{a['skip']})" if a["skip"] else "")
        sb = f"{b['ok']}/{b['n']}" + (f"~{b['partial']}*" if b["partial"] else "") + (f"(跳{b['skip']})" if b["skip"] else "")
        print(f"{step:<32}{sa:<16}{sb:<16}")
    a_tot = sum(v["A"]["ok"] for v in summary.values())
    b_tot = sum(v["B"]["ok"] for v in summary.values())
    a_n = sum(v["A"]["n"] for v in summary.values())
    b_n = sum(v["B"]["n"] for v in summary.values())
    print(f"{'总计':<32}{a_tot}/{a_n}      {b_tot}/{b_n}  (*=部分命中:检测到变化但未确认)")


if __name__ == "__main__":
    asyncio.run(main())
