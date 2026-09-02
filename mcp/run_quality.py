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
}

# 测试条目可带参数(如 "validate_closed_loop.py --local"),运行时按空格拆分

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
            "test_form_names.py",      # 表单字段 name 属性定位(幽灵字段防错位)
            "test_challenge_overlay.py",  # 挑战遮罩盲区复现(Step 1 的验收场景;当前记录盲区,page_outcome 实现后转绿)
            "test_assumption_e_glass.py", # 玻璃罩按钮:不报假成功(预期通过)
            "test_assumption_r_cssvar.py",# CSS 变量藏字:IPI 过滤(预期通过)
            "test_assumption_b_shadow.py",# Shadow DOM 穿透修复后:静态可见(预期通过,防回归)
            "test_shadow_dynamic.py", # Shadow DOM 动态感知(运行期新增/点击,预期通过)
            "test_page_outcome.py",  # page_outcome 五态(challenged 正例/弹窗不误判/负例保守)
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
def main():
    ap = argparse.ArgumentParser(description="Agent-Native Web 质检流水线")
    ap.add_argument("--all", action="store_true", help="跑全部三组(offline+real+special)")
    ap.add_argument("--real", action="store_true", help="跑 offline+real 两组")
    ap.add_argument("--list", action="store_true", help="列出分组与脚本")
    ap.add_argument("--only", metavar="SCRIPT", help="只跑指定脚本(如 test_map.py)")
    args = ap.parse_args()

    if args.list:
        for g in ORDER:
            print(f"[{g}] {GROUPS[g]['desc']}")
            for s in GROUPS[g]["scripts"]:
                print(f"    {s}")
        return

    if args.only:
        targets = [args.only]
        groups_run = [f"only:{args.only}"]
    elif args.all:
        targets = [s for g in ORDER for s in GROUPS[g]["scripts"]]
        groups_run = ORDER
    elif args.real:
        targets = [s for g in ("offline", "real") for s in GROUPS[g]["scripts"]]
        groups_run = ["offline", "real"]
    else:
        targets = list(GROUPS["offline"]["scripts"])
        groups_run = ["offline"]

    # 前置检查(仅完整流程执行;--only 单脚本时跳过,便于快速调式)
    checks = []
    if not args.only:
        ok_all_in_one, msg_all_in_one = check_all_in_one()
        checks.append(("all-in-one.js 一致性", ok_all_in_one, msg_all_in_one))
        if not ok_all_in_one:
            print("❌ " + msg_all_in_one)
            print("请先重新构建合并文件,再跑质检。")
            sys.exit(2)

    started = time.time()
    results = []
    for script in targets:
        group = next((g for g in ORDER if script in GROUPS[g]["scripts"]), "?")
        timeout = TIMEOUT_OVERRIDES.get(script) or GROUPS[group]["timeout"] if group in GROUPS else 180
        display = script.split()[0]  # 报告里只显示脚本名(不带参数)
        print(f"\n▶ [{group}] {display} (超时 {timeout}s)")
        sys.stdout.flush()
        r = run_script(script, timeout)
        r["script"], r["group"] = display, group
        results.append(r)
        print(f"  → {r['status']}  {r['elapsed']:.1f}s")

    total = time.time() - started
    print(f"\n{'=' * 56}")
    passed = sum(1 for r in results if r["status"] == "PASS")
    for r in results:
        print(f"  {r['status']:<8} {r['elapsed']:6.1f}s  {r['script']}")
    print(f"{'=' * 56}")
    print(f"通过 {passed}/{len(results)}  总耗时 {total:.1f}s")
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