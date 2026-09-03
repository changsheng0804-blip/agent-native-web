# -*- coding: utf-8 -*-
"""Agent-Native Web 质检流水线 (Quality Gate)

一条命令跑完一组测试并产出报告。设计目标:改完代码后不用人肉逐个跑脚本,
一按按钮就知道"有没有改坏东西"。

分组(按依赖与稳定性):
  offline  纯本地夹具测试(快、稳定,不依赖网络)——每次改动后必跑
  real     真实网站测试(慢,受网络/反爬影响)——功能验证时跑
  special  需要特殊环境的测试(CDP 调试端口 / 有头弹窗)——按需手动跑

用法:
  uv run python mcp/run_quality.py             # 默认:offline 组
  uv run python mcp/run_quality.py --real      # offline + real
  uv run python mcp/run_quality.py --all       # 全部三组
  uv run python mcp/run_quality.py --list      # 列出分组与脚本
  uv run python mcp/run_quality.py --only test_map.py   # 只跑某个脚本

退出码: 0=全过  1=有失败  2=前置检查失败(如 all-in-one.js 不同步)

报告: 写 mcp/quality_report.md(覆盖式),控制台同步输出汇总。
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


ROOT = pathlib.Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "mcp"
REPORT = MCP_DIR / "quality_report.md"

# ── 分组定义 ────────────────────────────────────────────────
# 个别脚本超时覆盖(真站六站体检类脚本耗时长,默认 300s 不够)
TIMEOUT_OVERRIDES = {
    "test_fingerprint_real.py": 900,  # 六站 × 2 次进站 + 指纹采样
    "test_map_real.py": 900,          # 六站地图体检
    "validate_closed_loop.py": 1200,  # 全量:真站 8 场景 + waitFor 专项
    "validate_closed_loop.py --local": 600,  # 本地 5 场景
    "test_receipt_metrics.py": 300,  # R4 度量:固定电池 ×3 独立重复,约 200s
}

# 测试条目可带参数(如 "validate_closed_loop.py --local"),运行时按空格拆分

# ── 守护面标签(scope)────────────────────────────────────────
# 每个测试标记它"守护什么代码面",用 --scope 只跑相关测试。
# 原则:改文档/README → 不用跑;改工具逻辑 → 该工具相关的 scope;改内核 → 全量。
# 全部合法 scope 可用 run_quality.py --list 查看。
SCOPES = {
    # 脚本名(不含参数) -> [守护面]
    "test_enhancements.py": ["fill", "forms", "action"],
    "test_ipi_filter.py": ["kernel", "visibility"],
    "test_wait_event.py": ["observer", "kernel"],
    "test_fingerprint.py": ["kernel", "identity"],
    "validate_closed_loop.py": ["judgment", "action"],
    "test_global_feedback.py": ["judgment", "navigation"],
    "test_channels.py": ["channels"],
    "test_guide.py": ["channels", "guide"],
    "test_visual_evidence.py": ["visual", "screenshot"],
    "test_visual_calib.py": ["visual", "judgment"],
    "test_visual_style.py": ["visual", "judgment"],
    "test_form_names.py": ["fill", "identity"],
    "test_page_outcome.py": ["judgment", "challenge"],
    "test_receipt.py": ["judgment", "receipt"],
    "test_receipt_metrics.py": ["judgment", "receipt"],
    "test_occlusion.py": ["occlusion", "action", "judgment"],
    "test_silent_failure.py": ["network", "console", "silent_failure", "judgment"],
    "test_challenge_overlay.py": ["challenge"],
    "test_assumption_e_glass.py": ["action", "judgment"],
    "test_assumption_r_cssvar.py": ["visibility", "ipi"],
    "test_assumption_b_shadow.py": ["shadow", "kernel"],
    "test_shadow_dynamic.py": ["shadow", "observer"],
    "test_fill_clear_and_text.py": ["fill", "observer"],
    # real 组
    "test_map.py": ["map", "navigation"],
    "test_map_drill.py": ["map"],
    "test_click_effect.py": ["judgment", "action"],
    "test_change_digest.py": ["channels", "digest"],
    "test_click.py": ["action", "judgment"],
    "test_fill.py": ["fill", "action"],
    "test_eval.py": ["debug"],
    "test_value.py": ["fill", "forms"],
    "test_status.py": ["status"],
    "test_gf_final.py": ["action", "fill", "navigation"],
    "test_frames.py": ["frames"],
    "test_compare.py": ["profile"],
    "test_official.py": ["general"],
    "test_action_layer.py": ["action", "fill"],
    "test_fingerprint_real.py": ["identity"],
    "test_map_real.py": ["map"],
    "test_channels_real.py": ["channels"],
}

# 别名:常用改动面 → 推荐 scope 组合("--scope 别名" 一次跑多个面)
SCOPE_ALIASES = {
    "fill": ["fill", "forms", "action"],        # 改 world_fill/batch_fill/行动层
    "observer": ["observer", "kernel"],         # 改观察器/变更感知
    "judgment": ["judgment", "challenge", "action"],  # 改 effect/page_outcome 判定
    "kernel": ["kernel", "observer", "visibility", "shadow", "identity"],  # 改内核任意处
    "channels": ["channels", "digest", "guide"],       # 改信道/导览
    "quick": ["fill", "judgment", "action"],    # 日常快速自检(1-3 分钟)
}

GROUPS = {
    "offline": {
        "desc": "本地夹具测试(不依赖网络,快速稳定)",
        "timeout": 180,
        "scripts": [
            "test_enhancements.py",   # 逐字打字/批量填表容错/状态卡 forms
            "test_ipi_filter.py",     # IPI 伪隐藏过滤 5 类阻断
            "test_wait_event.py",     # 事件驱动等待(命中/超时)
            "test_fingerprint.py",    # 稳定指纹跨会话认路
            "validate_closed_loop.py --local",  # 闭环判定一致性(本地 5 场景,fp=0 一票否决)
            "test_global_feedback.py",   # 页面整体反馈优先于局部(URL/弹窗/no-change)
            "test_channels.py",          # 三条独立信道(状态/变化摘要/操作证据)
            "test_guide.py",             # 实时任务导览接入信道
            "test_visual_evidence.py",   # SoM 标注/ImageContent/视觉 diff 兜底(正/负例)
            "test_visual_calib.py",      # P0-2 视觉阈值校准锁死(渗入/scroll-shift/回归)
            "test_visual_style.py",      # L2 样式层:WAAPI无DOM变更→style-diff结构化生效
            "test_form_names.py",      # 表单字段 name 属性定位(幽灵字段防错位)
            "test_protocol.py",        # 阶段 B 收口:默认 6 词协议(find/act/outcome + LITE 模式)
            "test_occlusion.py",       # Phase 3 遮挡归因:covered_by/at/action + unchanged 归因
            "test_page_outcome.py",    # 统一后果卡:全动作 page_outcome 五态(阶段 A)
            "test_receipt.py",         # 小票标准v0.1:全字段/sources/handoff/对账(R1)
            "test_receipt_metrics.py", # R4:小票回答旧验证#九度量(FP/FN/定位与操作时间/恢复出口)
            "test_challenge_overlay.py",  # 挑战遮罩复刻(Step 1 的验收场景;page_outcome 已实现,守护不回归)
            "test_assumption_e_glass.py", # 玻璃罩按钮:不报假成功(预期通过)
            "test_assumption_r_cssvar.py",# CSS 变量藏字:IPI 过滤(预期通过)
            "test_assumption_b_shadow.py",# Shadow DOM 穿透修复后:静态可见(预期通过,防回归)
            "test_shadow_dynamic.py", # Shadow DOM 动态感知(运行期新增/点击,预期通过)
            "test_fill_clear_and_text.py", # fill 清空语义 + 纯文本变更感知(弱模型验证发现的两个缺陷)
            "test_silent_failure.py",  # 网络与控制台静默失败监听(借鉴 Chrome DevTools MCP:422/500/console.error)
        ],
    },
    "real": {
        "desc": "真实网站测试(受网络/反爬影响,失败可能是环境问题)",
        "timeout": 300,
        "scripts": [
            "test_map.py",            # 页面地图(tabs + GitHub 真站)
            "test_map_drill.py",      # 区域钻取(tabs + GitHub 真站)
            "test_click_effect.py",   # 点击生效报告(正/负例)
            "test_change_digest.py",  # 结构化语义摘要 + 管线闭环
            "test_click.py",          # 点击 + 变更流(GF)
            "test_fill.py",           # 填表 + 生效报告(GF)
            "test_eval.py",           # world_eval 调试工具(GF)
            "test_value.py",          # 输入值回显(GF)
            "test_status.py",         # 状态卡 + 登录态(GF/闲鱼)
            "test_gf_final.py",       # GF 整套操作链路
            "test_frames.py",         # 多 frame 感知(闲鱼)
            "test_compare.py",        # profile 对比(闲鱼)
            "test_official.py",       # 官方页面抽查(HN)
            "test_action_layer.py",   # 行动层降级链路(GF)
            "test_fingerprint_real.py",  # 指纹真站六站体检
            "test_map_real.py",       # 地图真站六站体检
            "test_channels_real.py",  # 四条页面信道真站体检(GF弹窗/GitHub导览)
            "validate_closed_loop.py",  # 闭环判定一致性全量(真站 8 场景 + waitFor 专项)
        ],
    },
    "special": {
        "desc": "特殊环境(CDP 调试端口 / 有头弹窗 / 本地 HTTP 服务)",
        "timeout": 300,
        "scripts": [
            "test_cdp.py",        # 需要本地 Chrome 9222 调试端口
            "test_profile.py",    # 需要有头窗口 + 本地 8001 服务
        ],
    },
}

ORDER = ["offline", "real", "special"]


# ── 前置检查:all-in-one.js 与分文件一致性 ─────────────────────
def check_all_in_one():
    """复用构建脚本逻辑做纯对比(不写文件):manifest 顺序合并 vs 现有 all-in-one.js"""
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["content_scripts"][0]["js"]
    parts = []
    for f in files:
        parts.append(f"// ===== {f} =====")
        parts.append((ROOT / "extension" / f).read_text(encoding="utf-8"))
    expected = "\n".join(parts) + "\n"
    actual = (ROOT / "extension" / "all-in-one.js").read_text(encoding="utf-8")
    if expected != actual:
        return False, (f"all-in-one.js 与分文件不同步(应重新构建):\n"
                       f"  python {ROOT / 'extension' / 'scripts' / 'build_all_in_one.py'}\n"
                       f"  涉及文件: {files}")
    return True, "all-in-one.js 与分文件一致"


# ── 运行单个测试脚本 ─────────────────────────────────────────
def run_script(script, timeout):
    """运行单个测试脚本。script 条目可带参数(按空格拆分,如 'validate_closed_loop.py --local')"""
    parts = script.split()
    cmd = [sys.executable] + parts
    start = time.time()
    try:
        r = subprocess.run(
            cmd,
            cwd=str(MCP_DIR),
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - start
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            return {"status": "PASS", "elapsed": elapsed, "output": out}
        return {"status": "FAIL", "elapsed": elapsed, "output": out}
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start
        # 传了 encoding 时 stdout/stderr 是 str;未传时是 bytes——统一处理
        def _to_str(buf):
            if buf is None:
                return ""
            if isinstance(buf, bytes):
                return buf.decode("utf-8", errors="replace")
            return buf
        out = _to_str(e.stdout) + _to_str(e.stderr)
        return {"status": "TIMEOUT", "elapsed": elapsed, "output": out}


def summarize_output(out, status, keep_tail=25):
    lines = [l for l in out.splitlines() if l.strip()]
    if status == "PASS":
        # 通过时只留最后几行(通常是成功摘要)
        return "\n".join(lines[-5:]) if lines else "(无输出)"
    # 失败/超时时保留头尾(报错信息常在尾部;异常起点也可能在中间)
    head = "\n".join(lines[:10]) if lines else ""
    tail = "\n".join(lines[-keep_tail:]) if lines else ""
    if head == tail:
        return head
    return (head + "\n  …(中间省略)…\n" + tail) if lines else "(无输出)"


# ── 报告 ─────────────────────────────────────────────────────
def write_report(results, checks, groups_run, started):
    lines = [
        "# 质检报告 (Quality Gate)",
        "",
        f"> 运行时间:{time.strftime('%Y-%m-%d %H:%M:%S')}  耗时合计:{sum(r['elapsed'] for r in results):.1f}s",
        f"> 命令群组:{', '.join(groups_run)}",
        "",
        "## 汇总",
        "",
        "| 脚本 | 状态 | 耗时 |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['script']} | {r['status']} | {r['elapsed']:.1f}s |")
    passed = sum(1 for r in results if r["status"] == "PASS")
    lines.append("")
    lines.append(f"**通过 {passed}/{len(results)}**")
    if checks:
        lines.append("")
        lines.append("## 前置检查")
        lines.append("")
        for name, ok, msg in checks:
            lines.append(f"- {'✅' if ok else '❌'} {name}: {msg}")
    failed = [r for r in results if r["status"] != "PASS"]
    if failed:
        lines.append("")
        lines.append("## 失败详情")
        lines.append("")
        for r in failed:
            lines.append(f"### {r['script']} ({r['status']}, {r['elapsed']:.1f}s)")
            lines.append("")
            lines.append("```text")
            lines.append(summarize_output(r["output"], r["status"]))
            lines.append("```")
            lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")


# ── 主流程 ───────────────────────────────────────────────────
def _run_one(target, timeout_factor=1.0):
    """跑单个测试(供并行与串行共用)。"""
    group = next((g for g in ORDER if target in GROUPS[g]["scripts"]), "?")
    timeout = TIMEOUT_OVERRIDES.get(target) or GROUPS[group]["timeout"] if group in GROUPS else 180
    # 并行时多个浏览器共享 CPU,单测耗时会明显变长——超时按因子放宽,避免误杀
    if timeout_factor > 1.0:
        timeout = int(timeout * timeout_factor)
    display = target.split()[0]  # 报告里只显示脚本名(不带参数)
    r = run_script(target, timeout)
    r["script"], r["group"] = display, group
    return r


def main():
    ap = argparse.ArgumentParser(description="Agent-Native Web 质检流水线")
    ap.add_argument("--all", action="store_true", help="跑全部三组(offline+real+special)")
    ap.add_argument("--real", action="store_true", help="跑 offline+real 两组")
    ap.add_argument("--list", action="store_true", help="列出分组、脚本与守护面")
    ap.add_argument("--only", metavar="SCRIPT", help="只跑指定脚本(如 test_map.py)")
    ap.add_argument("--scope", metavar="SCOPE", help="只跑某守护面的测试(如 --scope fill;多面用逗号:--scope fill,observer;别名见 --list)")
    ap.add_argument("--parallel", type=int, default=1, metavar="N",
                    help="并行跑 N 个测试(默认 1=串行;offline 全量建议 3,约 13 分钟→5 分钟。注意每个测试会拉起独立浏览器,内存有限时用 2)")
    args = ap.parse_args()

    if args.list:
        for g in ORDER:
            print(f"[{g}] {GROUPS[g]['desc']}")
            for s in GROUPS[g]["scripts"]:
                name = s.split()[0]
                scopes = SCOPES.get(name, ["?"])
                print(f"    {s:<58} 守护面: {', '.join(scopes)}")
        print("\n别名(--scope 可用):")
        for k, v in SCOPE_ALIASES.items():
            print(f"    {k:<12} → {', '.join(v)}")
        return

    if args.only:
        targets = [args.only]
        groups_run = [f"only:{args.only}"]
    elif args.scope:
        # 展开别名 + 逗号分隔的面,收集覆盖这些面的所有测试。
        # 默认只扫 offline 组(纯本地、快);若显式面里有 real-only 的
        # (map/identity/frames/profile/digest/status/navigation/general/debug),
        # 才同时扫 real 组——避免 --scope quick 误拉真站全量。
        offline_only_faces = {"fill", "forms", "action", "kernel", "observer", "visibility",
                              "shadow", "ipi", "judgment", "challenge", "channels", "guide",
                              "visual", "screenshot", "receipt"}
        wanted = set()
        for part in [p.strip() for p in args.scope.split(",") if p.strip()]:
            if part in SCOPE_ALIASES:
                wanted.update(SCOPE_ALIASES[part])
            elif part in {s for v in SCOPES.values() for s in v}:
                wanted.add(part)
            else:
                print(f"⚠️ 未知 scope: {part}(见 --list 的守护面/别名)")
        include_real = bool(wanted - offline_only_faces)
        order = ORDER if include_real else ["offline"]
        targets = []
        for g in order:
            for s in GROUPS[g]["scripts"]:
                name = s.split()[0]
                scopes = SCOPES.get(name, [])
                if scopes and set(scopes) & wanted:
                    targets.append(s)
        targets = list(dict.fromkeys(targets))  # 去重保序
        groups_run = [f"scope:{args.scope}" + ("(含real)" if include_real else "")]
        if not targets:
            print(f"❌ scope={args.scope} 没有匹配到任何测试")
            sys.exit(2)
    elif args.all:
        targets = [s for g in ORDER for s in GROUPS[g]["scripts"]]
        groups_run = ORDER
    elif args.real:
        targets = [s for g in ("offline", "real") for s in GROUPS[g]["scripts"]]
        groups_run = ["offline", "real"]
    else:
        targets = list(GROUPS["offline"]["scripts"])
        groups_run = ["offline"]

    # 前置检查(仅完整流程执行;--only/--scope 跳过,便于快速调试)
    checks = []
    if not args.only and not args.scope:
        ok_all_in_one, msg_all_in_one = check_all_in_one()
        checks.append(("all-in-one.js 一致性", ok_all_in_one, msg_all_in_one))
        if not ok_all_in_one:
            print("❌ " + msg_all_in_one)
            print("请先重新构建合并文件,再跑质检。")
            sys.exit(2)

    started = time.time()
    results = []
    if args.parallel and args.parallel > 1:
        # ── 并行模式:每个测试独立子进程+独立浏览器,线程池并发提交 ──
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 并行超时放宽:多浏览器共享 CPU,单测耗时显著变长(实测 3 并行 ≈ 1.7x)
        timeout_factor = max(1.5, 0.5 * args.parallel + 1.0)
        order_index = {s.split()[0]: i for i, s in enumerate(targets)}
        with ThreadPoolExecutor(max_workers=args.parallel, thread_name_prefix="quality") as pool:
            futures = {}
            for script in targets:
                display = script.split()[0]
                print(f"\n▶ {display} (并行, 池 {args.parallel}, 超时×{timeout_factor:.1f})")
                sys.stdout.flush()
                futures[pool.submit(_run_one, script, timeout_factor)] = script
            for fut in as_completed(futures):
                r = fut.result()
                print(f"  → {r['status']}  {r['elapsed']:.1f}s  {r['script']}")
                sys.stdout.flush()
                results.append(r)
        results.sort(key=lambda r: order_index.get(r["script"], 999))  # 报告按原顺序
    else:
        # ── 串行模式(默认,行为不变) ──
        for script in targets:
            group = next((g for g in ORDER if script in GROUPS[g]["scripts"]), "?")
            timeout = TIMEOUT_OVERRIDES.get(script) or GROUPS[group]["timeout"] if group in GROUPS else 180
            display = script.split()[0]  # 报告里只显示脚本名(不带参数)
            print(f"\n▶ [{group}] {display} (超时 {timeout}s)")
            sys.stdout.flush()
            r = _run_one(script)
            results.append(r)
            print(f"  → {r['status']}  {r['elapsed']:.1f}s")

    total = time.time() - started
    print(f"\n{'=' * 56}")
    passed = sum(1 for r in results if r["status"] == "PASS")
    for r in results:
        print(f"  {r['status']:<8} {r['elapsed']:6.1f}s  {r['script']}")
    print(f"{'=' * 56}")
    print(f"通过 {passed}/{len(results)}  总耗时 {total:.1f}s" + (f"  (并行 ×{args.parallel})" if args.parallel > 1 else ""))
    write_report(results, checks, groups_run, started)
    print(f"报告已写入 {REPORT}")

    failed = [r for r in results if r["status"] != "PASS"]
    if failed:
        print(f"\n失败 {len(failed)} 个(详见报告中'失败详情'):")
        for r in failed:
            print(f"  - {r['script']} ({r['status']})")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()