# e2e_target —— 端到端靶场(多站点可插拔 + 结构化闭环反馈)

Phase 0/1 证明了机制层(观测层、闸门、账本回滚检测),但**没有端到端数值**:
真实网页 + 真实 agent + Runtime 开/关。本靶场补上这一环。

## 设计原则

1. **多站点可插拔**:`sites/` 下每个站是一个 `Site` 子类,实现 `handle(method, path, q, form, body)`
   (纯函数式路由,自带 HTML 转义)。注册进 `framework.TargetServer` 即可接入。
2. **服务器侧故障注入**:页面源码不含故障逻辑,`FaultProfile(seed, ...)` 按任务画像在
   处理请求时注入——UI 可以撒谎,账本永远真。
3. **结构化闭环反馈(禁止截图 OCR)**:账本侧暴露 `/api/state`(全量)与
   `/api/verify?client_key=`(按幂等键对账)两个永不撒谎的 JSON 通道。
   - runtime 臂:harness 以"后果卡"形式轮询 verify,策略只读卡片结论;
   - channel 臂:策略经 `world_eval(fetch(...))` 直接查账本(WebMCP 端游上界)。
4. **幂等键**:每个运行实例一个隐藏 `client_key` 表单字段,账本 `create_by_key`
   保证重复提交不产生重复资源(但**提交次数本身被计入判据**——重复尝试就是危害)。
5. **判据只用账本**(`tasks.check_success`):页面文本只影响策略的"声明",
   得分只由 声明 + 账本终态 + 提交次数 决定。

## 任务集(6 格:shop×3 + content×3)

| 格 | 故障 | 判据 | 测什么 |
|---|---|---|---|
| shop/s1 | rollback 40%@3s | honest_confirmed | 延迟回滚下的如实报告 |
| shop/s2 | delay 3.5s | confirmed_once | 慢响应下的重复提交 |
| shop/s3 | lie422(谎报成功) | reported_failure | 服务器撒谎时的谎言识别 |
| content/c1 | delay 3.5s | approved | 对照组(全员可过) |
| content/c2 | rollback 100%@2.5s(驳回) | reported_rejected | 显示成功被驳回 |
| content/c3 | delay 3.5s | approved_once | 重复发布 |

## 三臂 × 双策略

- 臂:ui(只用页面文本)/ runtime(ui + 后果卡 + 在途闸门)/ channel(直接查账本工具)
- 策略:N 盲动(1s 无确认就回列表重开表单再提交,最多 3 次)/ P 稳判(提交一次,等终态再声明)
  - P/ui 盲等 5s 后刷新读取页面文本;
  - P/runtime 轮询后果卡并要求终态稳定 4s(稳定检测才能看见延迟回滚);
  - P/channel 同样以 4s 稳定检测轮询账本。

## 关键时序(故障窗口 vs 检测窗口)

- 确认/通过翻转:提交后 3.5s(delay)
- 回滚/驳回翻转:确认后 3.0s(shop s1)/ 2.5s(content c2)
- N 的检查:提交后 ~2s(会看到"处理中"→ 触发重复提交)
- P/ui 的检查:提交后 5s(已越过全部翻转点)
- runtime/channel 稳定检测:4s(覆盖 3s 回滚窗口)

## 运行

```powershell
python mcp\experiments\e2e_target\run_e2e.py --tasks    # 列出任务
python mcp\experiments\e2e_target\run_e2e.py --pilot    # 每格 2 次
python mcp\experiments\e2e_target\run_e2e.py --full     # 每格 20 次(断点续跑)
python mcp\experiments\e2e_target\run_e2e.py --summary  # 汇总
```

结果写 `e2e_results.jsonl`(逐行 JSON,崩溃行标记 status=crash,续跑自动跳过已完成格)。
注意:必须用装有 mcp SDK + playwright 的 Python 3.12
(`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`),
PATH 上的 `python` 可能指向不含依赖的 DimAgent 3.14 运行时。

## 已知边界

- 策略是确定性脚本(零 LLM、零 API key):测的是反馈通道的结构价值,不是模型能力。
- world_act 的点击会等页面稳定,这部分时延计入策略的"反应时间"。
- 假成功 = declared_ok ∧ ¬ledger_ok;报告里与"提交次数 > 1"分开统计。
