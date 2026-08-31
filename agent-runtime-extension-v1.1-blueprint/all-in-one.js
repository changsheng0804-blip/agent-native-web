// ===== engine/scanner.js =====
// 全局命名空间
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  const GRID_SIZE = 40;
  let idCounter = 0;
  const idMap = new WeakMap(); // 稳定ID映射

  /**
   * 为元素生成稳定ID（强 ID，统一编号空间）
   * 规则：el_<seq>，全局递增，WeakMap 绑定 DOM 节点。
   * 节点存活期间 ID 绝不改变（即使重排/改属性/改位置）。
   * 页面自带 id 不作为主 ID（避免 7/18 等裸数字污染编号空间），
   * 存入 attributes.id 作为弱标识。
   */
  function getStableId(el) {
    if (idMap.has(el)) return idMap.get(el);
    const id = `el_${++idCounter}`;
    idMap.set(el, id);
    return id;
  }

  /**
   * 生成语义名字（弱 ID 基础）：role.slug
   * 优先级：aria-label/title/placeholder/alt > 可见文本 > class 启发式
   */
  function slugify(s) {
    return s.toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'unnamed';
  }

  function generateName(el, tag, semantic) {
    // 根级容器固定命名
    if (tag === 'html') return 'root.html';
    if (tag === 'body') return 'root.body';
    let label =
      el.getAttribute('aria-label') ||
      el.getAttribute('title') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('alt');
    if (!label) {
      // 文本长度合适（≤60 字符）才用作名字：短文本=按钮/链接/下拉等可命名构件，
      // 超长文本=容器/段落（整页文本如 html/body 由 root 兜底）
      const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
      if (text.length > 0 && text.length <= 60) {
        label = text.slice(0, 40);
      }
    }
    if (!label) {
      const cn = el.className;
      const clsName = (typeof cn === 'string' ? cn : (cn && cn.baseVal) || '').toLowerCase();
      label = clsName.split(/[\s-_]+/)[0] || '';
    }
    return `${semantic}.${slugify(label)}`;
  }

  /**
   * 扫描单个元素（不是整个页面）
   */
  function scanElement(el) {
    if (!el || !el.getBoundingClientRect) return null;
    
    const rect = el.getBoundingClientRect();
    
    // 过滤太小/不可见的元素
    if (rect.width < 3 || rect.height < 3) return null;
    
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return null;
    
    // IPI 伪隐藏过滤:color:white 同色 / 移出视口 / font-size:0 / text-indent / aria-hidden
    // (display/visibility/opacity 之外的五种"伪隐藏"泄露缺口,见 IPI 备忘录 VEC_4~VEC_8)
    if (global.AgentRuntime.visibility.isPseudoHidden(el, style, rect)) return null;
    
    // 过滤纯装饰/无意义元素
    const tag = el.tagName.toLowerCase();
    const skipTags = new Set(['br','hr','script','style','link','meta','noscript','svg','path','g','defs','use']);
    if (skipTags.has(tag)) return null;
    
    const id = getStableId(el);
    const semantic = inferSemanticRole(el, tag);
    
    return {
      id,
      name: generateName(el, tag, semantic),
      tag,
      text: (el.textContent || '').trim().substring(0, 100),
      bounds: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height)
      },
      grid: {
        gx: Math.floor(rect.x / GRID_SIZE),
        gy: Math.floor(rect.y / GRID_SIZE),
        gw: Math.ceil(rect.width / GRID_SIZE),
        gh: Math.ceil(rect.height / GRID_SIZE)
      },
      interactive: isInteractive(el, tag, style),
      semantic,
      attributes: {
        role: el.getAttribute('role'),
        ariaLabel: el.getAttribute('aria-label'),
        id: el.id || '',
        className: (typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '') || '',
        href: el.getAttribute('href'),
        type: el.getAttribute('type'),
        placeholder: el.getAttribute('placeholder'),
        value: (tag === 'input' || tag === 'textarea') ? (el.value || '') : undefined
      },
      depth: getDepth(el),
      _el: el // 保留 DOM 引用（内部使用，不暴露给 Agent）
    };
  }

  function isInteractive(el, tag, style) {
    const interactiveTags = new Set(['a','button','input','select','textarea','option','details','summary']);
    if (interactiveTags.has(tag)) return true;
    if (el.getAttribute('onclick') || el.getAttribute('tabindex')) return true;
    if (style.cursor === 'pointer') return true;
    if (el.getAttribute('role') === 'button') return true;
    return false;
  }

  function inferSemanticRole(el, tag) {
    // 1. ARIA role
    const ariaRole = el.getAttribute('role');
    if (ariaRole) return ariaRole;
    
    // 2. 语义化标签
    const tagRoles = {
      nav: 'navigation', header: 'banner', footer: 'contentinfo',
      main: 'main', aside: 'complementary', article: 'article',
      section: 'region', form: 'form', button: 'button',
      a: 'link', input: 'input', select: 'listbox',
      textarea: 'textbox', h1: 'heading', h2: 'heading',
      h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading',
      img: 'img', video: 'video', audio: 'audio',
      table: 'table', ul: 'list', ol: 'list', li: 'listitem',
      dialog: 'dialog', menu: 'menu'
    };
    if (tagRoles[tag]) return tagRoles[tag];
    
    // 3. class/id 启发式
    const cn = el.className;
    const clsName = (typeof cn === 'string' ? cn : (cn && cn.baseVal) || '').toLowerCase();
    const cls = clsName + ' ' + (el.id || '').toLowerCase();
    const heuristics = [
      [/nav|menu/, 'navigation'], [/btn|button/, 'button'],
      [/card/, 'card'], [/hero|banner/, 'banner'],
      [/sidebar/, 'complementary'], [/modal|dialog|popup/, 'dialog'],
      [/tab/, 'tab'], [/dropdown/, 'listbox'],
      [/tooltip/, 'tooltip'], [/carousel|slider/, 'group'],
      [/cta/, 'cta'], [/footer/, 'contentinfo'],
      [/header/, 'banner']
    ];
    for (const [pattern, role] of heuristics) {
      if (pattern.test(cls)) return role;
    }
    
    return 'content';
  }

  function getDepth(el) {
    let depth = 0;
    let current = el;
    while (current.parentElement) { depth++; current = current.parentElement; }
    return depth;
  }

  // 全量扫描（初始化用）
  function scanAll() {
    const elements = [];
    document.querySelectorAll('*').forEach(el => {
      const node = scanElement(el);
      if (node) elements.push(node);
    });
    return elements;
  }

  global.AgentRuntime.scanner = { scanElement, scanAll, getStableId, generateName, GRID_SIZE };
})(window);

// ===== engine/occupancy.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  class OccupancyGrid {
    constructor(cellSize = 40) {
      this.cellSize = cellSize;
      this.grid = [];
      this.cols = 0;
      this.rows = 0;
    }

    /**
     * 全量重建（初始化用）
     */
    rebuild(elements) {
      this.cols = Math.ceil(window.innerWidth / this.cellSize);
      this.rows = Math.ceil(document.body.scrollHeight / this.cellSize);
      
      // 初始化空网格
      this.grid = Array.from({ length: this.rows }, () =>
        Array.from({ length: this.cols }, () => ({ occupied: false, ids: [] }))
      );
      
      // 填充
      elements.forEach(el => this.paintElement(el));
    }

    /**
     * 增量更新：只更新变化的元素
     */
    incrementalUpdate(changedElements) {
      // 先清除旧位置
      changedElements.forEach(el => this.clearElement(el.id));
      // 再画新位置
      changedElements.forEach(el => this.paintElement(el));
    }

    /**
     * 画一个元素到网格
     */
    paintElement(el) {
      const { gx, gy, gw, gh } = el.grid;
      for (let y = gy; y < gy + gh && y < this.rows; y++) {
        for (let x = gx; x < gx + gw && x < this.cols; x++) {
          if (this.grid[y] && this.grid[y][x]) {
            this.grid[y][x].occupied = true;
            if (!this.grid[y][x].ids.includes(el.id)) {
              this.grid[y][x].ids.push(el.id);
            }
          }
        }
      }
    }

    /**
     * 清除一个元素在网格中的占位
     */
    clearElement(id) {
      for (let y = 0; y < this.rows; y++) {
        for (let x = 0; x < this.cols; x++) {
          if (this.grid[y] && this.grid[y][x]) {
            const idx = this.grid[y][x].ids.indexOf(id);
            if (idx !== -1) {
              this.grid[y][x].ids.splice(idx, 1);
              this.grid[y][x].occupied = this.grid[y][x].ids.length > 0;
            }
          }
        }
      }
    }

    /**
     * 查询某个网格格子的状态
     */
    getCell(gx, gy) {
      return this.grid?.[gy]?.[gx] || null;
    }

    /**
     * 检测重叠
     */
    detectOverlaps(elements) {
      const overlaps = [];
      for (let i = 0; i < elements.length; i++) {
        for (let j = i + 1; j < elements.length; j++) {
          const a = elements[i].grid, b = elements[j].grid;
          if (a.gx < b.gx + b.gw && a.gx + a.gw > b.gx &&
              a.gy < b.gy + b.gh && a.gy + a.gh > b.gy) {
            overlaps.push([elements[i].id, elements[j].id]);
          }
        }
      }
      return overlaps;
    }
  }

  global.AgentRuntime.OccupancyGrid = OccupancyGrid;
})(window);

// ===== engine/topology.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 构建拓扑关系
   */
  function buildTopology(elements) {
    const adjacency = new Map(); // id → {top, bottom, left, right}
    const navigationPath = [];
    
    // 按y坐标排序构建纵向浏览路径
    const sorted = [...elements].sort((a, b) => a.bounds.y - b.bounds.y);
    
    let prevRow = null;
    sorted.forEach(el => {
      const row = Math.floor(el.bounds.y / 100); // 100px为一行
      if (row !== prevRow) {
        navigationPath.push({ row, elements: [el.id] });
        prevRow = row;
      } else {
        navigationPath[navigationPath.length - 1].elements.push(el.id);
      }
    });
    
    // 计算每个元素的邻近关系
    elements.forEach(el => {
      const neighbors = { top: [], bottom: [], left: [], right: [] };
      
      elements.forEach(other => {
        if (el.id === other.id) return;
        const dist = elementDistance(el, other);
        if (dist > 3) return; // 超过3格不算邻居
        
        // 判断方向
        const dx = other.bounds.x - el.bounds.x;
        const dy = other.bounds.y - el.bounds.y;
        
        if (Math.abs(dy) > Math.abs(dx)) {
          if (dy < 0) neighbors.top.push({ id: other.id, dist });
          else neighbors.bottom.push({ id: other.id, dist });
        } else {
          if (dx < 0) neighbors.left.push({ id: other.id, dist });
          else neighbors.right.push({ id: other.id, dist });
        }
      });
      
      // 每个方向只保留最近的
      Object.keys(neighbors).forEach(dir => {
        neighbors[dir].sort((a, b) => a.dist - b.dist);
        neighbors[dir] = neighbors[dir].slice(0, 3).map(n => n.id);
      });
      
      adjacency.set(el.id, neighbors);
    });
    
    return { adjacency, navigationPath };
  }

  function elementDistance(a, b) {
    const GRID_SIZE = 40;
    const ax = a.bounds.x + a.bounds.w / 2;
    const ay = a.bounds.y + a.bounds.h / 2;
    const bx = b.bounds.x + b.bounds.w / 2;
    const by = b.bounds.y + b.bounds.h / 2;
    return Math.sqrt(((ax - bx) / GRID_SIZE) ** 2 + ((ay - by) / GRID_SIZE) ** 2);
  }

  global.AgentRuntime.topology = { buildTopology, elementDistance };
})(window);

// ===== engine/semantics.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 构建语义索引
   */
  function buildSemanticIndex(elements) {
    const byRole = {};
    const byRegion = {};
    
    elements.forEach(el => {
      // 按角色分组
      if (!byRole[el.semantic]) byRole[el.semantic] = [];
      byRole[el.semantic].push(el.id);
      
      // 按区域分组（用 y 范围划分：header/body/footer）
      const region = getRegion(el);
      if (!byRegion[region]) byRegion[region] = [];
      byRegion[region].push(el.id);
    });
    
    return { byRole, byRegion };
  }

  function getRegion(el) {
    const viewportH = window.innerHeight;
    const y = el.bounds.y;
    if (y < viewportH * 0.15) return 'header';
    if (y > document.body.scrollHeight - viewportH * 0.15) return 'footer';
    return 'body';
  }

  /**
   * 计算注意力权重
   */
  function calculateAttentionWeights(elements) {
    const maxArea = Math.max(...elements.map(e => e.bounds.w * e.bounds.h), 1);
    
    elements.forEach(el => {
      const sizeScore = (el.bounds.w * el.bounds.h) / maxArea;
      const positionScore = el.inViewport ? 1 : 0.3;
      const interactiveScore = el.interactive ? 1.5 : 1;
      const semanticScore = ['button','cta','link','navigation'].includes(el.semantic) ? 1.3 : 1;
      
      el.attentionWeight = Math.min(1, sizeScore * 0.3 + positionScore * 0.2 + interactiveScore * 0.3 + semanticScore * 0.2);
    });
    
    // 排序
    return [...elements].sort((a, b) => b.attentionWeight - a.attentionWeight);
  }

  /**
   * 生成自然语言布局描述
   */
  function generateLayoutDescription(elements, semanticIndex) {
    const { byRole, byRegion } = semanticIndex;
    const parts = [];
    
    const total = elements.length;
    const interactive = elements.filter(e => e.interactive).length;
    const inViewport = elements.filter(e => e.inViewport).length;
    
    parts.push(`页面包含 ${total} 个元素，${interactive} 个可交互，${inViewport} 个在当前视口内。`);
    
    // 描述语义结构
    const roles = Object.entries(byRole).sort((a,b) => b[1].length - a[1].length);
    if (roles.length > 0) {
      parts.push(`语义类型: ${roles.map(([r, ids]) => `${r}(${ids.length})`).join(', ')}。`);
    }
    
    // 描述空间布局
    const headerEls = byRegion.header || [];
    const footerEls = byRegion.footer || [];
    const bodyEls = byRegion.body || [];
    
    if (headerEls.length > 0) parts.push(`页头区域有 ${headerEls.length} 个元素。`);
    if (bodyEls.length > 0) parts.push(`主体区域有 ${bodyEls.length} 个元素。`);
    if (footerEls.length > 0) parts.push(`页脚区域有 ${footerEls.length} 个元素。`);
    
    // 描述密度
    const viewportWidth = window.innerWidth;
    const avgWidth = elements.reduce((s, e) => s + e.bounds.w, 0) / Math.max(total, 1);
    if (avgWidth > viewportWidth * 0.6) parts.push(`元素多为全宽布局。`);
    else if (avgWidth < viewportWidth * 0.3) parts.push(`元素多为窄列布局。`);
    
    return parts.join(' ');
  }

  /**
   * 名字去重：同名构件追加序号（button.card -> button.card-2）
   * 保证弱 ID 在任意时刻全局唯一，CAD 图纸编号原则
   */
  function dedupeNames(elements) {
    const seen = new Map();
    elements.forEach(el => {
      const n = seen.get(el.name) || 0;
      seen.set(el.name, n + 1);
      if (n > 0) el.name = `${el.name}-${n + 1}`;
    });
  }

  global.AgentRuntime.semantics = { buildSemanticIndex, calculateAttentionWeights, generateLayoutDescription, dedupeNames };
})(window);

// ===== engine/visibility.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 计算单个元素的可见性
   */
  function computeVisibility(el) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    
    const display = style.display;
    const visibility = style.visibility;
    const opacity = parseFloat(style.opacity || 1);
    const zIndex = parseInt(style.zIndex || 0);
    
    const isVisible = display !== 'none' && visibility !== 'hidden' && opacity > 0 && rect.width > 0 && rect.height > 0;
    const inViewport = rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
    
    // 视口覆盖率
    const viewportArea = window.innerWidth * window.innerHeight;
    const elArea = rect.width * rect.height;
    const visibleArea = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0)) *
                        Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
    const viewportCoverage = viewportArea > 0 ? visibleArea / viewportArea : 0;
    
    return {
      visible: isVisible,
      inViewport,
      opacity,
      zIndex,
      viewportCoverage: Math.round(viewportCoverage * 10000) / 10000,
      scrollPosition: {
        fromTop: Math.round(rect.top + window.scrollY),
        fromBottom: Math.round(document.body.scrollHeight - rect.bottom - window.scrollY)
      }
    };
  }

  /**
   * 批量更新所有元素的可见性
   */
  function updateAllVisibility(elements) {
    elements.forEach(el => {
      if (el._el) {
        const vis = computeVisibility(el._el);
        el.visible = vis.visible;
        el.inViewport = vis.inViewport;
        el.viewportCoverage = vis.viewportCoverage;
        el.opacity = vis.opacity;
        el.zIndex = vis.zIndex;
      }
    });
  }

  /**
   * 解析 computed color 字符串 → {r,g,b,a}
   * 兼容 rgb() / rgba() 两种格式
   */
  function parseColor(str) {
    if (!str) return null;
    const m = str.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    return {
      r: Math.round(parseFloat(m[1])),
      g: Math.round(parseFloat(m[2])),
      b: Math.round(parseFloat(m[3])),
      a: m[4] !== undefined ? parseFloat(m[4]) : 1
    };
  }

  /**
   * 向上追溯"有效纯色背景"：
   * - 从元素自身开始，找到第一个非透明 backgroundColor
   * - 若途中遇到 background-image（渐变/图片背景），返回 null（无法静态判定，不误伤白字+图背景的合法场景）
   */
  function effectiveBackgroundColor(el) {
    let node = el;
    while (node && node.nodeType === 1) {
      const st = getComputedStyle(node);
      if (st.backgroundImage && st.backgroundImage !== 'none') return null;
      const bg = st.backgroundColor;
      if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
        const c = parseColor(bg);
        if (c && c.a > 0) return c;
      }
      node = node.parentElement;
    }
    return null;
  }

  /**
   * 伪隐藏检测(IPI 攻防矩阵 VEC_4~VEC_8 的过滤缺口)
   * 在 display/visibility/opacity 三种"结构性隐藏"之外,补五种"伪隐藏":
   *   VEC_4 color:white(文字与有效背景同色)
   *   VEC_5 移出视口(position:absolute/fixed 且完全脱离视口上方/左侧)
   *   VEC_6 font-size:0
   *   VEC_7 text-indent 大幅负缩进
   *   VEC_8 aria-hidden="true"(含祖先链,遵循 ARIA 最近祖先覆盖语义)
   * 返回 true 表示"用户不可见,不应进入原生网页世界"。
   */
  function isPseudoHidden(el, style, rect) {
    if (!el || !el.closest || !style || !rect) return false;

    // VEC_8: aria-hidden="true"(自身或最近带 aria-hidden 的祖先)
    const ah = el.closest('[aria-hidden]');
    if (ah && (ah.getAttribute('aria-hidden') || '').trim().toLowerCase() === 'true') return true;

    // VEC_6: font-size:0 文字零号不可见
    if (parseFloat(style.fontSize) === 0) return true;

    // VEC_7: text-indent 大幅负缩进(文本被移出元素可视范围,经典 image-replacement 隐藏)
    if (parseFloat(style.textIndent) <= -100) return true;

    // VEC_5: 绝对/固定定位且完全脱离视口上方/左侧(不占文档流,滚动也不可达)
    // 只查负方向:向下/向右的大偏移可能是正常页尾/横向内容,避免误伤
    const pos = style.position;
    if ((pos === 'absolute' || pos === 'fixed') && (rect.right < -50 || rect.bottom < -50)) return true;

    // VEC_4: 文字与有效背景同色(或文字全透明)
    // 性能:仅元素含文本才做颜色比对(无文本元素不泄露文本,跳过祖先链 getComputedStyle 遍历)
    const color = parseColor(style.color);
    if (color && color.a === 0) return true; // 全透明文字
    if (color && color.a >= 0.99 && (el.textContent || '').trim().length > 0) {
      const bg = effectiveBackgroundColor(el);
      if (bg && bg.a >= 0.99 && bg.r === color.r && bg.g === color.g && bg.b === color.b) return true;
    }
    return false;
  }

  global.AgentRuntime.visibility = { computeVisibility, updateAllVisibility, isPseudoHidden };
})(window);

// ===== engine/query.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  class SpatialQuery {
    constructor(world) {
      this.world = world;
    }

    /** 按语义角色查找 */
    findByRole(role) {
      return (this.world.semanticIndex?.byRole?.[role] || []);
    }

    /** 按标签查找 */
    findByTag(tag) {
      return [...this.world.elements.values()]
        .filter(e => e.tag === tag.toLowerCase())
        .map(e => e.id);
    }

    /** 所有可交互元素 */
    findInteractive() {
      return [...this.world.elements.values()]
        .filter(e => e.interactive)
        .map(e => e.id);
    }

    /** 在视口内的元素 */
    findInViewport() {
      return [...this.world.elements.values()]
        .filter(e => e.inViewport)
        .map(e => e.id);
    }

    /** 获取元素详情 */
    getElement(id) {
      const el = this.world.elements.get(id);
      if (!el) return null;
      // 不暴露 _el 引用
      const { _el, ...safe } = el;
      return safe;
    }

    /** 查找邻近元素 */
    nearby(id, radius = 3) {
      const target = this.world.elements.get(id);
      if (!target) return [];
      return [...this.world.elements.values()]
        .filter(el => {
          if (el.id === id) return false;
          const dist = global.AgentRuntime.topology.elementDistance(target, el);
          return dist < radius;
        })
        .map(el => el.id);
    }

    /** 获取元素的邻居 */
    getNeighbors(id) {
      return this.world.topology?.adjacency?.get(id) || { top: [], bottom: [], left: [], right: [] };
    }

    /** 查找空白区域 */
    findEmptyRegions(minGridCells = 10) {
      const grid = this.world.occupancy;
      if (!grid || !grid.grid) return [];
      
      const visited = new Set();
      const regions = [];
      
      for (let y = 0; y < grid.rows; y++) {
        for (let x = 0; x < grid.cols; x++) {
          const key = `${x},${y}`;
          if (visited.has(key)) continue;
          if (grid.grid[y]?.[x]?.occupied) continue;
          
          // BFS 找连通空白区域
          const region = bfsEmpty(grid, x, y, visited);
          if (region.cells >= minGridCells) {
            regions.push({
              id: `region-${regions.length}`,
              bounds: region.bounds,
              cells: region.cells,
              areaPx: region.cells * grid.cellSize * grid.cellSize,
              center: {
                gx: Math.round((region.bounds.gx1 + region.bounds.gx2) / 2),
                gy: Math.round((region.bounds.gy1 + region.bounds.gy2) / 2)
              }
            });
          }
        }
      }
      
      return regions.sort((a, b) => b.cells - a.cells);
    }

    /** 导航路径 */
    navigationPath() {
      return this.world.topology?.navigationPath || [];
    }

    /** 自然语言描述 */
    describe() {
      const elements = [...this.world.elements.values()];
      return global.AgentRuntime.semantics.generateLayoutDescription(
        elements,
        this.world.semanticIndex
      );
    }

    /** 页面摘要 */
    getPageSummary() {
      const elements = [...this.world.elements.values()];
      const interactive = elements.filter(e => e.interactive);
      const inViewport = elements.filter(e => e.inViewport);
      const emptyRegions = this.findEmptyRegions();
      
      return {
        total: elements.length,
        interactive: interactive.length,
        inViewport: inViewport.length,
        emptyRegions: emptyRegions.length,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scroll: { y: window.scrollY, totalHeight: document.body.scrollHeight },
        semanticTypes: Object.keys(this.world.semanticIndex?.byRole || {}).length
      };
    }

    /** 完整快照 */
    getSnapshot() {
      const elements = [...this.world.elements.values()].map(e => {
        const { _el, ...safe } = e;
        return safe;
      });
      return {
        version: this.world.version,
        meta: this.world.meta,
        elements,
        summary: this.getPageSummary(),
        emptyRegions: this.findEmptyRegions(),
        navigationPath: this.navigationPath()
      };
    }

    /**
     * 构件清单（统一过滤式查询，CAD 构件表）
     * filter: { role, tag, text, name, interactive, inViewport, maxResults }
     */
    findEntities(filter = {}) {
      const {
        role, tag, text, name, interactive, inViewport, maxResults = 200
      } = filter;
      const result = [];
      for (const el of this.world.elements.values()) {
        if (role && el.semantic !== role) continue;
        if (tag && el.tag !== String(tag).toLowerCase()) continue;
        if (text && !(el.text || '').toLowerCase().includes(String(text).toLowerCase())) continue;
        if (name && !(el.name || '').toLowerCase().includes(String(name).toLowerCase())) continue;
        if (interactive !== undefined && el.interactive !== !!interactive) continue;
        if (inViewport !== undefined && el.inViewport !== !!inViewport) continue;
        result.push({
          id: el.id,
          name: el.name,
          tag: el.tag,
          semantic: el.semantic,
          text: el.text,
          bounds: el.bounds,
          interactive: el.interactive,
          inViewport: el.inViewport
        });
        if (result.length >= maxResults) break;
      }
      return result;
    }

    /**
     * 世界状态卡:显式暴露当前状态(弹窗/页面/表单/世界规模)
     * auth 登录态由 MCP server 层补充(cookie 信号,内核读不到 HttpOnly)
     */
    getStatus() {
      const s = this.world.status || {};
      return {
        dialogs: s.dialogs || [],
        page: s.page || {},
        forms: s.forms || [],
        world: {
          elements: this.world.elements.size,
          changesSeq: s.changesSeq || 0,
          version: this.world.version
        }
      };
    }

    /**
     * 构件详情（getElement 超集：含邻居/区域，不含 DOM 引用）
     */
    getEntity(id) {
      const el = this.world.elements.get(id);
      if (!el) return null;
      const { _el, ...safe } = el;
      // 实时刷新 input/textarea 的 value(动态输入后快照可能过期)
      if (_el && (_el.tagName === 'INPUT' || _el.tagName === 'TEXTAREA') && safe.attributes) {
        safe.attributes.value = _el.value || '';
      }
      safe.neighbors = this.world.topology?.adjacency?.get(id) || { top: [], bottom: [], left: [], right: [] };
      const viewportH = window.innerHeight;
      const y = el.bounds.y;
      safe.region = y < viewportH * 0.15 ? 'header'
        : (y > document.body.scrollHeight - viewportH * 0.15 ? 'footer' : 'body');
      return safe;
    }

    /**
     * 图层视图：结构/语义/空间/交互/名字
     */
    layers() {
      const elements = [...this.world.elements.values()];
      const byTag = {};
      elements.forEach(e => { byTag[e.tag] = (byTag[e.tag] || 0) + 1; });
      const byRole = this.world.semanticIndex?.byRole || {};
      const interactive = elements.filter(e => e.interactive).length;
      const unnamed = elements.filter(e => !e.name || e.name.endsWith('.unnamed')).length;
      return {
        structure: { total: elements.length, byTag },
        semantic: { byRole, types: Object.keys(byRole).length },
        spatial: {
          viewport: { width: window.innerWidth, height: window.innerHeight },
          scroll: { y: Math.round(window.scrollY), totalHeight: document.body.scrollHeight },
          grid: this.world.occupancy
            ? { cols: this.world.occupancy.cols, rows: this.world.occupancy.rows, cellSize: this.world.occupancy.cellSize }
            : null
        },
        interactive,
        names: { total: elements.length, named: elements.length - unnamed, unnamed }
      };
    }

    /**
     * 弱 ID 解析：强 ID / name / 页面原生 id / name 模糊
     */
    resolve(q) {
      if (!q) return null;
      if (this.world.elements.has(q)) {
        return { id: q, exact: true, kind: 'strong' };
      }
      const byName = [...this.world.elements.values()].filter(e => e.name === q);
      if (byName.length === 1) return { id: byName[0].id, exact: true, kind: 'name' };
      if (byName.length > 1) return { matches: byName.map(e => e.id), kind: 'name' };
      const byAttr = [...this.world.elements.values()].filter(e => e.attributes && e.attributes.id === q);
      if (byAttr.length === 1) return { id: byAttr[0].id, exact: true, kind: 'attr-id' };
      const fuzzy = [...this.world.elements.values()]
        .filter(e => (e.name || '').includes(q))
        .slice(0, 10);
      if (fuzzy.length > 0) return { matches: fuzzy.map(e => e.id), kind: 'name-fuzzy' };
      return null;
    }
  }

  function bfsEmpty(grid, startX, startY, visited) {
    const queue = [{ x: startX, y: startY }];
    visited.add(`${startX},${startY}`);
    let cells = 0;
    let gx1 = startX, gy1 = startY, gx2 = startX, gy2 = startY;
    
    while (queue.length > 0) {
      const { x, y } = queue.shift();
      cells++;
      gx1 = Math.min(gx1, x); gy1 = Math.min(gy1, y);
      gx2 = Math.max(gx2, x); gy2 = Math.max(gy2, y);
      
      const neighbors = [{ x: x-1, y }, { x: x+1, y }, { x, y: y-1 }, { x, y: y+1 }];
      for (const n of neighbors) {
        const key = `${n.x},${n.y}`;
        if (visited.has(key)) continue;
        if (n.x < 0 || n.x >= grid.cols || n.y < 0 || n.y >= grid.rows) continue;
        if (grid.grid[n.y]?.[n.x]?.occupied) continue;
        visited.add(key);
        queue.push(n);
      }
    }
    
    return { cells, bounds: { gx1, gy1, gx2, gy2 } };
  }

  global.AgentRuntime.SpatialQuery = SpatialQuery;
})(window);

// ===== content/observer.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let observer = null;
  const DEBOUNCE_MS = 150;
  // 持续变更兜底:页面懒加载/轮播/广告刷新会不断重置防抖计时器,
  // 若不设上限,onChange 可能永远不触发(原生网页世界饿死)。实战验证:
  // Booking.com 上 world 停滞在 678 个,forceRefresh 却抓到 2426 个。
  const MAX_WAIT_MS = 1000;

  /**
   * 启动 DOM 变化观察
   * 设计:累积式防抖(不丢中间批次) + maxWait 兜底(持续变更不饿死)
   */
  function startDOMObserver(onChange) {
    if (observer) observer.disconnect();

    let pending = [];       // 累积待处理的 mutation 批次
    let debounceTimer = null;
    let maxWaitTimer = null;

    function flush() {
      debounceTimer = null;
      maxWaitTimer = null;
      if (pending.length === 0) return;
      const batch = pending;
      pending = [];
      onChange(batch);
    }

    function onMutations(relevant) {
      if (relevant.length === 0) return;
      pending.push(...relevant);
      // 正常防抖:变更停止 150ms 后处理
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flush, DEBOUNCE_MS);
      // maxWait 兜底:若页面持续变更导致防抖一直被重置,
      // 首次变更后最多 MAX_WAIT_MS 一定强制 flush 一次
      if (!maxWaitTimer) {
        maxWaitTimer = setTimeout(() => {
          clearTimeout(debounceTimer);
          flush();
        }, MAX_WAIT_MS);
      }
    }

    observer = new MutationObserver((mutations) => {
      // 过滤有意义的变更
      const relevant = mutations.filter(m => {
        if (m.type === 'childList') return true;
        if (m.type === 'attributes') {
          const attr = m.attributeName;
          return ['style', 'class', 'id', 'role', 'aria-label', 'aria-hidden', 'aria-expanded', 'aria-selected', 'hidden', 'open', 'disabled'].includes(attr);
        }
        return false;
      });
      onMutations(relevant);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class', 'id', 'role', 'aria-label', 'aria-hidden', 'aria-expanded', 'aria-selected', 'hidden', 'open', 'disabled']
    });

    // 监听滚动（更新可见性）
    let scrollTimer = null;
    window.addEventListener('scroll', () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        onChange([{ type: 'scroll' }]);
      }, 100);
    }, { passive: true });

    // 监听 resize
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        onChange([{ type: 'resize' }]);
      }, 200);
    }, { passive: true });

    return observer;
  }

  function stopDOMObserver() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  global.AgentRuntime.observer = { startDOMObserver, stopDOMObserver };
})(window);

// ===== content/runtime.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  class AgentRuntime {
    constructor() {
      this.world = {
        version: '1.0.0',
        elements: new Map(),
        occupancy: null,
        topology: null,
        semanticIndex: null,
        meta: null,
        query: null
      };
      this.occupancyGrid = null;
      this.spatialQuery = null;
      this.updateCount = 0;
      // 事件驱动等待器:world_wait 由 MutationObserver 驱动,不再 server 端轮询
      this._waiters = [];
      this.changelog = {
        seq: 0,
        events: [],
        maxEvents: 2000
      };
      // 世界状态卡:显式暴露"现在是什么"(登录/弹窗/页面/表单)
      this.world.status = {
        dialogs: [],
        page: { state: 'stable', scrollY: 0, totalHeight: 0 },
        forms: [],
        changesSeq: 0
      };
    }

    /**
     * 刷新世界状态(增量维护,防抖后调用)
     */
    refreshStatus() {
      const elements = [...this.world.elements.values()];
      const byNode = new Map(elements.map(e => [e._el, e]));
      // 弹窗/对话框:直接 DOM 查询(预渲染隐藏弹窗会被可见性过滤掉,不依赖原生网页世界)
      const dialogs = [];
      const dialogNodes = document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]');
      for (const node of dialogNodes) {
        const rect = node.getBoundingClientRect();
        const st = getComputedStyle(node);
        if (rect.width < 3 || rect.height < 3 || st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
        if (global.AgentRuntime.visibility.isPseudoHidden(node, st, rect)) continue;
        const el = byNode.get(node);
        dialogs.push({
          id: el ? el.id : 'dom:' + node.tagName.toLowerCase(),
          name: el ? el.name : ((node.getAttribute('aria-label') || node.getAttribute('role') || node.tagName).toLowerCase())
        });
      }
      // 表单(有值的输入框,取前 10)
      const forms = [];
      for (const el of elements) {
        if (!el._el) continue;
        const tag = el._el.tagName;
        if ((tag === 'INPUT' || tag === 'TEXTAREA') && el._el.value) {
          forms.push({ id: el.id, name: el.name, value: String(el._el.value).slice(0, 50) });
          if (forms.length >= 10) break;
        }
      }
      this.world.status.dialogs = dialogs;
      this.world.status.forms = forms;
      // 稳定性:元素数连续两次刷新一致才算 stable(渐进渲染/分层加载下避免误报就绪)
      const curCount = elements.length;
      const prevCount = this._statusCount || 0;
      this._statusCount = curCount;
      const ready = document.readyState === 'complete';
      const stable = ready && (curCount === prevCount || Date.now() - (this.world.meta.initializedAt || 0) > 15000);
      this.world.status.page = {
        state: stable ? 'stable' : 'loading',
        scrollY: Math.round(window.scrollY),
        totalHeight: document.body.scrollHeight
      };
      this.world.status.changesSeq = this.changelog.seq;
    }

    /**
     * 记录变更事件（append-only，带游标）
     */
    logEvent(type, id, extra) {
      const cl = this.changelog;
      cl.seq++;
      const evt = { seq: cl.seq, t: Date.now(), type, id };
      if (extra) Object.assign(evt, extra);
      cl.events.push(evt);
      if (cl.events.length > cl.maxEvents) {
        cl.events.splice(0, cl.events.length - cl.maxEvents);
      }
      return evt;
    }

    /**
     * 读取自 sinceSeq 以来的变更（视频帧续读）
     */
    changes(sinceSeq = 0) {
      const cl = this.changelog;
      return {
        from: sinceSeq,
        to: cl.seq,
        events: cl.events.filter(e => e.seq > sinceSeq)
      };
    }

    /**
     * 最近 n 条变更日志
     */
    log(n = 50) {
      return this.changelog.events.slice(-n);
    }

    /**
     * 事件驱动等待(替代 server 端 0.3s 轮询):
     * 注册一个 waiter,MutationObserver 每次 flush(handleMutation)后检查条件,
     * 命中即 resolve(无需轮询);超时 setTimeout 兜底 resolve(false)。
     * filter 透传 findEntities({role,text,name,...});mode=appear/disappear。
     */
    waitFor(filter = {}, mode = 'appear', timeoutMs = 30000) {
      return new Promise((resolve) => {
        let settled = false;
        const finish = (result) => { if (!settled) { settled = true; resolve(result); } };
        const cleanup = () => {
          const i = this._waiters.indexOf(waiter);
          if (i >= 0) this._waiters.splice(i, 1);
          clearTimeout(waiter.timer);
        };
        const waiter = {
          filter, mode,
          timer: null,
          check: () => {
            try {
              const n = this.world.query.findEntities(filter).length;
              const ok = mode === 'appear' ? n > 0 : n === 0;
              if (ok) {
                cleanup();
                finish({ matched: true, mode, count: n });
                return true;
              }
            } catch (e) { /* query 未就绪等场景:继续等 */ }
            return false;
          }
        };
        waiter.timer = setTimeout(() => {
          cleanup();
          finish({ matched: false, mode, timeout_ms: timeoutMs });
        }, timeoutMs);
        this._waiters.push(waiter);
        waiter.check(); // 立即检查一次:条件已满足时立即返回
      });
    }

    /**
     * MutationObserver flush 后检查所有 waiter(事件驱动核心)
     */
    checkWaiters() {
      for (const w of this._waiters.slice()) w.check();
    }

    /**
     * 初始化：全量扫描 + 启动监听
     */
    init() {
      const startTime = performance.now();
      
      // 1. 全量扫描
      const elements = global.AgentRuntime.scanner.scanAll();
      elements.forEach(el => this.world.elements.set(el.id, el));
      
      // 2. 计算可见性
      global.AgentRuntime.visibility.updateAllVisibility(elements);
      
      // 3. 构建空间层
      this.rebuildSpatialLayers();
      
      // 4. 创建查询引擎
      this.spatialQuery = new global.AgentRuntime.SpatialQuery(this.world);
      this.world.query = this.spatialQuery;
      
      // 5. 记录 meta
      this.world.meta = {
        url: window.location.href,
        title: document.title,
        initializedAt: Date.now(),
        initTime: Math.round(performance.now() - startTime),
        elementCount: this.world.elements.size
      };
      
      // 名字去重（初始全量）
      global.AgentRuntime.semantics.dedupeNames(elements);
      
      // 记录初始事件
      this.logEvent('init', null, { count: this.world.elements.size });
      
      // 刷新世界状态卡
      this.refreshStatus();
      
      console.log(`[Agent Runtime] Initialized: ${this.world.elements.size} elements in ${this.world.meta.initTime}ms`);
      
      // 6. 启动 DOM 观察
      global.AgentRuntime.observer.startDOMObserver((mutations) => {
        this.handleMutation(mutations);
      });
    }

    /**
     * 处理 DOM 变化 — 增量更新
     */
    handleMutation(mutations) {
      this.updateCount++;
      const changedIds = new Set();
      const addedIds = new Set();
      const removedIds = new Set();
      const updatedIds = new Set();
      // 删除前捕获元数据:元素删除后 world.elements 查不到,remove 事件需要 name/semantic
      // 才能被 server 层翻译成人话摘要(变更可读化)
      const removedMeta = new Map();
      
      mutations.forEach(m => {
        if (m.type === 'scroll' || m.type === 'resize') {
          // 滚动/resize 只需更新可见性
          return;
        }
        
        // 处理新增节点
        if (m.addedNodes) {
          m.addedNodes.forEach(node => {
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const el = global.AgentRuntime.scanner.scanElement(node);
            if (el) {
              this.world.elements.set(el.id, el);
              changedIds.add(el.id);
              addedIds.add(el.id);
            }
            // 子节点也扫描
            if (node.querySelectorAll) {
              node.querySelectorAll('*').forEach(child => {
                const cel = global.AgentRuntime.scanner.scanElement(child);
                if (cel) {
                  this.world.elements.set(cel.id, cel);
                  changedIds.add(cel.id);
                  addedIds.add(cel.id);
                }
              });
            }
          });
        }
        
        // 处理属性变化
        if (m.type === 'attributes' && m.target.nodeType === Node.ELEMENT_NODE) {
          // 重新评估 target 及其所有后代:祖先 aria-hidden/隐藏样式变化会影响整棵子树,
          // 不遍历的话"先注册子元素、后给父容器加隐藏"的动态时序会泄露(IPI 防御闭环)
          const nodes = [m.target];
          if (m.target.querySelectorAll) {
            nodes.push(...m.target.querySelectorAll('*'));
          }
          for (const n of nodes) {
            const prevId = global.AgentRuntime.scanner.getStableId(n);
            const wasRegistered = this.world.elements.has(prevId);
            const el = global.AgentRuntime.scanner.scanElement(n);
            if (el) {
              this.world.elements.set(el.id, el);
              changedIds.add(el.id);
              updatedIds.add(el.id);
            } else if (wasRegistered) {
              // 元素被隐藏/变装饰(如动态加 aria-hidden/style/class),从世界移除
              // 避免"先注册后伪隐藏"的动态时序泄露(IPI 防御闭环)
              const prev = this.world.elements.get(prevId);
              if (prev) removedMeta.set(prevId, { name: prev.name, semantic: prev.semantic });
              this.world.elements.delete(prevId);
              changedIds.add(prevId);
              removedIds.add(prevId);
            }
          }
        }
        
        // 处理删除节点
        if (m.removedNodes) {
          m.removedNodes.forEach(node => {
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const id = global.AgentRuntime.scanner.getStableId(node);
            const prev = this.world.elements.get(id);
            if (prev) removedMeta.set(id, { name: prev.name, semantic: prev.semantic });
            this.world.elements.delete(id);
            changedIds.add(id);
            removedIds.add(id);
            if (node.querySelectorAll) {
              node.querySelectorAll('*').forEach(child => {
                const cid = global.AgentRuntime.scanner.getStableId(child);
                const cprev = this.world.elements.get(cid);
                if (cprev) removedMeta.set(cid, { name: cprev.name, semantic: cprev.semantic });
                this.world.elements.delete(cid);
                changedIds.add(cid);
                removedIds.add(cid);
              });
            }
          });
        }
      });
      
      // 记录变更日志（同一批次按 add > remove > update 优先级合并）
      addedIds.forEach(id => {
        const el = this.world.elements.get(id);
        this.logEvent('add', id, { name: el ? el.name : undefined, semantic: el ? el.semantic : undefined });
      });
      removedIds.forEach(id => {
        const meta = removedMeta.get(id);
        this.logEvent('remove', id, { name: meta ? meta.name : undefined, semantic: meta ? meta.semantic : undefined });
      });
      updatedIds.forEach(id => {
        const el = this.world.elements.get(id);
        this.logEvent('update', id, { name: el ? el.name : undefined, semantic: el ? el.semantic : undefined });
      });
      if (mutations.some(m => m.type === 'scroll' || m.type === 'resize')) {
        this.logEvent('visibility', null, { viewportY: Math.round(window.scrollY) });
      }
      
      // 更新可见性
      const allElements = [...this.world.elements.values()];
      global.AgentRuntime.visibility.updateAllVisibility(allElements);
      
      // 增量更新占位网格
      if (changedIds.size > 0 && this.occupancyGrid) {
        const changed = allElements.filter(e => changedIds.has(e.id));
        this.occupancyGrid.incrementalUpdate(changed);
      }
      
      // 如果变化较大，重建拓扑和语义
      if (changedIds.size > 5 || mutations.some(m => m.type === 'childList')) {
        this.rebuildSpatialLayers();
      }
      
      // 名字去重（保证弱 ID 唯一）
      global.AgentRuntime.semantics.dedupeNames(allElements);
      
      // 刷新世界状态卡
      this.refreshStatus();
      
      // 事件驱动等待器:每次 flush 后检查(命中即 resolve,替代轮询)
      this.checkWaiters();
      
      // 更新 meta
      this.world.meta.elementCount = this.world.elements.size;
      this.world.meta.lastUpdate = Date.now();
      this.world.meta.updateCount = this.updateCount;
    }

    /**
     * 重建空间层（拓扑 + 语义）
     */
    rebuildSpatialLayers() {
      const elements = [...this.world.elements.values()];
      
      // 占位网格
      if (!this.occupancyGrid) {
        this.occupancyGrid = new global.AgentRuntime.OccupancyGrid();
      }
      this.occupancyGrid.rebuild(elements);
      this.world.occupancy = this.occupancyGrid;
      
      // 拓扑
      this.world.topology = global.AgentRuntime.topology.buildTopology(elements);
      
      // 语义索引
      this.world.semanticIndex = global.AgentRuntime.semantics.buildSemanticIndex(elements);
      global.AgentRuntime.semantics.calculateAttentionWeights(elements);
    }

    /**
     * 强制全量刷新
     */
    forceRefresh() {
      this.world.elements.clear();
      this.init();
    }
  }

  global.AgentRuntime.AgentRuntime = AgentRuntime;
})(window);

// ===== content/overlay.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let currentMode = 'off';
  let overlayContainer = null;

  function createContainer() {
    if (overlayContainer) return overlayContainer;
    overlayContainer = document.createElement('div');
    overlayContainer.id = 'agent-runtime-overlay';
    overlayContainer.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:999999;';
    document.body.appendChild(overlayContainer);
    return overlayContainer;
  }

  function clearOverlay() {
    const container = document.getElementById('agent-runtime-overlay');
    if (container) container.innerHTML = '';
  }

  function toggleOverlay(mode, world) {
    clearOverlay();
    currentMode = mode;
    
    if (mode === 'off') return;
    
    const container = createContainer();
    const elements = [...world.elements.values()];
    
    switch(mode) {
      case 'grid': drawGrid(container); break;
      case 'elements': drawElements(container, elements); break;
      case 'regions': drawRegions(container, world); break;
      case 'all':
        drawGrid(container);
        drawElements(container, elements);
        drawRegions(container, world);
        break;
    }
  }

  function drawGrid(container) {
    const GRID = 40;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;';
    svg.setAttribute('width', window.innerWidth);
    svg.setAttribute('height', document.body.scrollHeight);
    
    for (let x = 0; x <= window.innerWidth; x += GRID) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', x); line.setAttribute('y1', 0);
      line.setAttribute('x2', x); line.setAttribute('y2', document.body.scrollHeight);
      line.setAttribute('stroke', 'rgba(125,211,252,0.1)'); line.setAttribute('stroke-width', '1');
      svg.appendChild(line);
    }
    for (let y = 0; y <= document.body.scrollHeight; y += GRID) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', 0); line.setAttribute('y1', y);
      line.setAttribute('x2', window.innerWidth); line.setAttribute('y2', y);
      line.setAttribute('stroke', 'rgba(125,211,252,0.1)'); line.setAttribute('stroke-width', '1');
      svg.appendChild(line);
    }
    container.appendChild(svg);
  }

  function drawElements(container, elements) {
    const colorMap = {
      navigation: '#7dd3fc', button: '#86efac', link: '#86efac',
      input: '#fbbf24', heading: '#c084fc', banner: '#7dd3fc',
      contentinfo: '#f87171', dialog: '#fb923c', card: '#a78bfa',
      content: '#64748b', img: '#f472b6'
    };
    
    elements.forEach(el => {
      if (!el.inViewport) return;
      const div = document.createElement('div');
      const color = colorMap[el.semantic] || '#64748b';
      div.style.cssText = `position:absolute;left:${el.bounds.x}px;top:${el.bounds.y}px;width:${el.bounds.w}px;height:${el.bounds.h}px;border:1px solid ${color};border-radius:2px;pointer-events:none;`;
      
      // 标签
      const label = document.createElement('span');
      label.style.cssText = `position:absolute;top:-14px;left:0;font-size:9px;font-family:monospace;color:${color};background:rgba(0,0,0,0.7);padding:1px 4px;border-radius:2px;white-space:nowrap;`;
      label.textContent = el.semantic;
      div.appendChild(label);
      
      container.appendChild(div);
    });
  }

  function drawRegions(container, world) {
    if (!world.query) return;
    const regions = world.query.findEmptyRegions(10);
    
    regions.forEach(region => {
      const GRID = 40;
      const div = document.createElement('div');
      div.style.cssText = `position:absolute;left:${region.bounds.gx1*GRID}px;top:${region.bounds.gy1*GRID}px;width:${(region.bounds.gx2-region.bounds.gx1+1)*GRID}px;height:${(region.bounds.gy2-region.bounds.gy1+1)*GRID}px;border:2px dashed rgba(251,191,36,0.4);background:rgba(251,191,36,0.05);border-radius:4px;pointer-events:none;`;
      
      const label = document.createElement('span');
      label.style.cssText = 'position:absolute;top:4px;left:4px;font-size:10px;font-family:monospace;color:#fbbf24;opacity:0.7;';
      label.textContent = `${region.cells}格`;
      div.appendChild(label);
      
      container.appendChild(div);
    });
  }

  global.AgentRuntime.overlay = { toggleOverlay, clearOverlay };
})(window);

// ===== api/agent-world.js =====
window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 挂载 window.agentWorld — Agent 的统一入口
   */
  function mountAgentWorld(runtime) {
    const query = runtime.world.query;
    
    global.agentWorld = {
      version: '1.0.0',
      
      // 页面元信息
      meta: runtime.world.meta,
      
      // 查询 API
      query: {
        findByRole: (role) => query.findByRole(role),
        findByTag: (tag) => query.findByTag(tag),
        findInteractive: () => query.findInteractive(),
        findInViewport: () => query.findInViewport(),
        getElement: (id) => query.getElement(id),
        nearby: (id, radius) => query.nearby(id, radius),
        getNeighbors: (id) => query.getNeighbors(id),
        findEmptyRegions: () => query.findEmptyRegions(),
        navigationPath: () => query.navigationPath(),
        describe: () => query.describe(),
        getPageSummary: () => query.getPageSummary(),
        getSnapshot: () => query.getSnapshot(),
        findEntities: (filter) => query.findEntities(filter),
        getEntity: (id) => query.getEntity(id),
        layers: () => query.layers(),
        resolve: (q) => query.resolve(q),
        getStatus: () => query.getStatus()
      },
      
      // 变更日志（视频流）
      changes: (sinceSeq) => runtime.changes(sinceSeq),
      log: (n) => runtime.log(n),
      
      // 可视化
      toggleOverlay: (mode) => global.AgentRuntime.overlay.toggleOverlay(mode, runtime.world),
      
      // 刷新
      refresh: () => runtime.forceRefresh(),
      
      // 内部状态
      _runtime: runtime
    };
    
    console.log('[Agent Runtime] window.agentWorld ready');
  }

  global.AgentRuntime.mountAgentWorld = mountAgentWorld;
})(window);

// ===== content/bootstrap.js =====
(function() {
  'use strict';
  
  // 等待 DOM 就绪
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
  
  function bootstrap() {
    try {
      const runtime = new window.AgentRuntime.AgentRuntime();
      runtime.init();
      window.AgentRuntime.mountAgentWorld(runtime);
      
      console.log('[Agent Runtime] ✅ World ready — try window.agentWorld.query.describe()');
    } catch(e) {
      console.error('[Agent Runtime] ❌ Bootstrap failed:', e);
    }
  }
})();

