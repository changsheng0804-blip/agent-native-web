# -*- coding: utf-8 -*-
"""时间感知反馈引擎。

从 world-kernel 的时间状态纪律中提取、并在网页操作环里重构的四条机制:
  1. 时间轴即事实 —— 内核变更事件自带 (seq, t, type, id),引擎消费它而不是再拍两张快照
  2. 动作归属   —— 以动作时间为锚,把动作后时间窗内的变化序列归因到该动作(含延时生效)
  3. 振荡检测   —— 同一身份高频 add/remove 交替 = 反爬/重绘循环信号(替代"世界缩水"模糊判断)
  4. 回退/过期  —— 窗口内"已出现又被移除" = 静默回退,主动标记(替代"确认后就再也不看")

纯函数设计,不依赖浏览器与 server;输入是变更事件流(与 world_changes 返回结构一致),
输出一张 temporal card(时间后果卡)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

# 强信号语义角色(与 server._DIGEST_HIGH_ROLES 同口径,独立声明避免依赖脏工作区)
HIGH_SEMANTICS = {
    "dialog", "alertdialog", "menu", "combobox", "option", "listbox",
    "input", "textbox", "searchbox", "select", "tooltip", "tab",
}

DEFAULT_WINDOW_MS = 6000      # 动作归属观察窗(基线 _wait_click_effect 只有 2500ms)
LATE_EFFECT_THRESHOLD_MS = 1500  # 超过该滞后即视为"延时生效"
OSCILLATION_MIN_TOGGLES = 3   # 最少 add/remove 交替次数才判振荡
OSCILLATION_WINDOW_MS = 4000
SETTLE_QUIET_MS = 1000        # 连续无事件多久视为"静默"
RATE_BUCKET_MS = 500


def _evt_key(evt: dict) -> str:
    """事件归属身份:优先内核 id,退而求其次用 name 稳定串(动态元素可能无稳定 id)。"""
    eid = evt.get("id")
    if eid:
        return f"id:{eid}"
    name = evt.get("name") or ""
    if name:
        return f"name:{name}"
    return f"seq:{evt.get('seq')}"


def _family_key(evt: dict) -> str:
    """构件"家族"身份:按名称(标签+文本)识别。

    内核每次重建 DOM 节点都发新 id(el_25 → el_26 → …),重绘循环/振荡在
    id 维度上不可见;但同一位置的同名构件 name 稳定,按家族才能聚合出振荡。
    """
    name = evt.get("name") or ""
    if name:
        return f"name:{name}"
    return _evt_key(evt)


def _is_high_value(evt: dict) -> bool:
    etype = evt.get("type")
    if etype not in ("add", "remove"):
        return False
    semantic = (evt.get("semantic") or "").split(".")[0]
    return semantic in HIGH_SEMANTICS or not semantic  # 无语义时保守算高价值


def attribute_window(events: list[dict], action_t: float, window_ms: int = DEFAULT_WINDOW_MS,
                     window_end: float | None = None) -> list[dict]:
    """以动作时间为锚,把 (action_t, window_end] 内的事件归因到该动作。

    事件自带内核时间戳 t(ms epoch);动作时刻由外部传入(与 t 同基准)。
    长阻塞动作(导航等待可达数十秒)时,效果发生时间远超 action_t+window_ms,
    调用方可显式传 window_end 覆盖终点;缺省 window_end = action_t + window_ms。
    返回附 lag_ms 的窗口事件(按 seq 排序,去重)。
    """
    end = window_end if window_end is not None else action_t + window_ms
    seen = set()
    out = []
    for evt in events:
        t = evt.get("t")
        if not t or t <= action_t or t > end:
            continue
        seq = evt.get("seq")
        if seq is not None and seq in seen:
            continue
        if seq is not None:
            seen.add(seq)
        evt = dict(evt)
        evt["lag_ms"] = int(t - action_t)
        out.append(evt)
    out.sort(key=lambda e: e.get("seq") or 0)
    return out


def detect_oscillation(events: list[dict], window_ms: int = OSCILLATION_WINDOW_MS,
                       min_toggles: int = OSCILLATION_MIN_TOGGLES) -> dict | None:
    """同一"家族"(名称族,跨重建)在窗口内 add/remove 交替达到阈值 → 振荡信号。

    内核每次重建 DOM 节点都发新 id,振荡只能按名称族聚合;
    返回 {key, toggles, first_t, last_t, hz, ids} 或 None。
    """
    from collections import defaultdict
    by_key: dict[str, list[dict]] = defaultdict(list)
    for evt in events:
        by_key[_family_key(evt)].append(evt)
    best = None
    for key, evts in by_key.items():
        evts.sort(key=lambda e: e.get("seq") or 0)
        toggles = 0
        prev_type = None
        first_t = None
        last_t = None
        for evt in evts:
            if evt.get("type") not in ("add", "remove"):
                continue
            t = evt.get("t") or 0
            if prev_type is None or prev_type != evt.get("type"):
                toggles += 1
                prev_type = evt.get("type")
                if first_t is None:
                    first_t = t
                last_t = t
        if toggles >= min_toggles and last_t and first_t and (last_t - first_t) > 0:
            hz = toggles / ((last_t - first_t) / 1000)
            cand = {"key": key, "toggles": toggles, "first_t": first_t, "last_t": last_t,
                    "hz": round(hz, 2), "ids": [e.get("id") for e in evts if e.get("id")]}
            if best is None or cand["toggles"] > best["toggles"]:
                best = cand
    return best


def stability_estimate(events: list[dict], t0: float, quiet_ms: int = SETTLE_QUIET_MS) -> dict:
    """按 500ms 桶统计事件速率,判定页面变化趋势。

    返回 {state, buckets, last_event_t, quiet_since_ms}:
      stable   — 末次事件后已静默 >= quiet_ms(渲染完成/已稳定)
      churning — 近 1.5s 内仍有高频变化(>=4 事件/桶)
      settling — 有变化但速率不高,尚未达到静默门槛
    """
    buckets: dict[int, int] = {}
    last_event_t = None
    for evt in events:
        t = evt.get("t") or 0
        if t <= t0:
            continue
        bucket = int((t - t0) / RATE_BUCKET_MS)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        if last_event_t is None or t > last_event_t:
            last_event_t = t
    if not buckets:
        return {"state": "stable", "buckets": [], "last_event_t": None, "quiet_since_ms": None}
    quiet_since_ms = int(last_event_t - t0) if last_event_t else None
    recent = [buckets.get(i, 0) for i in range(max(buckets) - 2, max(buckets) + 1) if i >= 0]
    if quiet_since_ms is not None and quiet_since_ms >= quiet_ms:
        state = "stable"
    elif max(recent) >= 4:
        state = "churning"
    else:
        state = "settling"
    return {"state": state, "buckets": list(buckets.values()),
            "last_event_t": last_event_t,
            "last_event_lag_ms": quiet_since_ms,
            "quiet_since_ms": quiet_since_ms}


# 回退质量门禁(真实 SPA 页装饰性元素高频"出现即消失"是常态,不能算回退)
REVERSION_MIN_LAG_MS = 800       # 一般构件:至少存在这么久再消失才算"出现过又被撤掉"
REVERSION_HIGH_VALUE_MIN_LAG_MS = 150  # 高价值构件(dialog/option/菜单等):短滞留也算信号
CHURN_LAG_MS = 150               # 低于该值(同批 add+remove)连 churn 都不算,直接忽略


def _is_high_value_evt(evt: dict) -> bool:
    semantic = (evt.get("semantic") or "").split(".")[0]
    return semantic in HIGH_SEMANTICS


def detect_reversion(events: list[dict], exclude_families: set[str] | None = None) -> dict:
    """窗口内"同一身份 add 后又 remove" = 静默回退(出现→确认→又没了)。

    exclude_families: 已判振荡的名称族不重复计入回退(交替是重绘循环,不是"出现后回退")。
    质量门禁:快闪噪声(lag < CHURN_LAG_MS)忽略;非高价值构件需存在 >= REVERSION_MIN_LAG_MS;
    高价值构件(dialog/option/menu 等)短滞留(>= REVERSION_HIGH_VALUE_MIN_LAG_MS)也算信号。
    返回 {"reversion": [...], "churn": int} — churn 单独计数供诊断,不参与判定。
    """
    from collections import defaultdict
    by_key: dict[str, list[dict]] = defaultdict(list)
    for evt in events:
        if exclude_families and _family_key(evt) in exclude_families:
            continue
        by_key[_evt_key(evt)].append(evt)
    revs = []
    churn = 0
    for key, evts in by_key.items():
        adds = [e for e in evts if e.get("type") == "add"]
        rems = [e for e in evts if e.get("type") == "remove"]
        for add in adds:
            for rem in rems:
                if (rem.get("seq") or 0) > (add.get("seq") or 0):
                    lag = int((rem.get("t") or 0) - (add.get("t") or 0))
                    if lag < CHURN_LAG_MS:
                        continue  # 同批增删,连快闪都算不上
                    high = _is_high_value_evt(add) or _is_high_value_evt(rem)
                    if lag < REVERSION_MIN_LAG_MS and not high:
                        churn += 1
                        continue
                    if high and lag < REVERSION_HIGH_VALUE_MIN_LAG_MS:
                        churn += 1
                        continue
                    revs.append({
                        "key": key,
                        "id": add.get("id") or rem.get("id"),
                        "add_seq": add.get("seq"), "remove_seq": rem.get("seq"),
                        "add_t": add.get("t"), "remove_t": rem.get("t"),
                        "lag_ms": lag,
                    })
                    break
    revs.sort(key=lambda r: r.get("remove_t") or 0)
    return {"reversion": revs, "churn": churn}


@dataclass
class TemporalCard:
    """时间后果卡:动作的时间轴事实摘要 + 判定。"""
    action: str
    action_t: float
    window_ms: int
    window_end_ms: float | None = None
    window_events: int = 0
    attributed: list = field(default_factory=list)      # 高价值归属事件(带 lag_ms)
    oscillation: dict | None = None
    stability: dict = field(default_factory=dict)
    reversion: list = field(default_factory=list)       # 通过质量门禁的回退(判定用)
    churn: int = 0                                      # 快闪噪声计数(诊断用,不参与判定)
    verdict: str = "unknown"
    confidence: str = "low"
    why: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def build_temporal_card(action: str, action_t: float, events: list[dict],
                        window_ms: int = DEFAULT_WINDOW_MS,
                        window_end: float | None = None) -> TemporalCard:
    """从窗口事件流构建时间后果卡并给出判定。

    判定优先级:
      reverted    — 出现后又回退(比任何"成功"信号更重要,防止基于过期确认继续行动)
      oscillation — 同一身份高频交替(反爬/重绘循环,非正常效果)
      effected    — 有高价值归属事件(lag>阈值时为 effected-delayed)
      changed     — 有结构变化但性质不明
      no-change   — 窗口内无任何变化
    """
    window = attribute_window(events, action_t, window_ms, window_end=window_end)
    high = [e for e in window if _is_high_value(e)]
    osc = detect_oscillation(window)
    osc_families = {osc["key"]} if osc else None
    stab = stability_estimate(window, action_t)
    rev_result = detect_reversion(window, exclude_families=osc_families)
    rev = rev_result["reversion"]
    churn = rev_result["churn"]

    card = TemporalCard(
        action=action, action_t=action_t, window_ms=window_ms,
        window_end_ms=window_end,
        window_events=len(window),
        attributed=high[:12],
        oscillation=osc,
        stability=stab,
        reversion=rev[:8],
        churn=churn,
    )
    if osc:
        card.verdict = "oscillation"
        card.confidence = "high"
        card.why = (f"振荡信号: {osc['key']} 在 {osc['toggles']} 次 add/remove 交替"
                    f"({osc['hz']}Hz)——疑似重绘循环/反爬")
    elif rev:
        card.verdict = "reverted"
        card.confidence = "high"
        card.why = (f"静默回退: {len(rev)} 个构件出现后又被移除"
                    f"(快闪噪声 {churn} 条已过滤),最近 lag {rev[0]['lag_ms']}ms")
    elif high:
        max_lag = max(e.get("lag_ms", 0) for e in high)
        delayed = max_lag > LATE_EFFECT_THRESHOLD_MS
        card.verdict = "effected-delayed" if delayed else "effected"
        card.confidence = "high"
        card.why = (f"归属到 {len(high)} 个高价值构件变化"
                    + (f",最晚 lag {max_lag}ms(延时生效)" if delayed else ""))
    elif window:
        card.verdict = "changed"
        card.confidence = "medium"
        card.why = f"窗口内 {len(window)} 条变化但无高价值信号,性质不明"
    else:
        card.verdict = "no-change"
        card.confidence = "high"
        card.why = f"{window_ms}ms 窗口内无任何变更事件"
    return card
