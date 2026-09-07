# -*- coding: utf-8 -*-
"""最小闭环模拟:环境持续维护 Agent 决策前提。

剧本(确定性,多步表单任务 + 两处外部前提失效注入):
  1. 填字段A = hello
  2. 填字段B = world
  3. [外部注入] 页面脚本重置字段A(受控组件场景)——事件流静默
  4. 勾选选项C
  5. [外部注入] 页面脚本取消勾选C——事件流静默
  6. 确认:检查 A/B/C 是否齐全 → 成功

两臂:
  A 无监视(现状):第 6 步确认时才暴露失效 → 重做后再次确认
  B 有监视(混合驱动):第 3/5 步注入后,监视器轮询快照发现失效 → 即时通知 → agent 立即修正
     → 第 6 步一次通过

监视器(混合驱动):
  - 事件驱动:文本/结构类前提(世界模型的 add/update/remove 事件,0 轮询成本)
  - 轮询驱动:表单值/勾选类前提(事件流全盲,低频轻量 evaluate 读真实状态)
  - 通知前事实校验:轮询/事件命中后重读真实状态确认,防噪声误报

指标:确认失败次数 / 重做步数 / 通知延迟 / 轮询成本(evaluate 次数) / 总步骤数
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(r"G:\工作区\Agent-world-mcp\agent-native-web\mcp\server.py")
FIXTURE = Path(r"G:\工作区\Agent-world-mcp\agent-native-web\tests\fixtures\premise_sim.html")

POLL_INTERVAL_S = 1.0  # 表单类前提的轮询间隔


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except Exception:
                return {"_raw_text": item.text[:200]}
    return {}


# ── 前提监视器 ─────────────────────────────────────────────
class PremiseMonitor:
    """轮询驱动的前提监视器(表单类前提)。

    assumptions: {name: {"expr": JS 读值表达式, "expect": 期望值}}
    轮询对比 expect,发现偏差 → 通知(回调),然后更新 expect 为当前值(防重复通知)。
    """

    def __init__(self, session, wid, assumptions, on_violation, interval_s=POLL_INTERVAL_S):
        self.session = session
        self.wid = wid
        self.assumptions = dict(assumptions)   # name -> {expr, expect}
        self.on_violation = on_violation
        self.interval_s = interval_s
        self.polls = 0
        self.violations = []
        self.stopped = False

    async def run(self):
        # 每假设独立监视任务:一个假设的通知处理(含 agent 修正)不阻塞其他假设
        self.tasks = [asyncio.create_task(self._watch(name, spec))
                      for name, spec in self.assumptions.items()]
        if not self.tasks:
            await asyncio.sleep(3600)

    async def _watch(self, name, spec):
        while not self.stopped:
            if not spec.get("active", True):
                await asyncio.sleep(self.interval_s)
                continue
            self.polls += 1
            try:
                current = str(await read_actual(self.session, self.wid, spec["expr"])).strip()
                expected = str(spec["expect"])
                if current != expected:
                    # 通知前事实校验:连续两次读都偏离才确认失效(防瞬时噪声)
                    await asyncio.sleep(0.3)
                    current2 = str(await read_actual(self.session, self.wid, spec["expr"])).strip()
                    if current2 != expected:
                        self.violations.append({"name": name, "expected": expected,
                                                "actual": current2,
                                                "at_ms": int(time.time() * 1000)})
                        spec["active"] = False  # 暂停该假设,等 agent 修正后 ack
                        await self.on_violation(name, expected, current2)
            except Exception:
                pass
            await asyncio.sleep(self.interval_s)

    def ack(self, name, expected):
        """agent 修正完成后显式恢复监视(替代冷却期:冷却期会误屏蔽真实失效)。"""
        spec = self.assumptions.get(name)
        if spec:
            spec["expect"] = expected
            spec["active"] = True

    def set_assumption(self, name, expr, expect):
        """注册假设并立即创建该假设的独立监视任务(动态注册场景)。"""
        self.assumptions[name] = {"expr": expr, "expect": expect, "active": True}
        task = asyncio.create_task(self._watch(name, self.assumptions[name]))
        self.tasks.append(task)

    def stop(self):
        self.stopped = True


def read_expr(doc_id, attr):
    """读元素真实状态的 JS 表达式(与内核扫描同源:读 DOM 事实)。"""
    if attr == "value":
        return f"() => (document.getElementById('{doc_id}') || {{}}).value || ''"
    if attr == "checked":
        return f"() => (document.getElementById('{doc_id}') || {{}}).checked === true ? 'true' : 'false'"


async def read_actual(session, wid, expr):
    """读真实状态并反序列化(world_eval 的 result 是 JSON 文本,字符串带引号)。"""
    r = await call(session, "world_eval", {"world_id": wid, "expression": expr}, timeout=15)
    raw = r.get("result") if isinstance(r, dict) else r
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ── 剧本执行 ────────────────────────────────────────────────
# 构件查找表:DOM id → 内核 name(由探针实测,中文界面 name 带中文标签)
NAME_BY_ID = {"field-a": "字段a", "field-b": "字段b", "opt-c": "选项c"}


async def find_and_act(session, wid, doc_id, action, text=None):
    """按内核构件名(中文标签)定位并执行动作(模拟 agent 的定位+动作)。"""
    r = await call(session, "world_entities", {"world_id": wid, "role": "input",
                                               "name": NAME_BY_ID[doc_id], "max_results": 5}, timeout=30)
    ents = r.get("entities", [])
    if not ents:
        raise RuntimeError(f"找不到 {doc_id}")
    eid = ents[0]["id"]
    if action == "fill":
        return await call(session, "world_fill", {"world_id": wid, "id": eid, "text": text}, timeout=30)
    if action == "click":
        return await call(session, "world_click", {"world_id": wid, "id": eid}, timeout=30)


async def verify(session, wid):
    """确认步:读三个控件真实状态。返回 (ok, 缺失清单)。"""
    vals = {}
    for doc_id, attr, expect in [("field-a", "value", "hello"), ("field-b", "value", "world"), ("opt-c", "checked", "true")]:
        raw = await read_actual(session, wid, read_expr(doc_id, attr))
        vals[doc_id] = str(raw).strip()
    missing = [k for k, v in vals.items() if v != {"field-a": "hello", "field-b": "world", "opt-c": "true"}[k]]
    return not missing, missing, vals


async def inject(session, wid, label, expr):
    print(f"  ⚡ 外部注入[{label}]", flush=True)
    await call(session, "world_eval", {"world_id": wid, "expression": expr}, timeout=15)


async def run_arm(session, wid, monitored):
    """执行剧本。monitored=True 时启用前提监视器。"""
    timeline = []
    steps = 0
    redo = 0
    verify_fails = 0
    monitor = None

    def tlog(msg):
        timeline.append(f"+{int(time.time() * 1000) % 100000:5d} {msg}")

    async def on_violation(name, expected, actual):
        nonlocal redo
        redo += 1
        tlog(f"⚠ 前提失效通知: {name} 期望 {expected!r} 实际 {actual!r} → 修正")
        # agent 修正动作:重填字段A 或 重勾选项C
        if name == "field-a":
            await find_and_act(session, wid, "field-a", "fill", "hello")
            tlog("✅ 已重填 field-a")
        elif name == "opt-c":
            await find_and_act(session, wid, "opt-c", "click")
            tlog("✅ 已重勾 opt-c")
        # 修正后显式恢复监视(ack),期望值回到修正后的目标
        monitor.ack(name, expected)

    if monitored:
        monitor = PremiseMonitor(session, wid, {}, on_violation)
        monitor.run_task = asyncio.create_task(monitor.run())

    # 剧本
    await find_and_act(session, wid, "field-a", "fill", "hello"); steps += 1
    tlog("step1 填 field-a=hello")
    if monitored:
        monitor.set_assumption("field-a", read_expr("field-a", "value"), "hello")
    await find_and_act(session, wid, "field-b", "fill", "world"); steps += 1
    tlog("step2 填 field-b=world")
    if monitored:
        monitor.set_assumption("field-b", read_expr("field-b", "value"), "world")
    await asyncio.sleep(0.5)
    await inject(session, wid, "重置 field-a(受控组件)", "() => { document.getElementById('field-a').value = ''; return true; }")
    await asyncio.sleep(0.5)
    await find_and_act(session, wid, "opt-c", "click"); steps += 1
    tlog("step3 勾选 opt-c")
    if monitored:
        monitor.set_assumption("opt-c", read_expr("opt-c", "checked"), "true")
    await asyncio.sleep(0.5)
    await inject(session, wid, "取消勾选 opt-c", "() => { const c = document.getElementById('opt-c'); c.checked = false; return true; }")
    await asyncio.sleep(0.5)
    # 确认步
    ok, missing, vals = await verify(session, wid)
    if not ok:
        verify_fails += 1
        tlog(f"step4 确认失败,缺失: {missing}")
        for k in missing:
            if k == "field-a":
                await find_and_act(session, wid, "field-a", "fill", "hello"); redo += 1
                tlog("✅ 重做: 重填 field-a")
            elif k == "field-b":
                await find_and_act(session, wid, "field-b", "fill", "world"); redo += 1
                tlog("✅ 重做: 重填 field-b")
            elif k == "opt-c":
                await find_and_act(session, wid, "opt-c", "click"); redo += 1
                tlog("✅ 重做: 重勾 opt-c")
        ok2, missing2, vals2 = await verify(session, wid)
        tlog(f"step4 二次确认: {'成功' if ok2 else '仍失败 ' + str(missing2)}")
    else:
        tlog("step4 确认: 一次通过")

    if monitor:
        await asyncio.sleep(0.3)
        monitor.stop()
        for t in getattr(monitor, "tasks", []):
            t.cancel()
        try:
            await asyncio.gather(*getattr(monitor, "tasks", []), return_exceptions=True)
        except Exception:
            pass
        polls = monitor.polls
        viols = len(monitor.violations)
    else:
        polls = 0
        viols = 0

    return {"steps": steps, "redo": redo, "verify_fails": verify_fails,
            "polls": polls, "violations": viols, "timeline": timeline}


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            for arm in ("A-无监视", "B-有监视"):
                opened = await call(session, "world_open", {
                    "url": FIXTURE.as_uri(), "ready_policy": "action", "wait_ms": 2000,
                    "reuse_policy": "never", "task_id": f"sim-{arm}",
                }, timeout=90)
                wid = opened["world_id"]
                print(f"\n===== {arm} =====", flush=True)
                res = await run_arm(session, wid, monitored=(arm == "B-有监视"))
                for line in res["timeline"]:
                    print("  " + line, flush=True)
                print(f"结果: steps={res['steps']} 重做={res['redo']} 确认失败={res['verify_fails']} "
                      f"轮询次数={res['polls']} 失效通知={res['violations']}", flush=True)
                await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
