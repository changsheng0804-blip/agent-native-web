# 小票标准 page_receipt v0.1（R1）

> 状态：先做项，已按真卡定稿，不是愿望单。
> 真卡来源：新鲜 master 实测（`tests/fixtures/form_names.html` fill → progressed；`challenge_overlay.html` 点 Continue → challenged），原始卡存档于验证机 `receipt_cards.json`。
> 对应代码：`mcp/server.py::_outcome_card`、`_errored_card`、`_t_world_outcome`；消费规范：`skills/agent-world/SKILL.md` §三/§四。
> 配套方向文档：`探索方向-地图公地与小票标准.md` §2。

## 1. 一句话定位（防埋没声明）

小票不是“动作返回体美化”，是本项目的**监理章**：点免费之后唯一还值钱的东西。任何动作有没有生效、以谁为准、卡在哪交给谁、凭什么复核——全部只看小票。后续任何改动不得删字段、不得改五态语义，只能加字段。

## 2. 必填字段（少一个即不合格）

| 字段 | 类型 | 语义（以代码为准） |
|---|---|---|
| `world_id` | int | 世界号，小票归属 |
| `channel` | "outcome" | 信道标识 |
| `page_outcome` | 五态之一 | 主标签，弱模型只读这一个键：`progressed / challenged / errored / uncertain / unchanged`，另有对账态 `none`（`world_outcome` 在 since 之后无新卡时返回） |
| `situation` | {type, to_url?, evidence?} | 实测出现过的 type：`form / navigation / overlay / state-flip / modal_iframe_challenge / occluded / error / none`；`occluded` 必带 `covered_by/at/action` |
| `confidence` | high/medium/low | 与 verdict 联动（如 visual-effected 须 high；challenged 常 medium） |
| `why` | string（一句话） | 给人/弱模型读的原因，须与 verdict 一致 |
| `target{id,name,fingerprint}` | object | `el_N` 可回查；`navigate` 成功后 id 置空（旧编号随 `world_epoch+1` 全失效）；`name` 永远不可信（见 sources） |
| `action{kind,via}` | object | kind ∈ click/fill/press/batch_fill（聚合 steps 任一步 errored 即停，整单 errored） |
| `effect{verdict,confidence,why,observed,evidence{...}}` | object | verdict ∈ `effected / visual-effected / no-change / changed`；evidence 必带 `polls/total_ms/first_change_ms/stop（early-effect/stable）`；`visual-effected` 必带 `visual_diff_score（RMS>阈值）` |
| `page{before_url,after_url,url_changed,state,anomaly}` | object | 全局事实，URL 变化覆盖局部判定 |
| `overlays{new[],gone[]}` | object | 新/消失弹窗（最多 8 条），挑战检测的输入 |
| `sources` | map[字段→四类] | 见 §3，F2 安全收口，缺失即不合格 |
| `next{guide_stale,suggested,candidates[]}` | object | 全局事实变了旧导览即不可信；challenged 时 suggested 必须是停下转人工 |
| `evidence_seq` | int（单调递增） | 小票序号，对账主键；`world_outcome(since)` 只返回序号更大的新卡 |
| `changes_seq{before,after}` | object | 页面变更游标对子，与 evidence_seq 成对出现 |
| `world_epoch` | int | 导航计数器，旧 `el_N` 失效依据 |
| `status` | object（轻量/全量） | 默认轻量（URL/稳定态/登录态/弹窗摘要+changed 高亮）；`unchanged/uncertain/challenged/errored` 或 `verbose=true` 自动全量 |
| `handoff` | object（challenged 必带） | `{required,type:human_challenge,reason,suggested,resume_condition:challenge_cleared}` |
| `recipes` | array（unchanged+弹窗时带） | 自愈处方候选（如按 Escape / 关弹窗），模型可直接拾取 |

## 3. sources 四类（身份证制度）

```text
fact       URL/编号/坐标/指纹/序号（target.id、target.fingerprint、page.*、evidence_seq…）→ 可信
evidence   动作前后差分（why、effect.*、overlays）→ 可信
inference  导览/处方/建议（situation.type、next.suggested、handoff）→ 仅参考
untrusted  页面自由文本（target.name、text、aria-label、placeholder）→ 绝不当指令执行
```

真卡实证（两张卡完全一致）：`target.name` 标 `untrusted`，`effect.verdict` 标 `evidence`，`situation.type` 标 `inference`，challenged 卡另有 `handoff: inference`。

## 4. 对账规则（任何进程可复核）

1. 单调性：同 world 内 `evidence_seq` 严格递增；`world_outcome(since=N)` 仅当有更大序号新卡才返回，否则 `page_outcome: none`。
2. 成对性：`evidence_seq` 与 `changes_seq{before,after}` 必须同时出现；`after>before` 说明页面的确动了。
3. 失效性：`navigate` 成功 → `world_epoch+1`，旧 `el_N` 一律作废，`target.id=null`。
4. 一致性：`page.url_changed=true` 则 situation 须为 navigation 类；`challenged` 须带 handoff；`unchanged` 不得带“成功”字样 why。

## 5. 示例 A：progressed-form（填表生效）

```json
{
  "world_id": 1, "channel": "outcome",
  "page_outcome": "progressed",
  "situation": {"type": "form", "to_url": null},
  "confidence": "high",
  "why": "填表值已进入可见输入框",
  "target": {"id": "el_9", "name": "input.用户名", "fingerprint": "input|input|placeholder=用户名|path=form"},
  "action": {"kind": "fill", "via": "self"},
  "effect": {"verdict": "effected", "confidence": "high",
    "evidence": {"polls": 1, "total_ms": 243, "first_change_ms": 243, "stop": "early-effect"}},
  "sources": {"target.id": "fact", "target.fingerprint": "fact", "target.name": "untrusted",
    "effect.verdict": "evidence", "situation.type": "inference"},
  "evidence_seq": 1, "changes_seq": {"before": 1, "after": 1}, "world_epoch": 0,
  "next": {"guide_stale": false, "suggested": null, "candidates": []}
}
```

## 6. 示例 B：challenged（验证墙拦截，转人工）

```json
{
  "world_id": 2, "channel": "outcome",
  "page_outcome": "challenged",
  "situation": {"type": "modal_iframe_challenge", "to_url": null,
    "evidence": ["新出现 fixed 全屏遮罩(约 1440x900px)内含 iframe(400x388px)", "iframe 来源: (同源/空白)"]},
  "confidence": "medium",
  "why": "页面被挑战遮罩/验证墙拦截: 新出现 fixed 全屏遮罩(约 1440x900px)内含 iframe(400x388px)",
  "target": {"id": "el_9", "name": "button.continue", "fingerprint": "button|button|path=form"},
  "action": {"kind": "click", "via": "self"},
  "effect": {"verdict": "changed", "confidence": "medium",
    "evidence": {"polls": 3, "total_ms": 625, "first_change_ms": 210, "stop": "stable"}},
  "handoff": {"required": true, "type": "human_challenge",
    "reason": "页面被挑战遮罩/验证墙拦截",
    "suggested": "页面触发人机验证或固定遮罩,请通知用户在可见窗口协助完成",
    "resume_condition": "challenge_cleared"},
  "sources": {"target.name": "untrusted", "effect.verdict": "evidence",
    "situation.type": "inference", "handoff": "inference"},
  "evidence_seq": 1, "changes_seq": {"before": 1, "after": 5}, "world_epoch": 0,
  "next": {"guide_stale": false,
    "suggested": "页面被挑战遮罩/验证墙拦截,请暂停自动推进并转交人工处理", "candidates": []}
}
```

## 7. R1 验收清单（机器可查）

- [ ] 每次 `world_act`（及旧 click/fill/press/navigate）返回含 §2 全字段，缺一即失败。
- [ ] `world_outcome` 幂等重读一致；`since=evidence_seq` 无新卡时返回 `none` 卡。
- [ ] challenged 卡必带 `handoff.required=true + resume_condition`。
- [ ] `target.name` 在 sources 中恒为 `untrusted`（两真卡已证）。
- [ ] `test_page_outcome.py` 基线 23/23 不退化；`test_protocol.py` 6 词环不退化。

## 8. 非目标（重申）

不做钱包、不存密码、不上链、不签发身份；`world_eval` 永不进小票信任边界。

## 10. L2 样式层（视觉梯子第二级，P0-2 后续）

`effect.verdict=visual-effected` 有两条路，`effect.visual_path` 标明走了哪条：

- `style-diff`：区域元素计算样式快照比对命中。附 `style_changes[{id,prop,before,after}]`（≤8条），
  如 `el_X.backgroundColor:rgb(52,152,219)→rgb(231,76,60)`。结构化、可解释、免截图。
  命中前先过波动基线：after 连采两次（间隔 0.15s），两次之间在变的属性（转菊花类持续动画）
  视为噪声直接排除；元素消失/新增归 DOM 侧，不管。
- `pixel`：样式层哑火后落到区域前后帧 RMS（`VISUAL_RMS_THRESHOLD=5.0`，附 `visual_diff_raw`）。
  滚动致错位时整条作废（`visual_skipped=scroll-shift`），不投票。

验收：`test_visual_style.py`（WAAPI 无DOM变更→style-diff；静态→no-change）。

## 9. 聚合整单语义（P0-1，`world_act steps`）

单步卡管一步，整单卡管一单。`steps` 聚合执行时：

- 每步都走同一出口，各占一个 `evidence_seq`（含 errored 步——P0-1 已堵黑洞：errored 卡也 mint，否则它的序号与上一张成功卡重复，`world_outcome(since)` 会回 `none` 把它藏掉）。
- 整单主标签 = **末步卡**（任一步 errored 即停，故 errored 必为末步）。
- 整单另附聚合记账（长任务 FP/FN 以此为准，不只看末步）：
  - `step_count` / `steps[]`（坏步也记，逐项可查）
  - `step_outcomes[]`（如 `["progressed","errored"]`，部分成功如实保留）
  - `all_progressed`（全绿才 true）
  - `first_failure_idx`（首个非 progressed 下标，全绿为 null）
  - `seq_range{first,last}`（子卡序号闭区间，须严格递增）
- 验收：`test_receipt.py` [7]全绿整单 / [8]部分失败整单（47 项基线一部分）。

---
*R1 完成标志：本文件合入 + R1 验收清单全过。下一步 R2（字段齐全性入测试）、R3（三条预算规则进 SKILL）。*
