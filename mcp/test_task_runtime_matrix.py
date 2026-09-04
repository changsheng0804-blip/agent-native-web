# -*- coding: utf-8 -*-
"""填写资料并提交：固定覆盖矩阵与跨会话候选图测试。"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from task_runtime import build_graph, normalize_page_state, validate_transition


SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "task_submission.html"

# 这是固定覆盖矩阵，不是随机探索。
CASES = (
    {"name": "normal-submit", "mode": "ok", "expected": "progressed"},
    {"name": "backend-rejected", "mode": "error", "expected": "errored"},
    {"name": "challenge-required", "mode": "challenge", "expected": "challenged"},
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/task-form"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FIXTURE.read_bytes())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path.startswith("/api/submit-profile?mode=error"):
            self.send_response(422)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "code": "PROFILE_REJECTED",
                "message": "资料校验未通过",
            }, ensure_ascii=False).encode("utf-8"))
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):
        pass


async def call(session, name, args):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=60)
    return json.loads(result.content[0].text)


async def run_case(session, base_url, case):
    opened = await call(session, "world_open", {
        "url": f"{base_url}/task-form?mode={case['mode']}",
        "wait_ms": 300,
        "task_goal": "填写资料并提交",
    })
    wid = opened["world_id"]
    try:
        found = await call(session, "world_find", {
            "world_id": wid,
            "role": "input",
            "max_results": 10,
        })
        fields = []
        for item in found["matches"]:
            name = item.get("name") or "input"
            text = "alice@example.com" if "email" in name else "alice"
            if "password" in name:
                text = "not-a-real-secret"
            fields.append({"id": item["id"], "text": text})
        assert len(fields) == 3, fields

        fill_card = await call(session, "world_act", {
            "world_id": wid,
            "kind": "batch_fill",
            "fields": fields,
            "operation": "填写资料",
        })
        assert fill_card["page_outcome"] == "progressed", fill_card

        submit = await call(session, "world_find", {
            "world_id": wid,
            "q": "提交资料",
            "role": "button",
            "interactive": True,
        })
        button = next(item["id"] for item in submit["matches"])
        result = await call(session, "world_act", {
            "world_id": wid,
            "kind": "click",
            "id": button,
            "operation": "提交资料",
        })
        assert result["page_outcome"] == case["expected"], result
        trace = await call(session, "world_trace", {"world_id": wid})
        assert len(trace["traces"]) == 2, trace
        encoded = json.dumps(trace, ensure_ascii=False)
        assert "not-a-real-secret" not in encoded
        return trace["traces"]
    finally:
        await call(session, "world_close", {"world_id": wid})


async def main():
    sock = __import__("socket").socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{port}"

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            traces = []
            for case in CASES:
                traces.extend(await run_case(session, base_url, case))

            graph = build_graph(traces, task_id="matrix-task", goal="填写资料并提交")
            outcomes = {
                edge["effects"].get("page_outcome")
                for edge in graph["edges"]
            }
            assert {"progressed", "errored", "challenged"}.issubset(outcomes), outcomes
            assert graph["status"] == "candidate"
            assert graph["trace_count"] == len(CASES) * 2

            submit_edges = [edge for edge in graph["edges"] if edge["operation"] == "提交资料"]
            assert len(submit_edges) == 3, submit_edges
            empty = next(
                state["snapshot"] for state in graph["states"]
                if state["snapshot"].get("form_state") == "empty"
                and state["snapshot"].get("outcome_hint") is None
            )
            denied = validate_transition(empty, submit_edges[0])
            assert denied["allowed"] is False, denied

            # 同一成功场景再次执行，规范化后的状态和边身份必须保持一致。
            replay = await run_case(session, base_url, CASES[0])
            replay_graph = build_graph(replay, task_id="replay-task", goal="填写资料并提交")
            assert [s["state_key"] for s in graph["states"] if s["snapshot"].get("outcome_hint") is None]
            replay_keys = {s["state_key"] for s in replay_graph["states"]}
            original_ok_keys = {
                s["state_key"] for s in graph["states"]
                if s["snapshot"].get("outcome_hint") is None
            }
            assert replay_keys.issubset(original_ok_keys), (replay_keys, original_ok_keys)

    httpd.shutdown()
    print("固定覆盖矩阵、非法迁移拦截和成功路径回放测试通过")


if __name__ == "__main__":
    asyncio.run(main())

