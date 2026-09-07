# 实施计划 · 阶段 A:统一后果卡(page_outcome 全动作出口)

> 版本:2026-09-02 · 状态:待评审
> 依据:Grok 改造方案之阶段 A(已修正其事实性偏差,见文末"勘误")
> 目标:所有动作(world_click/fill/batch_fill/press/click_at/navigate)返回同一张后果卡,
> 五态 `progressed | challenged | errored | uncertain | unchanged` 全部落地。

---

## 0. 现状(已核实,server.py 现行行为)

| 动作 | 局部 effect | 全局 finalize | evidence 记录 | 统一卡 |
|---|---|---|---|---|
| world_click | ✅ `_wait_click_effect` | ✅ `_finalize_click_result` | ✅ | ❌(结构是旧式 ret+feedback) |
| world_fill | ✅(fill_verified 强证据) | ❌ | ✅ | ❌ |
| world_press | ✅(含 disappear_ok) | ❌ | ✅ | ❌ |
| world_batch_fill | ❌(仅逐字段 ok 汇总) | ❌ | ✅ | ❌ |
| world_navigate | ❌(裸 goto+summary) | ❌ | ✅ | ❌ |
| world_click_at | ❌(裸 mouse.click) | ❌ | ✅ | ❌ |

关键事实:`_impl_with_status`(server.py:416)已对 6 个动作统一拍 before_signal 并写 evidence_log,
证据基础齐全;缺的是**同一出口的卡片化**与**五态命名**。

---

## 1. 改动点 A1 — 常量与枚举(server.py,新增 ~30 行)

- `PAGE_OUTCOME_*` 五态常量:`progressed / challenged / errored / uncertain / unchanged`
- `SITUATION_TYPES` 封闭枚举(阶段 A 子集):
  `navigation / overlay / state-flip / form / submit / visual / external-unscoped / none`
  (external-unscoped 预留给阶段 C,阶段 A 不使用但占位)
- `SOURCE_TAGS`:`fact / evidence / inference / untrusted`(阶段 A 只定契约键,F2 再填值)
- `_is_submit_trigger(wid, target, key)`:`key` 为 Enter 且目标在 form 内或为 submit 按钮 → True
  (实现:`el._el.closest('form')` 或 `type=submit`)

## 2. 改动点 A2 — `_outcome_card(wid, action, args, ret, before_signal, extra=None)`(新增 ~60 行)

从 `_finalize_click_result` 提取公共部分(after_signal / url_changed / overlays delta / changes_seq),
组装统一卡(append 到 ret,保留旧字段以兼容):

```json
{
  "world_id": 1, "channel": "outcome",
  "page_outcome": "progressed",
  "situation": {"type": "overlay", "to_url": null},
  "confidence": "high", "why": "页面整体出现新的弹窗/菜单: …",
  "target": {"id": "el_89", "name": "…", "fingerprint": "…"},
  "action": {"kind": "click", "method": "locator"},
  "effect": {"verdict": "effected", "observed": [], "region_changed": {}},
  "page": {"before_url": "…", "after_url": "…", "url_changed": false, "state": "stable", "anomaly": false},
  "overlays": {"new": [], "gone": []},
  "status": {"auth": {}, "dialogs": [], "forms": [], "changed": {}},
  "sources": {},
  "next": {"guide_stale": false, "suggested": null, "candidates": []},
  "evidence_seq": 12, "changes_seq": {"before": 80, "after": 94},
  "world_epoch": 1
}
```

五态映射(写死,不允许模型猜):
- `effect.verdict ∈ {effected, visual-effected}` → **progressed**(situation 按证据分类:url_changed→navigation;
  新 dialog/menu→overlay;状态翻转→state-flip;fill_verified→form)
- challenge 命中(见 A3)→ **challenged**
- 动作执行抛异常 → **errored**(由 A4 处理)
- `effect.verdict == changed`(medium)→ **uncertain**
- `effect.verdict == no-change` → **unchanged**

`situation.type` 必须来自封闭枚举;`world_epoch` 每导航 +1(见 A3-navigate)。

## 3. 改动点 A3 — 六个动作出口(server.py,每个 handler 改 return 处)

统一手法:改 `_impl(name, args, before_signal=None)` 签名,_impl_with_status 把已拍的
before_signal 传入 6 个 action handler(不再重复 evaluate)。

| 动作 | 改动 | 备注 |
|---|---|---|
| `_t_world_click` | return 从 `_finalize_click_result(...)` 改为 `_outcome_card(...)`;finalize 逻辑收缩为 card 内部一步 | 结构=旧字段超集,旧调用方不受影响 |
| `_t_world_fill` | 两个分支(locator-fill / js-setter)返回前都过 `_outcome_card` | challenge 检测同 click |
| `_t_world_press` | 同上 + `_is_submit_trigger(Enter)` → situation.type=submit;触发后查 challenge | Escape 关弹窗仍走 disappear 证据 |
| `_t_world_batch_fill` | 聚合卡:整体 page_outcome(全 ok→progressed/uncertain;有字段失败→uncertain;全失败→errored);不逐字段 region diff,复用 evidence_log transition | target=fields[] |
| `_t_world_navigate` | goto 前拍 before_signal(handler 内),goto 后 `_outcome_card`;`w["epoch"]+=1`;**target.id 恒为 null,只给 url**;page_outcome=progressed(situation.type=navigation) | 跨导航旧 el_N 全部失效,由 world_epoch 表达 |
| `_t_world_click_at` | 新增 `_region_snapshot_at(wid, x, y)`(复用 `_click_region_snapshot` 的 JS,region 由坐标±200 构造)→ `_wait_click_effect` → `_outcome_card` | 视觉兜底路径也要有反馈 |
| `_t_world_wait` | 不动(无动作语义) | |

challenge 检测(最小实现):动作后 `state=anomaly`,或全屏 `position:fixed` 遮罩/新 iframe 出现
(单条 evaluate,~10 行)。先只判定、不解除。

## 4. 改动点 A4 — 异常路径卡片化(server.py `_impl_with_status` / `call_tool`)

6 个动作抛异常时,不再只回 `错误: {e}` 文本;改为返回统一卡:
`page_outcome=errored` + `error` 字段(保留原始错误文本,便于调试)+ 已拍 before_signal 下的现状快照。
错误信息不吞、不回滚(动作已发生无法回滚,如实标注)。

## 5. 改动点 A5 — SKILL.md(skills/agent-world/SKILL.md,唯一技能文件)

- 步骤 4「验证变化」改写:第一句话改为"读返回的 `page_outcome` 五态",附五态→下一步行为表
  (progressed→继续任务;challenged→停下,报告被拦截;errored→重试或换路径;uncertain→world_outcome/截图确认;unchanged→换目标)。
- 新增节「统一后果卡」(示例 JSON + 说明:主标签只有字段层,编号 el_N 可回查)。

## 6. 测试

### 6.1 新建 `mcp/test_page_outcome.py`(offline 组第 16 项,必须被 run_quality 收编)

| # | 夹具 | 动作 | 期望 page_outcome | 断言要点 |
|---|---|---|---|---|
| 1 | dyn.html | click 弹窗按钮 | progressed | situation.type=overlay;why 含弹窗名;effect.verdict=effected |
| 2 | dyn.html | click 普通标题(负例) | unchanged | FP 一票否决:不得 progressed |
| 3 | far_modal.html | click 角落按钮 | progressed | observed 含新 dialog;id 为 el_N 可回查 |
| 4 | tabs.html | click tab | progressed | situation.type=state-flip(aria-selected 翻转) |
| 5 | visual.html | click 变色(visual_evidence=True) | progressed | verdict∈{visual-effected, effected};RMS 存在 |
| 6 | form_names.html | fill(按 name) | progressed | situation.type=form;fill_verified |
| 7 | form_names.html | fill 不存在的 id | errored | 异常路径返回卡而非纯错误文本 |
| 8 | dyn.html | press Escape 关弹窗 | progressed | disappear 证据(observed 含 remove) |
| 9 | dyn.html | press Enter(在 form 内) | progressed 或 challenged | submit 触发;不得 unchanged |
| 10 | far_modal.html | click_at 坐标点按钮 | progressed 或 uncertain | 有 effect(region 或全局) |
| 11 | dyn.html | navigate 到 far_modal.html | progressed | situation.type=navigation;world_epoch+1;target.id=null |
| 12 | dyn.html | batch_fill 两个可见输入框 | progressed 或 uncertain | 不得 unchanged;ok_count 断言 |
| 13 | challenge_overlay.html | 点提交按钮 | challenged | 遮罩检测命中 |
| 14 | challenge_overlay.html | 点非提交装饰 | 不得 challenged | 防误报(FP 一票否决) |

每个用例都断言卡内 `evidence_seq` 递增且与 changes_seq 成对(回查契约)。

### 6.2 现有测试小改

- `mcp/test_form_names.py`:fill 断言追加"返回含 page_outcome 键"(1 行)。
- `mcp/test_global_feedback.py`:不动(用例 11 已覆盖 navigate 反馈)。

### 6.3 通过线

- offline 组 **16/16**(15 项存量 + test_page_outcome)
- `validate_closed_loop.py --local` 仍 **FP=0**
- 负例(用例 2/14)不得 progressed/challenged;fill 正例不得 unchanged

## 7. 实施顺序与回归

1. A1 常量 → A2 `_outcome_card` → A3 六出口(先 click 保绿,再逐个动作)
2. `--only test_page_outcome.py` 快速迭代
3. 全量 offline(≈7 分钟)→ validate_closed_loop 复核
4. A5 SKILL.md → 提交

## 8. 风险与规避

- **返回体变大**:主标签(page_outcome/situation/why)放 JSON 最前(输出顺序即字段顺序);
  弱模型只读首层,旧字段对强模型仍可用。
- **并发远程推送**(本会话已遇两次 push 拒绝):推前 fetch+rebase。
- **click_at 区域证据窗**:坐标来自截图,与 el_N 世界解耦;region 快照失败时降级为纯全局卡(不报错)。
- **challenge 误报**:只在新遮罩/iframe/anomaly 时判 challenged,且必须有 before/after 差异。

## 9. 勘误(Grok 方案的偏差,已按此修正)

1. `skills/agent-world/SKILL-executor.md` **不存在**;SKILL.md 内也无"page_outcome 缺失时退化"文案——改为直接在 SKILL.md 步骤 4 落地,不涉及删句。
2. `mcp/test_page_outcome.py` **不存在**,是新建不是扩。
3. offline 现为 15/15(含 test_shadow_dynamic),加本测试后为 16/16。
4. 不需要为阶段 A 加挂 world_outcome 工具(那是阶段 B 的门面),本阶段只统一出口。