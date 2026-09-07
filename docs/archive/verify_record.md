# 弱模型 vs SKILL 类型验证 — 结果记录

> 日期:2026-09-02
> 任务:联想下拉(autocomplete,场景1) 输入 ap → 从下拉点击选择 apple → 确认最终值
> harness:world_proxy(常驻 HTTP)+ 子 agent(forkContext=none,Flash 级别弱模型模拟)
> 页面:fixtures/autocomplete.html(联想项 apple/apricot;直接 Enter 显示"未选择联想项"警告)

## 组配置

| 组 | 子agent | 注入内容 | 结果 |
|---|---|---|---|
| 对照组 A | 小雨 | 通用操作指令(简洁版,无决策树) | ✅ 完成(经大量挣扎) |
| 实验组 B | 阿城 | SKILL-executor 全文(决策树+场景1固定模式+逃生门) | ✅ 完成(有序,两次遇阻后按手册恢复) |

## A 组(小雨,通用指令)过程摘要

1. fill ap(type_delay 30)→ 联想出现 apple/apricot ✅
2. click apple → 下拉关闭、表单值 apple ✅
3. **误判**:点选后 world_entities 读 final-value 仍显示"(尚未选择)"→ 困惑,反复重查
4. 误把 world_fill 再输入 → **值被追加变成 "apapple"**(fill 不清空)
5. 尝试空 fill/点击清空均无效 → 卡顿多个来回
6. 自己读 server.py 源码找原因 → 用 loc.fill(type_delay=0)重置 → 重选成功
7. 最终确认:输入框值=apple;final-value 文本世界模型读不到(监听盲区)

**挣扎点**:值污染/清空困难/多轮误判点击未生效。用了 ~20+ 次调用,含读源码调试。

## B 组(阿城,SKILL-executor)过程摘要

1. 按手册:world_guide 建方向感 → 查到 evidence 显示值已是 apple(之前 A 组残留)
2. 按手册"必须从下拉点击选择"→ 重新 fill 触发联想;**同样遇值污染**("apapple")
3. 按手册失败恢复路径:world_state → 识破问题 → Ctrl+A + Backspace 清空(有纪律)
4. 重填 ap → 联想展开 → click apple → forms 确认 value=apple ✅
5. same 盲区:final-value 文本读不到;但 B 组**直接给出正确机制判断**(characterData 监听盲区),未绕圈

**挣扎点**:值污染一次(清理果断);无 A 组式恐。~12 次调用,无源码阅读。

## 关键发现(验证的意外产出:2 个真实工具缺陷)

1. **world_fill 追加不清空**(press_sequentially 不清空现有值):
   - 弱模型在非空输入框上再输入 = 追加污染("ap"→"apapple")
   - A 组被此卡死;A/B 都必须靠 Ctrl+A 清空 hack,手册无"清空"指令
   - → 行动层应有"先清空再输入"语义或 world_clear/world_fill(clear=True)

2. **observer 纯文本变化盲区**(root cause:OBSERVE_OPTS 无 characterData):
   - final-value 的 textContent 变化、input.value 的 property 变化都不产生可观察事件
   - 世界模型永远读旧文本 → 弱模型无法确认"最终值区域变了"
   - A 组误判为"点击无效";这是 A 组绕圈的主因
   - → 与之前 shadow_dynamic 测试"点击生效但 verdict=no-change"同根因

## 对照结论

- 任务完成率:A ✅ B ✅(2/2)——任务本身简单
- 卡顿/绕圈:A 大量(误判+源码调试)vs B 少量(按手册恢复,无源码阅读)
- 决策树价值显现点:**值污染时的恢复路径**——A 自创路径绕圈,B 按手册 world_state→清空→重试,直接命中
- 但两个缺陷(清空语义/文本盲区)对两类 agent 都是真实摩擦,修复工具比优化 SKILL 更能受益

## 建议

1. 修 world_fill 清空语义(或加 world_clear) —— P1
2. 修 observer characterData 盲区(文本变化入事件流) —— P1(顺带修复 shadow_dynamic 的 no-change 误判)
3. SKILL-executor 补"清空输入框"固定模式(Ctrl+A+Backspace 或 world_clear) —— P2