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
        placeholder: el.getAttribute('placeholder')
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
