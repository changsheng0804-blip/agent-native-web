# -*- coding: utf-8 -*-
"""stdio MCP 运行时日志探针。

MCP 的 stdio 传输把 stdout 用作协议流；运行时诊断应使用 logging
并写向 stderr。本探针只检查会作为 MCP server 运行时加载的目标模块，
用于 Draft PR 的专项验收，不自动加入默认离线门禁。
"""
import ast
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

MCP_DIR = Path(__file__).resolve().parent.parent
TARGETS = [
    MCP_DIR / "server.py",
    MCP_DIR / "aw_query.py",
]


def find_print_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            found.append(node.lineno)
    return found


def main():
    violations = []
    for path in TARGETS:
        for lineno in find_print_calls(path):
            violations.append(f"{path.relative_to(MCP_DIR.parent)}:{lineno}")

    if violations:
        print("发现 stdio 运行时 print()，应改用 logging -> stderr：", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        raise SystemExit(1)

    print("stdio 运行时未发现 print()；日志边界符合预期")


if __name__ == "__main__":
    main()
