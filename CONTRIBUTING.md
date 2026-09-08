# 贡献与工程协作约定（多智能体适用）

## 项目核心（不可破坏的红线）
- 对外协议默认 6 词:`open → guide → find → act → outcome → close`;每次动作返回统一后果卡(`page_outcome` 五态)。
- 旧 `world_*` 工具保留并标记 `[内部/调试]`;`AGENT_WORLD_LITE=1` 只暴露 6 词。
- `validate_closed_loop` 的 FP=0 一票否决("没生效"误报"成功"即失败)。
- 修改 `extension/engine|content|api` 后必须:`python extension/scripts/build_all_in_one.py` + `node --check extension/all-in-one.js`。

## 分支规范
- 命名:`feat/`(功能)、`codex/<agent>-<topic>`(智能体任务)、`experiment/`(实验)、`proposal/`(RFC)、`wip/`(半成品,禁止直合 master)。
- 一个分支一个目的;合并进 master 后**立即删除**(本地+远端);半成品用 `archive/*` tag 归档后再删。
- master 直推仅限文档与小修复;特性一律走 PR。

## 提交纪律
- 提交信息只写变更内容,**不携带门禁报告全文**(报告放 `docs/archive/` 或运行时文件)。
- 提交者身份用统一约定名(`git config user.name`),不使用占位名(如 `user`)。

## 提交溯源(多对话/多智能体必读)
同一个智能体常有多个对话并行工作,仅靠 user.name 无法区分提交来自哪次对话。约定三层方案:

1. **身份**:`user.name` = 智能体名(如 `codex`、`dimagent`);`user.email` 用 GitHub noreply
   (`287860629+changsheng0804-blip@users.noreply.github.com`),保证头像与归属正确。
2. **任务溯源(自动)**:一个对话通常对应一个分支。启用仓库钩子后,每次提交自动追加
   `Task: <分支名>` trailer——分支合并、删除后依然可追溯到任务。新克隆需一次性执行:
   ```bash
   git config core.hooksPath .githooks
   ```
3. **会话溯源(可选)**:对话启动时 `export AGENT_SESSION_ID=<会话短id>`,提交自动追加
   `Session:` trailer。审计方式:`git log --format='%B' -1 <sha>` 查看 Task/Session 行。

## 测试与门禁
- 日常:`python mcp/run_quality.py --scope <守护面>`;提交合并前 offline 全量必须全绿:
  `python mcp/run_quality.py`(串行,未基准测试 / `--parallel 3` 约 3 分钟,建议以并行为准)。
- 新增离线测试必须注册进 `mcp/run_quality.py` 的 offline 组并标注守护面;修改
  `run_quality.py` 的 GROUPS 后必须同步更新根 README 的门禁数字(offline/real 项数)。
- CI 已含 ruff 正确性检查(`mcp/` 下 E9/F63/F7/F82,规则见 pyproject.toml),提交前可用
  `ruff check mcp` 本地自查。
- 真实网站测试失败先怀疑网络/反爬,人工判断后再改代码。

## 依赖
- 依赖统一声明在 `pyproject.toml`;新增 import 第三方库时必须同步登记。
- `mcp/` 内部按分层组织,依赖只允许自下而上(被依赖层不得反向引用上层):
  `aw_core` ← `aw_runtime` ← {`aw_status`, `aw_query`, `aw_timeline`} ← `aw_outcome` ←
  `aw_taskgraph` ← `aw_actions`;`aw_guide` 依赖 `aw_query`(归属查询面);
  `server.py` 是唯一调度入口,负责组装各层并注册 MCP 工具。

## 仓库卫生
- 运行时产物(profiles/screenshots/memory/runtime_traces/artifacts/报告)一律不入库,已由 .gitignore 覆盖。
- 过期文档移入 `docs/archive/`,顶层只保留长期维护的设计/规格/探索/经验文档(见 docs/README.md 索引)。
