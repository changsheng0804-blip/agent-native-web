# -*- coding: utf-8 -*-
"""Verification Value Benchmark v1 — H0 实验执行器 / 审计器基础设施。

本包只实现执行器与审计器：
  - 冻结协议（A/B/C 定义、task 文本、成功条件、verifier 语义、gate policy、
    P0 口径、四场景核心机制）一律从仓库文件只读加载，代码内不复制第二套阈值；
  - 不修改生产 MCP / extension；
  - 原始产物输出到 gitignore 的 mcp/experiments/artifacts/。

H0 的任何模型表现数据都不得用于推断 Astra、A/B/C 或项目战略价值。
"""
