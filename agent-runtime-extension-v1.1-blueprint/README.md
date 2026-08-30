# Agent Runtime Adapter V1

> 实时将网页翻译成 Agent 可查询世界

## 核心特性

- 🌍 **世界状态实时同步** — 不是 scan once，而是 live runtime world
- 📊 **Occupancy Grid 自动更新** — 增量更新（diff），不是重新扫描整个页面
- 🔌 **统一 API** — `window.agentWorld.query.*` 对 Agent 暴露

## 文件结构

```
agent-runtime-extension/
├── manifest.json          # Chrome 扩展配置 (MV3)
├── content/
│   ├── bootstrap.js       # 入口：初始化整个 Runtime
│   ├── runtime.js         # AgentRuntime 类：核心调度
│   ├── observer.js        # MutationObserver：实时 DOM 变化监听
│   └── overlay.js         # 可视化覆盖层
├── engine/
│   ├── scanner.js         # DOM Scanner：单个元素扫描
│   ├── occupancy.js       # OccupancyGrid 类：占位网格 + 增量更新
│   ├── topology.js        # 拓扑关系：邻近/包含/对齐/导航路径
│   ├── semantics.js       # 语义推断：role/attention/importance
│   ├── visibility.js      # 可见性计算：inViewport/occlusion/opacity
│   └── query.js           # SpatialQuery 类：Agent 查询引擎
├── api/
│   └── agent-world.js     # window.agentWorld API 统一暴露
├── ui/
│   └── dev-panel.js       # 开发面板（可选拓展）
├── icons/
│   ├── icon16.svg
│   ├── icon48.svg
│   └── icon128.svg
└── all-in-one.js          # 合并版本，便于手动注入测试
```

## 安装方式

### 方式一：Chrome 扩展（推荐）

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启右上角的「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `agent-runtime-extension` 目录
5. 扩展图标将出现在工具栏

### 方式二：手动注入（测试用）

```javascript
// 在浏览器控制台执行
// 或创建一个书签：
javascript:(function(){var s=document.createElement('script');s.src='./all-in-one.js';document.head.appendChild(s);})();
```

### 方式三：Tampermonkey / Violentmonkey

```javascript
// ==UserScript==
// @name         Agent Runtime Adapter
// @namespace    http://tampermonkey.net/
// @version      1.0.0
// @description  实时将网页翻译成 Agent 可查询世界
// @match        <all_urls>
// @grant        none
// @run-at       document_idle
// ==/UserScript==

// 复制 all-in-one.js 的内容到这里
```

## API 文档

### 基础查询

```javascript
// 获取页面摘要
agentWorld.query.getPageSummary()
// 返回: { total, interactive, inViewport, emptyRegions, viewport, scroll, semanticTypes }

// 自然语言描述
agentWorld.query.describe()
// 返回: "页面包含 42 个元素，12 个可交互，28 个在当前视口内..."

// 完整快照
agentWorld.query.getSnapshot()
// 返回: 包含所有数据的完整页面状态
```

### 元素查找

```javascript
// 按语义角色查找
agentWorld.query.findByRole('button')    // 所有按钮
agentWorld.query.findByRole('link')       // 所有链接
agentWorld.query.findByRole('input')      // 所有输入框
agentWorld.query.findByRole('navigation') // 导航区域

// 按标签查找
agentWorld.query.findByTag('a')           // 所有 <a> 标签
agentWorld.query.findByTag('button')     // 所有 <button> 标签

// 可交互元素
agentWorld.query.findInteractive()

// 当前视口内的元素
agentWorld.query.findInViewport()

// 获取元素详情
agentWorld.query.getElement('el_div_123')
// 返回: { id, tag, text, bounds, grid, interactive, semantic, attributes, ... }
```

### 空间查询

```javascript
// 查找邻近元素
agentWorld.query.nearby('el_div_123', 3)  // 3格半径内的元素

// 获取元素的邻居方向
agentWorld.query.getNeighbors('el_div_123')
// 返回: { top: [...], bottom: [...], left: [...], right: [...] }

// 查找空白区域
agentWorld.query.findEmptyRegions()
// 返回: [{ id, bounds, cells, areaPx, center }, ...]

// 导航路径
agentWorld.query.navigationPath()
// 返回: [{ row: 0, elements: [...] }, ...]
```

### 可视化

```javascript
// 切换覆盖层模式
agentWorld.toggleOverlay('grid')      // 显示网格
agentWorld.toggleOverlay('elements') // 显示元素边框
agentWorld.toggleOverlay('regions')  // 显示空白区域
agentWorld.toggleOverlay('all')      // 显示全部
agentWorld.toggleOverlay('off')       // 关闭
```

### 刷新

```javascript
// 强制全量刷新
agentWorld.refresh()
```

## 使用示例

### 示例 1：获取页面结构

```javascript
const summary = agentWorld.query.getPageSummary();
console.log(`页面共有 ${summary.total} 个元素`);
console.log(`可交互: ${summary.interactive}, 视口内: ${summary.inViewport}`);
```

### 示例 2：找到所有按钮并获取邻居

```javascript
const buttons = agentWorld.query.findByRole('button');
buttons.forEach(id => {
  const neighbors = agentWorld.query.getNeighbors(id);
  console.log(`${id} 的邻居:`, neighbors);
});
```

### 示例 3：描述页面布局

```javascript
console.log(agentWorld.query.describe());
// 输出: "页面包含 42 个元素，12 个可交互，28 个在当前视口内。
//       语义类型: content(15), link(8), button(4), heading(3), navigation(2)。
//       页头区域有 5 个元素。主体区域有 32 个元素。页脚区域有 5 个元素。"
```

### 示例 4：找到可点击的元素

```javascript
const interactive = agentWorld.query.findInteractive();
const inViewport = agentWorld.query.findInViewport();
const clickable = interactive.filter(id => inViewport.includes(id));
console.log('可点击的元素:', clickable);
```

### 示例 5：查找页面空白区域

```javascript
const empty = agentWorld.query.findEmptyRegions(20);
console.log('大面积空白区域:', empty);
```

## 验证命令

```bash
# 验证文件完整性
ls -la ./Agent-Native-实验/agent-runtime-extension/content/
ls -la ./Agent-Native-实验/agent-runtime-extension/engine/
ls -la ./Agent-Native-实验/agent-runtime-extension/api/

# 验证 JS 语法正确性
node -c ./Agent-Native-实验/agent-runtime-extension/content/bootstrap.js
node -c ./Agent-Native-实验/agent-runtime-extension/content/runtime.js
node -c ./Agent-Native-实验/agent-runtime-extension/content/observer.js
node -c ./Agent-Native-实验/agent-runtime-extension/content/overlay.js
node -c ./Agent-Native-实验/agent-runtime-extension/engine/scanner.js
node -c ./Agent-Native-实验/agent-runtime-extension/engine/occupancy.js
node -c ./Agent-Native-实验/agent-runtime-extension/engine/topology.js
node -c ./Agent-Native-实验/agent-runtime-extension/engine/semantics.js
node -c ./Agent-Native-实验/agent-runtime-extension/engine/visibility.js
node -c ./Agent-Native-实验/agent-runtime-extension/engine/query.js
node -c ./Agent-Native-实验/agent-runtime-extension/api/agent-world.js
node -c ./Agent-Native-实验/agent-runtime-extension/all-in-one.js
```

## 技术原理

### 增量更新机制

1. **MutationObserver** 监听 DOM 变化（添加/删除节点、属性变化）
2. **增量 diff**：只处理变化的元素，而非全量扫描
3. **防抖**：150ms 防抖，避免频繁更新
4. **滚动/resize**：只更新可见性，不重建拓扑

### 空间索引

- **OccupancyGrid**: 40px 网格，记录每个格子的元素占用
- **拓扑关系**: 计算每个元素的上下左右邻居
- **导航路径**: 按 y 坐标分行，构建阅读顺序

### 语义推断

1. ARIA role 优先
2. 语义化 HTML 标签次之
3. class/id 启发式匹配兜底

## 注意事项

- ⚠️ 纯 JavaScript，无外部依赖
- ⚠️ 首次加载会扫描整个 DOM，有一定性能开销
- ⚠️ 后续增量更新，开销较小
- ⚠️ `window.agentWorld._runtime` 为内部引用，不建议直接使用

## License

MIT
