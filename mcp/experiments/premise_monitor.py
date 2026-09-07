# -*- coding: utf-8 -*-
"""前提监视器(正式版)——从最小模拟提炼。

机制(经模拟排除不合理项后确认):
  1. 轮询驱动:表单值/勾选类变化对内核事件流全盲(捕获矩阵实测),必须轮询真实状态
  2. 每假设独立并发任务:一个假设的通知处理(含 agent 修正)不阻塞其他假设
  3. 通知前双重校验:连续两次读都偏离才确认失效(防瞬时噪声)
  4. ack 协议:通知后暂停该假设,agent 修正后显式 ack 恢复(冷却期会误屏蔽真实失效,已排除)
  5. 动态注册:假设随步骤推进注册,注册即建监视任务

与实验脚本的接口:
  monitor.assume(name, expr, expect)    注册假设(立即开始监视)
  monitor.ack(name, expect)             agent 修正后恢复
  monitor.pending                       [失效通知],实验脚本注入 LLM 上下文
  monitor.polls / monitor.violations    成本与事件统计
"""
from __future__ import annotations

import asyncio
import json
import time


async def read_actual(session, wid, expr):
    """读真实状态并反序列化。

    world_eval 返回 {"result": text},text 是 json.dumps(result) 的结果——
    字符串值会再带一层引号(如 '"北京"'),必须二次反序列化(模拟阶段实测的坑)。
    """
    result = await asyncio.wait_for(session.call_tool("world_eval", {"world_id": wid, "expression": expr}), timeout=15)
    text = None
    for item in result.content:
        if getattr(item, "type", None) == "text":
            text = item.text
            break
    if text is None:
        return None
    try:
        payload = json.loads(text)
        raw = payload.get("result") if isinstance(payload, dict) else payload
    except Exception:
        raw = text
    if raw is None:
        return None
    try:
        return json.loads(raw)   # 第二层:result 字段是 JSON 编码的字符串
    except Exception:
        return raw


class PremiseMonitor:
    """环境侧前提监视器:维护 agent 提交的决策前提,失效即产生通知。"""

    def __init__(self, session, wid, on_violation=None, interval_s=1.0):
        self.session = session
        self.wid = wid
        self.on_violation = on_violation or (lambda *a, **k: None)
        self.interval_s = interval_s
        self.assumptions = {}
        self.tasks = []
        self.pending = []          # 未消费的失效通知(实验脚本注入 LLM)
        self.violations = []       # 全部失效记录
        self.polls = 0
        self.stopped = False

    # ── 协议:假设生命周期 ──────────────────────────────
    def assume(self, name, expr, expect):
        """agent 提交假设:name 唯一,expr 是读真实状态的 JS,expect 是期望值。"""
        self.assumptions[name] = {"expr": expr, "expect": str(expect), "active": True}
        self.tasks.append(asyncio.create_task(self._watch(name, self.assumptions[name])))

    def ack(self, name, expect=None):
        """agent 修正后显式恢复监视(替代冷却期——冷却期会误屏蔽冷却窗口内的真实失效)。"""
        spec = self.assumptions.get(name)
        if spec:
            if expect is not None:
                spec["expect"] = str(expect)
            spec["active"] = True

    def dismiss(self, name):
        """任务结束/前提不再相关:停止监视该假设。"""
        spec = self.assumptions.pop(name, None)
        if spec:
            spec["active"] = False

    # ── 监视循环 ──────────────────────────────────────
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
                    # 双重校验:0.3s 后再读一次,仍偏离才确认(防瞬时噪声)
                    await asyncio.sleep(0.3)
                    current2 = str(await read_actual(self.session, self.wid, spec["expr"])).strip()
                    if current2 != expected:
                        violation = {"name": name, "expected": expected, "actual": current2,
                                     "at_ms": int(time.time() * 1000)}
                        self.violations.append(violation)
                        self.pending.append(violation)
                        spec["active"] = False  # 等 agent ack
                        await self.on_violation(name, expected, current2)
            except Exception:
                pass
            await asyncio.sleep(self.interval_s)

    def stop(self):
        self.stopped = True
        for t in self.tasks:
            t.cancel()

    def stats(self):
        return {"polls": self.polls, "violations": len(self.violations),
                "pending": len(self.pending)}
