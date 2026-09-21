# -*- coding: utf-8 -*-
"""LLM 试验结果分析:结果表 + Fisher 精确检验(无 scipy 依赖)。

输入:results.jsonl,每行 {"trial","scenario","arm","verdict"}
  verdict ∈ SUCCESS / FAILED / NO_VERDICT(未给出结论,不计入统计,单独计数)

用法:python llm_trial_analyze.py --results <results.jsonl>
"""
import argparse
import json
import sys
from collections import defaultdict
from math import comb

sys.stdout.reconfigure(encoding="utf-8")


def fisher_exact(a: int, b: int, c: int, d: int):
    """2x2 表 Fisher 精确检验。表:[[a,b],[c,d]]。
    返回 (one_sided, two_sided):one_sided = P(X<=a)(更小侧)。"""
    n, r1, c1 = a + b + c + d, a + b, a + c
    if n == 0 or r1 == 0 or r1 == n or c1 == 0 or c1 == n:
        return 1.0, 1.0

    def p(x):
        return comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)

    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    probs = {x: p(x) for x in range(lo, hi + 1)}
    one = sum(v for x, v in probs.items() if x <= a)
    two = sum(v for x, v in probs.items() if v <= probs.get(a, 0) + 1e-12)
    return min(one, 1.0), min(two, 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.results, encoding="utf-8") if l.strip()]
    cells = defaultdict(lambda: {"n": 0, "false": 0, "correct": 0, "no": 0, "trials": []})

    for r in rows:
        scen, arm, v = r["scenario"], r["arm"], (r.get("verdict") or "NO_VERDICT").strip().upper()
        key = (scen, arm)
        c = cells[key]
        if v == "NO_VERDICT" or v not in ("SUCCESS", "FAILED"):
            c["no"] += 1
            c["trials"].append((r["trial"], "NO_VERDICT"))
            continue
        c["n"] += 1
        c["trials"].append((r["trial"], v))
        if v == "SUCCESS":          # 三场景真值均为失败 → SUCCESS = 假成功
            c["false"] += 1
        else:
            c["correct"] += 1

    scen_order = ["reg", "pay", "task"]
    print("| 场景 | 臂 | n | 假成功 | 假成功率 | 判对 | Fisher(ui vs receipt) |")
    print("|---|---|---|---|---|---|---|")
    for scen in scen_order:
        u, rc = cells.get((scen, "ui")), cells.get((scen, "receipt"))
        one = two = None
        if u and rc and u["n"] and rc["n"]:
            # 表:[[receipt 假成功, receipt 判对],[ui 假成功, ui 判对]]
            one, two = fisher_exact(rc["false"], rc["n"] - rc["false"],
                                    u["false"], u["n"] - u["false"])
        for arm in ("ui", "receipt"):
            c = cells.get((scen, arm))
            if not c:
                continue
            fs = f"{c['false']}/{c['n']}" if c["n"] else "-"
            rate = f"{100*c['false']/c['n']:.0f}%" if c["n"] else "-"
            fish = ""
            if arm == "receipt" and one is not None:
                fish = f"one={one:.4f}, two={two:.4f}"
            print(f"| {scen} | {arm} | {c['n']} | {fs} | {rate} | {c['n']-c['false']} | {fish} |")
        for c, who in ((u, "ui"), (rc, "receipt")):
            if c and c["no"]:
                print(f"| {scen} | {who} | - | (NO_VERDICT ×{c['no']},不计入) | | | |")

    # 汇总:窗口内失败场景(reg+pay)
    u_f = u_n = r_f = r_n = 0
    for scen in ("reg", "pay"):
        for arm, f, n in (("ui", u_f, u_n), ("receipt", r_f, r_n)):
            c = cells.get((scen, arm))
            if c:
                if arm == "ui":
                    u_f += c["false"]; u_n += c["n"]
                else:
                    r_f += c["false"]; r_n += c["n"]
    if u_n and r_n:
        one, two = fisher_exact(r_f, r_n - r_f, u_f, u_n - u_f)
        print(f"\n汇总(reg+pay,窗口内失败):ui 假成功 {u_f}/{u_n},receipt 假成功 {r_f}/{r_n};"
              f"Fisher one={one:.5f}, two={two:.5f}")

    print("\n明细(逐试验判定):")
    for scen in scen_order:
        for arm in ("ui", "receipt"):
            c = cells.get((scen, arm))
            if c:
                print(f"  {scen}/{arm}: " + ", ".join(f"{t}={v[:7]}" for t, v in c["trials"]))


if __name__ == "__main__":
    main()
