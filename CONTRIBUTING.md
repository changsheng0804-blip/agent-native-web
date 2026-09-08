# 贡献与工程协作约定（多智能体适用）

## 项目核心（不可破坏的红线）
- 对外协议默认 6 词:`open → guide → find → act → outcome → close`;每次动作返回统一后果卡(`page_outcome` 五态)。
- 旧 `world_*` 工具保留并标记 `[内部/调试]`;`AGENT_WORLD_LITE=1` 只暴露 6 词。
- `validate_closed_loop` 的 FP=0 一票否决("没生效"误报"成功"即失败)。
- 修改 `extension/engine|content|api` 后必须:`python extension/scripts/build_all_in_one.py` + `node --check extension/all-in-one.js`。

## 分支规范
- 命名:`feat/`(功能)、`codex/<agent>-<topic>`(智能体任务)、`experiment/`(实验)、`proposal/`(RFC)、`wip/`(半成品,禁止直合 master)。
- 一个分支一个目的;合并进 master 后**立即删除**(本地+远端);半成品用 `archive/*` tag 归档后再删。
- 所有变更（包括文档和小修复）都走 PR（合并申请）；master 禁止直推、强制推送和删除。
- 合并要求分支跟上最新主线、“工程验收”通过、讨论已解决。当前为单人维护，不要求另一名审核人批准，避免无法自审造成死锁。
- 合并使用 squash（把本次申请收为一个提交），远端自动删除已合并分支；本地更新 master 后删除对应任务分支。

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
  `python -m ruff check mcp scripts` 本地自查；CI 即每次合并前自动执行的检查。
- GitHub 在 Python 3.10、3.12 上执行全部离线测试，“工程验收”汇总任务是主分支唯一必需检查。
- `--scope kernel/observer/identity` 默认只选择离线覆盖；真实网站需加 `--real` 或显式选择 `--scope real-site` 等真实网站专属范围。
- 未知守护面、冲突参数和非正并发数立即报错，防止漏测后误报通过。
- 真实网站测试失败先怀疑网络/反爬,人工判断后再改代码。

## 依赖
- 依赖统一声明在 `pyproject.toml`;新增 import 第三方库时必须同步登记。
- 当前按源码运行，不以 `pip install .` 或发布安装包作为支持入口。请在项目根目录使用独立虚拟环境：
  ```bash
  python -m venv .venv
  # Windows PowerShell：.venv/Scripts/Activate.ps1
  # Linux/macOS：source .venv/bin/activate
  python -m pip install -r 依赖锁定-requirements.txt
  python -m playwright install chromium
  python scripts/工程治理-check.py
  ```
- 锁定清单保存所有依赖的具体版本，避免各环境安装结果漂移。变更声明后使用 `uv`（Python 依赖管理工具）重建：
  ```bash
  uv pip compile pyproject.toml --extra dev --universal --python-version 3.10 --output-file 依赖锁定-requirements.txt
  ```
  主动升级版本时增加 `--upgrade`，重新安装并通过完整验收后合并。开发工具也在锁定清单中。
- `mcp/` 内部按分层组织,依赖只允许自下而上(被依赖层不得反向引用上层):
  `aw_core` ← `aw_runtime` ← {`aw_status`, `aw_query`, `aw_timeline`} ← `aw_outcome` ←
  `aw_taskgraph` ← `aw_actions`;`aw_guide` 依赖 `aw_query`(归属查询面);
  `server.py` 是唯一调度入口,负责组装各层并注册 MCP 工具。

## 仓库卫生
- 运行时产物(profiles/screenshots/memory/runtime_traces/artifacts/报告)一律不入库,已由 .gitignore 覆盖。
- 过期文档移入 `docs/archive/`,顶层只保留长期维护的设计/规格/探索/经验文档(见 docs/README.md 索引)。
- 新增自定义文档与脚本采用“中文名-English”命名；现有工具入口、协议字段和 GitHub 规定的配置名称保留兼容名称，界面与模板正文使用中文。
- 范围、历史工作区、版本、许可待决事项和复查机制统一见[项目治理](docs/项目治理-governance.md)。
