# 实时闭环反馈 · 实战验证报告

> 日期:2026-08-31 · 通过线:TP+TN ≥ 80% 且 FP=0

## 结果

| 场景 | 分类 | verdict | confidence | truth |
|---|---|---|---|---|
| far-modal(远距弹窗) | TP | effected | high | True |
| tabs(标签切换无弹窗) | TP | effected | high | True |
| negative-heading(负例) | TN | no-change | high | False |
| fill-dyn(填表值进入输入框) | TP | effected | high | True |
| press-escape(按键关闭弹窗) | TP | effected | high | True |
| visual-css(纯 CSS 视觉生效·PR2 视觉兜底) | TP | visual-effected | high | True |
| visual-negative(视觉兜底负例·不误报) | TN | no-change | high | False |

**汇总**: TP=5 TN=2 FP=0 FN=0 AM=0 AMs=0 SKIP=0
**准确率**: 100% (通过线:≥80% 且 FP=0)

## 失败模式清单


## 逐场景细节

### far-modal(远距弹窗) [TP]
- why: 页面出现新的弹窗/菜单(可能远离目标): 弹窗 dialog.far-modal-title-居中弹窗-这个弹窗离角落按钮很远-用于验证-2
- truth: dialog可见
- 证据窗: polls=1 total=219ms first_change=219ms stop=early-effect
- note: 角落按钮→居中弹窗(离按钮 ~600px),专测 ±200px 是否漏判

### tabs(标签切换无弹窗) [TP]
- why: 目标自身状态变化: 选中态(aria-selected) 翻转
- truth: tab-b选中
- 证据窗: polls=1 total=206ms first_change=203ms stop=early-effect
- note: 无 dialog,SPA 式切换,专测是否误判 effected(FP)或漏判

### negative-heading(负例) [TN]
- why: 目标区域无变化(点击可能未生效,或效果发生在远处)
- truth: 无变化
- 证据窗: polls=3 total=629ms first_change=213ms stop=stable
- note: 点击无副作用标题,应 TN 不误报

### fill-dyn(填表值进入输入框) [TP]
- why: 填表值已进入可见输入框
- truth: 输入框含值: hello-agent
- action: fill method=locator-fill
- 证据窗: polls=1 total=218ms first_change=218ms stop=early-effect
- note: 填表后值应进入可见输入框(fill_verified 强证据)

### press-escape(按键关闭弹窗) [TP]
- why: 弹窗/菜单已关闭: 弹窗 dialog.far-modal-title-居中弹窗-这个弹窗离角落按钮很远-用于验证-2
- truth: 无可见dialog
- action: press Escape
- 证据窗: polls=1 total=206ms first_change=206ms stop=early-effect
- note: 先点开弹窗再按 Escape → 弹窗应消失(disappear 信号)

### visual-css(纯 CSS 视觉生效·PR2 视觉兜底) [TP]
- why: 检测到目标区域发生显著视觉状态或浮层变化 (RMS=33.32)
- truth: 动画目标已变 active
- 证据窗: polls=3 total=688ms first_change=274ms stop=stable
- note: DOM 无增删、纯背景色翻转 → 应触发视觉 diff 兜底 visual-effected,TP

### visual-negative(视觉兜底负例·不误报) [TN]
- why: 目标区域无变化(点击可能未生效,或效果发生在远处)
- truth: 无变化
- 证据窗: polls=3 total=628ms first_change=203ms stop=stable
- note: 点击无副作用元素 → 不应被视觉兜底误判为 visual-effected(若是→FP 一票否决)
