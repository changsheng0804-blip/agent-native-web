# -*- coding: utf-8 -*-
"""按 manifest.json 顺序合并 content_scripts -> all-in-one.js"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
files = manifest["content_scripts"][0]["js"]

parts = []
for f in files:
    parts.append(f"// ===== {f} =====")
    parts.append((ROOT / f).read_text(encoding="utf-8"))

out = "\n".join(parts) + "\n"
(ROOT / "all-in-one.js").write_text(out, encoding="utf-8")
print(f"merged {len(files)} files -> all-in-one.js ({len(out)} chars)")