# 浏览器扩展内核

这里是 Agent-Native Web 的网页注入内核。完整项目入口请阅读 [项目指南](../docs/项目指南与架构.md)。

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

## 活动范围与历史原型

`manifest.json` 中声明的 `engine/`、`content/`、`api/` 是当前扩展加载入口；服务器使用对应的 `all-in-one.js`。扩展按钮打开中文用途说明，不提供实时控制面板。

`runtime/`、`overlay/`、`ui/`、`popup/` 和根目录 `content.js` 属于旧原型，当前清单不加载它们；保留用于历史对照，不代表已支持的第二套运行方式。旧弹窗直接等待自身页面的网页运行时，不能当作当前扩展控制台使用。

扩展内核版本 `1.0.0` 与项目实验版本 `0.1.0` 是不同层的版本；当前没有浏览器商店或软件包正式发布。原有矢量图标仅保留为设计素材，扩展使用浏览器默认图标。
