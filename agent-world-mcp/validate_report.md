# 实时闭环反馈 · 实战验证报告

> 日期:2026-08-31 · 通过线:TP+TN ≥ 80% 且 FP=0

## 结果

| 场景 | 分类 | verdict | confidence | truth |
|---|---|---|---|---|
| gf-passenger(就近弹窗基线) | TP | effected | high | True |
| far-modal(远距弹窗) | TP | effected | high | True |
| tabs(标签切换无弹窗) | TP | effected | high | True |
| negative-heading(负例) | TN | no-change | high | False |
| baidu-submit(表单提交→URL) | AM | None | None | True |
| wiki-link(常规链接跳转) | TP | effected | high | True |

**汇总**: TP=4 TN=1 FP=0 FN=0 AM=1 AMs=0 SKIP=0
**准确率**: 83% (通过线:≥80% 且 FP=0)

## 失败模式清单

- **AM** baidu-submit(表单提交→URL): verdict=None truth=True → (fill 无 effect,看 URL)

## 逐场景细节

### gf-passenger(就近弹窗基线) [TP]
- why: 页面出现新的弹窗/菜单(可能远离目标): 弹窗 dialog.number-of-passengers
- truth: dialog可见
- note: 基准:弹窗出现在按钮附近,应 TP

### far-modal(远距弹窗) [TP]
- why: 页面出现新的弹窗/菜单(可能远离目标): 弹窗 dialog.far-modal-title-居中弹窗-这个弹窗离角落按钮很远-用于验证-2
- truth: dialog可见
- note: 角落按钮→居中弹窗(离按钮 ~600px),专测 ±200px 是否漏判

### tabs(标签切换无弹窗) [TP]
- why: 目标自身状态变化: 选中态(aria-selected) 翻转
- truth: tab-b选中
- note: 无 dialog,SPA 式切换,专测是否误判 effected(FP)或漏判

### negative-heading(负例) [TN]
- why: 目标区域无变化(点击可能未生效,或效果发生在远处)
- truth: 无变化
- note: 点击无副作用标题,应 TN 不误报

### baidu-submit(表单提交→URL) [AM]
- why: (fill 无 effect,看 URL)
- truth: url: "https://www.baidu.com/" → "https://wappass.baidu.com/static/captcha/tuxing_v2.html?&lo
- action: fill method=locator-fill
- note: 填搜索框+Enter → URL 变化,专测 H3 URL 分支

### wiki-link(常规链接跳转) [TP]
- why: URL 变化(导航/提交类)
- truth: url: "https://en.wikipedia.org/wiki/Python_(programming_language) → "https://en.wikipedia.org/wiki/Guido_van_Rossum"
- note: 点正文链接 → URL 变化(导航类)
