# -*- coding: utf-8 -*-
"""批量起隔离 driver:每个 (场景,组,次) 一个独立 world + 独立端口。

用法:
  python mcp/experiments/harness_p2_launch.py --spec "ledger:F:1-3" --spec "async:C:1-3"
输出:每行 BRIEF_JSON,供拼子代理任务。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="append", required=True,
                    help="场景:组:范围,如 ledger:F:1-3(范围可为 1-3 或 1,4)")
    ap.add_argument("--outdir", default="")
    a = ap.parse_args()

    jobs = []
    for spec in a.spec:
        sc, grp, rng = spec.split(":")
        if "-" in rng:
            lo, hi = rng.split("-")
            runs = list(range(int(lo), int(hi) + 1))
        else:
            runs = [int(x) for x in rng.split(",")]
        for r in runs:
            jobs.append((sc, grp, r))

    procs = []
    for sc, grp, r in jobs:
        log = f"{a.outdir}/drv_{sc}_{grp}_{r}.log" if a.outdir else ""
        p = subprocess.Popen(
            [sys.executable, str(HERE / "harness_p2_driver.py"),
             "--scenario", sc, "--group", grp, "--run", str(r), "--log", log],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        procs.append((sc, grp, r, p))

    # 逐个读首行 BRIEF_JSON(每个 driver 起来约 10-30s)
    for sc, grp, r, p in procs:
        line = ""
        t0 = time.time()
        while time.time() - t0 < 180:
            line = p.stdout.readline()
            if line.startswith("BRIEF_JSON"):
                break
            if not line and p.poll() is not None:
                break
        if line.startswith("BRIEF_JSON"):
            d = json.loads(line[len("BRIEF_JSON "):])
            print("BRIEF " + json.dumps(d, ensure_ascii=False), flush=True)
        else:
            print(f"FAIL {sc}/{grp}/{r}", flush=True)

    print(f"\n共 {len(procs)} 个 driver 已就绪(按 Ctrl+C 或 kill 清理)", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for _, _, _, p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
