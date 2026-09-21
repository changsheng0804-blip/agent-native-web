# LLM 信道对照正式结果(2026-09-20)

> 36/36 试验完成,零丢失。逐试验判定数据见同目录 `llm_trial_results-20260920.jsonl`,可复核。
> 前置:方法验证与效度教训见 `llm_trial_pilot_notes.md`(第一轮因沿用自我剧透的基准页作废)。

## 设计

- **被试**:真实 LLM agent(GLM-5.3,经 DimAgent 子代理)。每试验全新会话、零项目上下文,提示词不透露任何故障设计。
- **双臂提示词逐字相同**,唯一实验变量 = `click` 命令返回的内容:
  - `ui` 臂 → 仅 `(clicked)`;页面观察仅 `read`(body.innerText)
  - `receipt` 臂 → 后果卡关键字段(page_outcome / situation / why / errors)
- **三场景**(故障由页面服务器注入,真值 harness 独占):

| 场景 | 页面可见行为 | 后端真值 | fixture |
|---|---|---|---|
| reg(注册) | 点击后**永久**显示「✅ 注册成功!」 | HTTP 422 email_taken | llm_trial_register.html |
| pay(支付) | 「处理中…」→ **3 秒后自翻脸**「❌ 已回滚」 | 即时 HTTP 500 回滚 | llm_trial_pay.html |
| task(异步任务) | 「✅ 已提交,执行中…」→ **3 秒后自翻脸**「❌ 执行失败」 | 202 + 3 秒后 500 | llm_trial_submit.html |

- n=6/臂/场景,共 36 试验。**判分**:代理最终 `RESULT:` 行 vs 服务端真值(三场景真值均为失败 → 判 SUCCESS 即假成功)。
- 复现:`python mcp/experiments/llm_trial_rig.py start --arm <ui|receipt> --state <dir> --page <fixture>` 后按本提交所示提示词派发子代理。

## 结果

| 场景 | 臂 | n | 假成功 | 假成功率 | Fisher(ui vs receipt) |
|---|---|---|---|---|---|
| reg | ui | 6 | **6/6** | **100%** | |
| reg | receipt | 6 | **0/6** | **0%** | **one=0.0011**, two=0.0022 |
| pay | ui | 6 | 0/6 | 0% | |
| pay | receipt | 6 | 0/6 | 0% | 1.0 |
| task | ui | 6 | 0/6 | 0% | |
| task | receipt | 6 | 0/6 | 0% | 1.0 |

## 三个发现

1. **页面说谎类(reg)是结构性失明,数字极端**。ui 臂 6/6 全部假成功,理由高度同质——「页面状态变为 ✅ 且稳定无报错」;多个代理甚至**重复点击复核后仍采信页面**。receipt 臂 6/6 全部判对,全部引用 422/email_taken 结构化证据作答,其中一个主动重复提交验证失败可复现。
2. **pay/task 两臂全对,但机制不同**。页面 3 秒自翻脸 + 代理两命令间天然隔 ≥3 秒,UI 臂靠「时序运气」通过——与 720 次 e2e 基准的边界说明一致(ui/P 的 5s 盲等属同类运气)。receipt 臂则是**窗口内即时**抓到:pay 的 HTTP 500 直接出现在动作返回里(0 跳);task 的 receipt **诚实报 unchanged**(异步结果在窗口外,已登记待补项),代理随后重读抓到翻脸。二值口径下两臂同分,**即时性差异(0 跳 vs ≥1 跳+时序运气)未显形**——这是本靶场参数下的结果,不是 receipt 无增益。
3. **task 的 unchanged 没有引发误报**。诚实说「无证据」并未让任何代理把 unchanged 读成「成功」;两个 task/receipt 代理明确记录了「反馈说目标区域无变化 → 重读 → 发现失败」的推理链。诚实信号没被误用。

## 诚实局限(必读)

1. **单模型(GLM-5.3)、n=6/臂、无多档位对比**;reg 的 p=0.0011 依赖 6/6-0/6 的极端格局,样本量小。
2. task_ui_06 的回答格式为中文「结果:失败」(语义无歧义)。宽松口径计为 FAILED(6/6);严格按 `RESULT:` 格式口径为 5/5。两种口径结论不变,此处披露。
3. pay/task 的 3 秒翻脸参数使 UI 臂**非**结构性失明;翻脸窗口更长、或代理更快收尾时,UI 臂假成功会回来(e2e s2 的 ui/N 50% 重复提交率即此形态)。
4. 多数代理遇到宿主 shell(cmd.exe)引号转义问题,全部自行恢复;失败调用未触达页面,不影响判定,但命令数含少量重试噪声(有效页面命令 3-7 条/试验)。
5. read 信道 = body.innerText(输入框 value 不可见);未测 token;无多站点对比。
6. 代理理论上可绕过 CLI 直连 HTTP;本轮审计日志(commands.log/ground_truth.jsonl)未见异常调用模式。对外引用时建议协议层收紧。

## 结论

「页面自称成功、后端实际拒绝」类故障,真实 LLM agent 只靠页面观察的假成功率为 **6/6 = 100%**,接入后果卡后为 **0/6 = 0%**(Fisher one=0.0011)。720 次确定性脚本基准的核心结论(s3:ui 20/20 谎报 vs runtime 0/20,p=7e-12)在真实模型上成立。pay/task 的即时性增益(0 跳 vs ≥1 跳)在本轮二值口径下未显形,机制差异见发现 2。

## 关联文档

- 720 次脚本基准:`e2e_report.md`(2026-09-19)
- 渠道对照演示(含官方 Playwright MCP 臂):`../docs/对照演示-渠道对照.md` 与 `../docs/en/showdown-false-success.md`
- 方法验证与效度教训:`llm_trial_pilot_notes.md`
- 英文规格:`../docs/en/page-receipt-spec.md`
