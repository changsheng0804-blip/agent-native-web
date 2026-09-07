# 贡献与工程协作约定（多智能体适用）

## 项目核心（不可破坏的红线）
- 对外协议默认 6 词:`open → guide → find → act → outcome -> close`;每次动作返回统一后果卡(`page_outcome` 五态)。
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

## 测试与门禁
- 日常:`python mcp/run_quality.py --scope <守护面>`;提交合并前 offline 全量必须全绿:
  `python mcp/run_quality.py`(串行约 13 分钟 / `--parallel 3` 约 5-6 分钟)。
- 新增离线测试必须注册进 `mcp/run_quality.py` 的 offline 组并标注守护面。
- 真实网站测试失败先怀疑网络/反爬,人工判断后再改代码。

## 依赖
- 依赖统一声明在 `pyproject.toml`;新增 import 第三方库时必须同步登记。

## 仓库卫生
- 运行时产物(profiles/screenshots/memory/runtime_traces/artifacts/报告)一律不入库,已由 .gitignore 覆盖。
- 过期文档移入 `docs/archive/`,顶层只保留长期维护的设计/规格/探索/经验文档(见 docs/README.md 索引)。
