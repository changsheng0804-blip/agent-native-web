# -*- coding: utf-8 -*-
"""Fixture 环境适配器：reset 确定性、真实浏览器交互、Harness-only 通道。

隔离原则：
  - `snapshot()/verifier()/hiddenJudge()` 只由 Harness 调用；
  - hiddenJudge 永不进入模型上下文；
  - 模型只能通过真实 mouse/pointer/keyboard 事件驱动业务动作，
    不允许调用 fixture 内部 action 函数伪造 Computer Use。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import protocol

# 只有 Harness 能调用的页面接口
HARNESS_API = ("reset", "snapshot", "verifier", "hiddenJudge")

# 业务动作按钮选择器（各 fixture 的唯一业务入口）
BUSINESS_BUTTONS = {
    "silent-422": "#submit",
    "optimistic-rollback": "#save",
    "duplicate-danger": "#send",
    "false-causality": "#refresh",
}
CHECKBOX_SELECTOR = "#notifications"


class InfrastructureError(RuntimeError):
    """环境/基础设施故障 —— 必须记 invalid_run，不得计为模型失败。"""


class FixtureEnvironment:
    """一个 fixture 页面；每个 run 独立实例，杜绝状态继承。"""

    def __init__(self, protocol_data: dict, task_id: str, headless: bool = True):
        self.protocol = protocol_data
        self.task_id = task_id
        self.task = protocol.task_spec(protocol_data, task_id)
        self.fixture = protocol.fixture_path(protocol_data, task_id)
        self.headless = headless
        self._pw = None
        self._browser = None
        self._page = None

    # ── 生命周期 ─────────────────────────────────────────────
    def open(self):
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._page = self._browser.new_page(viewport={"width": 1280, "height": 800})
            self._page.goto(self.fixture.as_uri())
            self._page.wait_for_function("() => !!window.verificationBench")
        except Exception as exc:
            raise InfrastructureError(f"环境启动失败：{type(exc).__name__}: {exc}") from exc
        return self

    def close(self):
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._browser = self._page = self._pw = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    # ── Harness-only 通道 ────────────────────────────────────
    def reset(self) -> dict:
        """重置 fixture 到协议初始状态，返回初始快照。"""
        try:
            snap = self._page.evaluate("() => window.verificationBench.reset()")
        except Exception as exc:
            raise InfrastructureError(f"reset 失败：{type(exc).__name__}: {exc}") from exc
        if not isinstance(snap, dict):
            raise InfrastructureError(f"reset 未返回 snapshot：{snap!r}")
        return snap

    def snapshot(self) -> dict:
        return self._page.evaluate("() => window.verificationBench.snapshot()")

    def verifier(self) -> dict:
        return self._page.evaluate("() => window.verificationBench.verifier()")

    def hidden_judge(self) -> dict:
        return self._page.evaluate("() => window.verificationBench.hiddenJudge()")

    def verifier_packet(self) -> str:
        """B/C 收到的 verifier packet 原文（canonical JSON，用于等价性比对）。"""
        return json.dumps(self.verifier(), ensure_ascii=False, sort_keys=True)

    # ── 真实交互通道（不调用 fixture 内部 action 函数）──────
    def click_business_button(self, timeout_ms: int = 5000) -> dict:
        """用真实鼠标点击业务按钮。返回点击前后的 action_seq。"""
        selector = BUSINESS_BUTTONS[self.task_id]
        before = int(self.snapshot().get("action_seq", 0))
        try:
            self._page.click(selector, timeout=timeout_ms)
        except Exception as exc:
            raise InfrastructureError(f"真实点击失败（{selector}）：{type(exc).__name__}: {exc}") from exc
        after = int(self.snapshot().get("action_seq", 0))
        return {"selector": selector, "action_seq_before": before, "action_seq_after": after,
                "triggered": after != before}

    def toggle_checkbox(self, checked: bool = True, timeout_ms: int = 5000) -> dict:
        """用真实鼠标点击复选框（optimistic-rollback 需要先开启开关）。"""
        before = self._page.evaluate(
            f"() => document.querySelector('{CHECKBOX_SELECTOR}').checked")
        try:
            if bool(before) != bool(checked):
                self._page.click(CHECKBOX_SELECTOR, timeout=timeout_ms)
        except Exception as exc:
            raise InfrastructureError(f"真实点击复选框失败：{type(exc).__name__}: {exc}") from exc
        after = self._page.evaluate(
            f"() => document.querySelector('{CHECKBOX_SELECTOR}').checked")
        return {"checked_before": bool(before), "checked_after": bool(after)}

    def press_key(self, key: str) -> dict:
        try:
            self._page.keyboard.press(key)
        except Exception as exc:
            raise InfrastructureError(f"按键失败：{type(exc).__name__}: {exc}") from exc
        return {"ok": True, "key": key}

    # ── 坐标级 Computer Use（H1：模型自己决定点哪里）────────
    def click_at(self, x: float, y: float, timeout_ms: int = 5000) -> dict:
        """真实鼠标点击屏幕坐标。返回 action_seq 变化（业务 checkpoint 判定）。"""
        before = int(self.snapshot().get("action_seq", 0))
        try:
            self._page.mouse.click(float(x), float(y))
        except Exception as exc:
            raise InfrastructureError(f"坐标点击失败（{x},{y}）：{type(exc).__name__}: {exc}") from exc
        after = int(self.snapshot().get("action_seq", 0))
        return {"x": float(x), "y": float(y), "action_seq_before": before,
                "action_seq_after": after, "triggered": after != before}

    def mouse_move(self, x: float, y: float) -> dict:
        try:
            self._page.mouse.move(float(x), float(y))
        except Exception as exc:
            raise InfrastructureError(f"移动失败：{type(exc).__name__}: {exc}") from exc
        return {"ok": True, "x": float(x), "y": float(y)}

    def mouse_drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> dict:
        before = int(self.snapshot().get("action_seq", 0))
        try:
            self._page.mouse.move(float(from_x), float(from_y))
            self._page.mouse.down()
            self._page.mouse.move(float(to_x), float(to_y))
            self._page.mouse.up()
        except Exception as exc:
            raise InfrastructureError(f"拖拽失败：{type(exc).__name__}: {exc}") from exc
        after = int(self.snapshot().get("action_seq", 0))
        return {"from": [float(from_x), float(from_y)], "to": [float(to_x), float(to_y)],
                "action_seq_before": before, "action_seq_after": after,
                "triggered": after != before}

    def hit_test_business_target(self, x: float, y: float) -> bool:
        """Harness-only 命中测试：该坐标是否落在业务按钮/复选框上。

        用于 C 臂在**动作发生前**判定「这是不是一个不可逆业务 intent」，
        从而让 gate 能真正前置阻断。此结果不进入模型上下文。
        """
        selector = BUSINESS_BUTTONS[self.task_id]
        try:
            return bool(self._page.evaluate(
                """([sel, x, y]) => {
                     const el = document.elementFromPoint(x, y);
                     if (!el) return false;
                     const target = document.querySelector(sel);
                     const box = document.querySelector('#notifications');
                     return (target && (el === target || target.contains(el)))
                         || (box && (el === box || box.contains(el)));
                   }""", [selector, float(x), float(y)]))
        except Exception:
            return False

    def screenshot_png(self) -> bytes:
        try:
            return self._page.screenshot(full_page=False)
        except Exception as exc:
            raise InfrastructureError(f"截图失败：{type(exc).__name__}: {exc}") from exc

    def wait_ms(self, ms: int):
        time.sleep(max(0, int(ms)) / 1000)

    # ── 隔离探测 ─────────────────────────────────────────────
    def probe_leaks(self) -> dict:
        """检查页面是否把 Harness-only 内容渲染进可见 DOM（泄漏探测）。"""
        return self._page.evaluate("""
        () => {
          const text = document.body ? document.body.innerText : '';
          const html = document.documentElement ? document.documentElement.outerHTML : '';
          const needles = ['hiddenJudge', 'authoritative_state', 'authoritativeState',
                           'verificationBench', 'delivery_count', 'report_version',
                           'application_submitted', 'persisted'];
          const visibleHits = needles.filter(n => text.includes(n));
          const inScripts = needles.filter(n => html.includes(n));
          return {
            body_text: text,
            visible_leaks: visibleHits,
            html_contains: inScripts,
            window_keys: Object.keys(window).filter(k => /verification|bench/i.test(k)),
          };
        }
        """)

    def enumerate_model_visible_globals(self) -> list:
        """模型若做 window 枚举能看到的名字（用于证明 hidden judge 不在其中）。"""
        return self._page.evaluate("() => Object.keys(window).filter(k => /verification|bench/i.test(k))")

    def try_harness_api_from_page(self, name: str) -> dict:
        """模拟模型尝试越权调用 Harness-only 接口（仅用于负向证据）。"""
        if name not in HARNESS_API:
            raise SystemExit(f"未知 Harness 接口 {name!r}")
        return self._page.evaluate(
            "(n) => { try { return {ok: true, value: window.verificationBench[n]()}; }"
            " catch (e) { return {ok: false, error: String(e)}; } }", name)
