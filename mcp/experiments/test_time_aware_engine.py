# -*- coding: utf-8 -*-
"""时间感知引擎单元测试(离线合成事件,无浏览器)。

运行: python -m pytest mcp/experiments/test_time_aware_engine.py -q
或:    python mcp/experiments/test_time_aware_engine.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from time_aware_engine import (
    attribute_window, detect_oscillation, detect_reversion, stability_estimate,
    build_temporal_card,
)

T0 = 1_000_000.0


def evt(seq, t, etype, eid=None, semantic=None, name=None):
    e = {"seq": seq, "t": t, "type": etype, "id": eid}
    if semantic:
        e["semantic"] = semantic
    if name:
        e["name"] = name
    return e


class TestAttributeWindow(unittest.TestCase):
    def test_filters_outside_window(self):
        events = [
            evt(1, T0 - 500, "add", "el_1"),          # 动作前 → 排除
            evt(2, T0 + 100, "add", "el_2"),
            evt(3, T0 + 6100, "add", "el_3"),         # 窗口外 → 排除
        ]
        out = attribute_window(events, T0, 6000)
        self.assertEqual([e["seq"] for e in out], [2])
        self.assertEqual(out[0]["lag_ms"], 100)

    def test_dedupe_by_seq(self):
        events = [
            evt(2, T0 + 100, "add", "el_2"),
            evt(2, T0 + 100, "add", "el_2"),
        ]
        out = attribute_window(events, T0, 6000)
        self.assertEqual(len(out), 1)


class TestOscillation(unittest.TestCase):
    def test_detects_toggle_loop(self):
        # 同一 id 500ms 交替 4 次
        events = []
        for i in range(4):
            events.append(evt(10 + i * 2, T0 + 500 + i * 500, "add", "s2-el"))
            events.append(evt(11 + i * 2, T0 + 1000 + i * 500, "remove", "s2-el"))
        osc = detect_oscillation(events)
        self.assertIsNotNone(osc)
        self.assertGreaterEqual(osc["toggles"], 3)
        self.assertIn("s2-el", osc["key"])

    def test_detects_loop_across_recreated_ids(self):
        # 内核每次重建发新 id(真实行为),但名称族稳定 → 仍应判振荡
        events = []
        for i in range(4):
            events.append(evt(10 + i * 2, T0 + 500 + i * 500, "add", f"el_{25 + i}",
                              name="div.oscillating"))
            events.append(evt(11 + i * 2, T0 + 1000 + i * 500, "remove", f"el_{25 + i}",
                              name="div.oscillating"))
        osc = detect_oscillation(events)
        self.assertIsNotNone(osc)
        self.assertEqual(osc["key"], "name:div.oscillating")
        self.assertGreaterEqual(osc["toggles"], 3)

    def test_ignore_single_add(self):
        events = [evt(1, T0 + 100, "add", "el_x")]
        self.assertIsNone(detect_oscillation(events))


class TestReversion(unittest.TestCase):
    def test_detects_appear_then_remove(self):
        events = [
            evt(1, T0 + 600, "add", "s4-msg", semantic="content"),
            evt(2, T0 + 2000, "remove", "s4-msg", semantic="content"),
        ]
        revs = detect_reversion(events)["reversion"]
        self.assertEqual(len(revs), 1)
        self.assertEqual(revs[0]["lag_ms"], 1400)

    def test_no_reversion_when_stays(self):
        events = [evt(1, T0 + 600, "add", "ok", semantic="dialog")]
        self.assertEqual(detect_reversion(events)["reversion"], [])

    def test_fast_churn_is_not_reversion(self):
        # 同批/极快增删(SPA 装饰性波动)不算回退,只计 churn
        events = [
            evt(1, T0 + 100, "add", "dec", semantic="content"),
            evt(2, T0 + 180, "remove", "dec", semantic="content"),
            evt(3, T0 + 300, "add", "dec2", semantic="content"),
            evt(4, T0 + 700, "remove", "dec2", semantic="content"),
        ]
        r = detect_reversion(events)
        self.assertEqual(r["reversion"], [])
        self.assertEqual(r["churn"], 1)  # 80ms 的低于忽略线不计;400ms 的计入 churn

    def test_short_lived_decorative_is_churn(self):
        # 非高价值构件存在 600ms 就消失(< 800ms 门槛)→ 不算回退
        events = [
            evt(1, T0 + 600, "add", "tip", semantic="content"),
            evt(2, T0 + 1200, "remove", "tip", semantic="content"),
        ]
        r = detect_reversion(events)
        self.assertEqual(r["reversion"], [])
        self.assertEqual(r["churn"], 1)

    def test_short_lived_high_value_counts(self):
        # 高价值构件(dialog)短滞留 300ms 就消失 → 算回退
        events = [
            evt(1, T0 + 600, "add", "dlg", semantic="dialog"),
            evt(2, T0 + 900, "remove", "dlg", semantic="dialog"),
        ]
        r = detect_reversion(events)
        self.assertEqual(len(r["reversion"]), 1)
        self.assertEqual(r["churn"], 0)


class TestStability(unittest.TestCase):
    def test_settling_then_stable_buckets(self):
        # 三波变化后已静默超过 quiet_ms → stable(渲染完成)
        events = [
            evt(1, T0 + 300, "add", "a"),
            evt(2, T0 + 800, "add", "b"),
            evt(3, T0 + 1500, "add", "c"),
        ]
        stab = stability_estimate(events, T0)
        self.assertEqual(stab["state"], "stable")
        self.assertEqual(stab["last_event_lag_ms"], 1500)

    def test_settling_when_recent_activity(self):
        events = [evt(1, T0 + 300, "add", "a")]
        stab = stability_estimate(events, T0)
        self.assertEqual(stab["state"], "settling")

    def test_stable_when_no_events(self):
        stab = stability_estimate([], T0)
        self.assertEqual(stab["state"], "stable")

    def test_churning_high_rate(self):
        events = [evt(i, T0 + i * 50, "add", f"el_{i}") for i in range(1, 20)]
        stab = stability_estimate(events, T0)
        self.assertEqual(stab["state"], "churning")


class TestTemporalCard(unittest.TestCase):
    def test_delayed_effect(self):
        events = [evt(1, T0 + 3000, "add", "dlg", semantic="dialog")]
        card = build_temporal_card("click:s1-btn", T0, events)
        self.assertEqual(card.verdict, "effected-delayed")
        self.assertEqual(card.attributed[0]["lag_ms"], 3000)

    def test_immediate_effect(self):
        events = [evt(1, T0 + 300, "add", "dlg", semantic="dialog")]
        card = build_temporal_card("click:s1-btn", T0, events)
        self.assertEqual(card.verdict, "effected")

    def test_reversion_wins_over_effect(self):
        events = [
            evt(1, T0 + 600, "add", "msg", semantic="content"),
            evt(2, T0 + 2000, "remove", "msg", semantic="content"),
        ]
        card = build_temporal_card("click:s4-btn", T0, events)
        self.assertEqual(card.verdict, "reverted")

    def test_oscillation_wins_over_reversion(self):
        # 振荡族不能同时被计入"回退"(2Hz 交替是重绘循环,不是出现后消失)
        events = []
        for i in range(4):
            events.append(evt(10 + i * 2, T0 + 500 + i * 500, "add", f"el_{25 + i}",
                              name="div.oscillating"))
            events.append(evt(11 + i * 2, T0 + 1000 + i * 500, "remove", f"el_{25 + i}",
                              name="div.oscillating"))
        card = build_temporal_card("click:s2-btn", T0, events)
        self.assertEqual(card.verdict, "oscillation")
        self.assertEqual(card.reversion, [])

    def test_no_change(self):
        card = build_temporal_card("click:s0-btn", T0, [])
        self.assertEqual(card.verdict, "no-change")

    def test_changed_when_weak_signal(self):
        events = [evt(1, T0 + 200, "update", "btn", semantic="button")]
        card = build_temporal_card("click:x", T0, events)
        self.assertEqual(card.verdict, "changed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
