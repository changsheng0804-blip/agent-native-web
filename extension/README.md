# 浏览器扩展内核

这里是 Agent Web Suite 的网页注入内核。完整项目入口请阅读 [项目指南](../docs/项目指南与架构.md)。

## 三种使用方式

1. MCP 服务器自动读取 `all-in-one.js` 注入网页。
2. 在 Chrome 的扩展管理页加载本目录。
3. 将 `all-in-one.js` 注入 Playwright、Tampermonkey 或其他浏览器自动化环境。

## 构建合并文件

修改 `engine/`、`content/` 或 `api/` 后执行：

```bash
python scripts/build_all_in_one.py
node --check all-in-one.js
```

`all-in-one.js` 是运行产物，必须与分文件源码保持同步。
