# 实施记录：统一环境侧时间线（world_timeline）

日期：2026-09-06
分支：experiment/time-aware-feedback
范围：`mcp/server.py`（时间线内核 + 三层接入 + `world_timeline` 工具）、
`mcp/experiments/test_server_timeline.py`（集成测试 7 项）、
`mcp/experiments/run_timeline_gh.py`（GitHub 真实站点演示）

## 1. 动机

前序实验证明：时间感知反馈（world timeline 流式）在合成夹具上显著优于前后快照比对
（15/15 vs 6/15），而真实站点上常规延迟落在基线窗口内——**时间线的真实价值是诊断信息**：
精确的延迟测量、SPA 噪声识别、跨层因果归因。

本项目已有两套独立反馈：L2 前提监视（`_assumption_check` / `_t_world_assume`）与 L3 动作证据
（`_build_action_evidence` / `runtime_events`）。它们分别回答"前提还成立吗"和"这个动作生效了吗"，
但彼此没有共享的因果骨架。统一时间线把三层（DOM 变化、网络事件、动作生命周期）合并为一条
环境侧时间线——这是与 WebMCP（只有能力声明）和裸 computer-use（只有瞬时感知）的核心差异。

## 2. 设计

### 2.1 数据模型

- 每条事件：`{seq, t, type, ...}`。`seq` 为世界内单调递增序号（仅作游标，不参与排序）；
  `t` 为墙钟毫秒（事件排序依据，DOM 事件用内核时间戳 `kt`）。
- 存储：世界级 `collections.deque(maxlen=600)` 环形缓冲，随世界销毁清除（无跨会话泄漏）。
- 线程安全：`w["tl_lock"]`（RLock），监听器线程（`_rt`）与工具执行线程都可能入账。
- 隐私：URL 经 `_evidence_norm_url` 归一化（去 query 参数，防 token 泄漏）；不存响应体。

### 2.2 三层接入点

| 层 | 接入点 | 事件类型 |
|---|---|---|
| 动作生命周期 | `_impl_with_status`（`_tl_action` start/end） | `action`，end 附后果卡主标签 |
| 网络 | `_rt` 监听器（request/response/requestfailed，URL 归一化） | `request`/`response`/`requestfailed` |
| DOM 变化 | `_tl_merge_dom` 懒合并（内核 `agentWorld.changes` 游标推进，按 `kt` 排序批量入账） | `dom`（add/remove/update/bulk） |
| 前提失效 | `_assumption_check` 失效分支 | `premise`，带 `derived_from` = 最近响应 seq |

### 2.3 降噪三规则

真实站点实测（GitHub git/git 仓库，打开后点击 Pull requests）：未降噪时间线 600/600 条为
DOM 事件（526 条 update + 70 条 remove），action/request 被挤到缓冲边缘。噪声源不是单元素
重绘，而是两类系统行为：

1. **初始快照**：`world_open` 后首次合并时整个页面都是"新元素"；
2. **整页替换**：点击导航后旧 DOM 批量卸载（300+ remove）+ 新页面元素全部"新 name"
   （300+ update）。

试错记录：按 id 限流无效（渐进扫描每次重建元素，el id 全变）；按 name 限流无效
（扫描是逐元素 touch，每个 name 只出现一次）。最终三规则：

- **初始快照不入账**：首次合并只把 update 的 name 收集进集合，不产生事件
  （世界初始状态不是"变化"）。
- **批量替换聚合**：同批 add+remove > 50 条（导航/刷新/SPA 整块替换）→ 聚合为一条
  `dom`/`bulk` 事件（`{add, remove}` 计数）。结构信号保留，明细不淹没有用事件。
- **update 按新 name 入账**：name 已见过的 update 跳过（扫描 touch）；真实内容变化
  （如价格 800→1200）必然产生新 name → 入账。

效果：**600 → 38 条（-94%）**，其中 request 16 / response 17 / action 2 / dom 2（两条 bulk）。

### 2.4 因果窗口

`_timeline_causal_windows`：按 action 事件切段，每段 = 该动作 start 到下一动作 start 之间的
全部事件。每窗口输出 `{action, from_seq, counts, statuses, key}`：

- `counts`：各类型计数（request/response/dom/premise/failed/console…）；
- `key`：仅保留高价值项——前提失效、failed、4xx/5xx 响应、JSON 接口响应（`{type: api, url, status}`）。

GitHub 演示实况：

```text
world_click (from_seq=10) → counts {request:11, response:10, dom:1}, statuses [200×8]
    ← 点击 Pull requests 引发的完整页面导航
world_click (from_seq=34) → counts {request:1, response:2, dom:1}, statuses [200, 204]
    key: [{type: api, url: github.com/pull_request_review_decisions, status: 200}]
    ← SPA 数据请求精确归因
```

### 2.5 静默失败标注（L1 盲区）

`digest` 模式：若窗口内有 4xx/5xx 或 `failed` 事件，且没有任何 DOM 变化 → 标注
`silent_failures`（"动作失败了但页面没有变化"——纯前后快照比对的盲区）。

## 3. 验证

集成测试（`experiments/test_server_timeline.py`，7 项全过）：

1. `world_timeline` 工具注册；
2. 动作+网络同线入账（fill + fetch）；
3. 三层同线（click 后 DOM 回执更新出现）；
4. 因果窗口（click 窗口归因 dom 变化）；
5. 前提失效入账（`derived_from` 指向触发响应 seq）；
6. 游标增量读不重不漏；
7. 4xx 入账（`statuses: {404: 1}`，GitHub API 真 404）。

测试中的坑（非实现缺陷，均已修正测试）：本地 file:// 夹具无网络活动（需显式
`fetch(..., {mode:'no-cors'})` 造网络事件）；fill 的 value 变化对 MutationObserver 静默
（捕获矩阵既定行为，DOM 断言移到 click 之后）；CORS 拦截的 fetch 只产生 requestfailed
无 response（改用支持 CORS 的 GitHub API 造真 404）。

## 4. 六个价值点（落地对照）

| 价值点 | 落地位置 |
|---|---|
| 因果归因 | `causal_windows`：动作 → 窗口内请求/响应/DOM |
| 自适应窗口 | 按动作切段：导航窗口 vs SPA 数据窗口自然不同 |
| 跨层互验 | 动作 outcome + 窗口内状态码 + DOM 变化互相印证 |
| 前提订阅升级 | `premise.derived_from` 可回放定位触发事件 |
| 查询代替重读 | `world_timeline(since=cursor)` 增量摘要，免重读页面 |
| 回放/复盘 | raw 模式 + seq 游标可重放一段操作 |

## 5. 后续方向

- DOM 明细进一步按语义分级（dialog/menu/option 等强信号构件优先）；
- 网络事件附加"资源类型"聚合（css/js/img 与 api 分流）；
- 时间线跨动作聚合为"任务片段"（多动作 → 一个意图单元）。
