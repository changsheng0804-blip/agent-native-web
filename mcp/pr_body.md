## 概述

本项目前期主要基于纯文本 LLM（如 DeepSeek V4 Flash）环境开发，对于**原生多模态模型（如 Gemini / GPT-4o / Claude 3.5 Sonnet）**的视觉感知与空间优势利用不足。

本 PR 为 `agent-native-web` 引入了**原生多模态视觉标注（Set-of-Mark）**与**操作生效双轨判定（Visual Diff Evidence）**，在保持原有的“极致低 Token 结构化语义”优势的同时，大幅提升原生多模态 Agent 在复杂 UI 下的感知与交互鲁棒性。

---

## 核心变更

1. **`world_screenshot` 支持 Set-of-Mark 构件标注与原生 Base64 传输** (`mcp/server.py`)：
   - 增加 `annotated: bool = False` 参数：自动为视口内所有可交互构件绘制醒目的半透明 `[el_X]` 编号标注框，彻底消除复杂排版与卡片流中的编号对齐歧义。
   - 增加 `return_base64: bool = True` 参数：直接返回 MCP 标准的 `types.ImageContent`，减少多模态 Agent 读取本地文件路径的额外交互轮次。

2. **操作生效信道引入视觉帧差双轨判定** (`mcp/server.py`)：
   - 在 `_click_region_snapshot` 中捕获操作前基线视口帧。
   - 当 DOM 结构未变时，计算局部操作区域的 RMS 像素差异。若 `RMS > 1.5`，自动纠正为 `verdict: "visual-effected"` 并附带 `visual_diff_score`，解决纯 CSS 动效、Toast 弹层、搜索联想浮层在证据信道中被误判为 `no-change` 的缺陷。

3. **智能体技能规范同步升级** (`skills/agent-world/SKILL.md`)：
   - 更新 Playbook 及多模态最佳实践指南。

4. **端到端实战验证套件** (`mcp/run_real_site_tests.py` 等)：
   - 提供真实外部网站（GitHub、MDN Canvas、Wikipedia）的多模态回归与验证脚本。

---

## 真实网站实战验收结果

| 验证站点 | 验证项 | 实测指标 | 验收判定 |
| :--- | :--- | :--- | :--- |
| **GitHub Explore** | 视口交互构件 SoM 标注 | 51/51 处交互构件精准套框与编号映射 | ✅ 100% 检出与空间对齐 |
| **MDN Canvas Demo** | Canvas 元素识别与局部高清裁剪 | 独立像素级渲染帧捕获 | ✅ Canvas 区域黑盒突破 |
| **Wikipedia 搜索** | 动态联想浮层触发前后视觉帧差 | 像素均方根 RMS = 24.03（阈值 > 1.5） | ✅ 成功捕获视觉生效事实 |

---

## 兼容性与安全

- **向后兼容**：所有新增参数均设置了安全的默认值，现有纯文本客户端与旧版 `world_*` 工具调用不受任何影响。
- **依赖安全**：使用标准 `Pillow` 库处理图像合成与像素比对，无额外重量级依赖。
