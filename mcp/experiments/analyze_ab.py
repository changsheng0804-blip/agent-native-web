# -*- coding: utf-8 -*-
"""分析 A/B 报告:B 臂时间线/前提工具的使用模式。"""
import json

r = json.load(open(r"G:\工作区\Agent-world-mcp\agent-native-web\mcp\experiments\artifacts\timeline_ab_report.json", encoding="utf-8"))
for run in r["runs"]:
    if run["arm"] != "B":
        continue
    tl = [t for t in run["trace"] if t["name"] == "world_timeline"]
    pa = [t for t in run["trace"] if t["name"].startswith("world_a")]
    print(f"{run['task']:<14} r{run['round']} {'PASS' if run['ok'] else 'FAIL'} "
          f"steps={run['steps']} tl={len(tl)} premise_tools={len(pa)}")
    for t in tl:
        print("   timeline:", t["args"][:90])
    for t in pa:
        print("   assume:", t["args"][:100])
