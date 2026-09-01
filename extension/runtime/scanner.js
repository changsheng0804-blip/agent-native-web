/**
 * scanner.js - DOM Scanner
 * 扫描页面所有可见元素，提取结构信息
 */

(function(global) {
  'use strict';

  // 配置
  const CONFIG = {
    GRID_SIZE: 40,
    MIN_ELEMENT_SIZE: 5,
    TEXT_TRUNCATE: 100,
    SKIP_TAGS: ['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'CANVAS', 'HEAD', 'META', 'TITLE', 'LINK'],
    DECORATIVE_TAGS: ['BR', 'HR', 'SPAN'],
    INTERACTIVE_SELECTORS: 'a, button, input, select, textarea, [onclick], [onmouseover], [onfocus], [tabindex], [contenteditable="true"]'
  };

  /**
   * 生成稳定的元素ID
   */
  function generateElementId(element, index) {
    if (element.id) {
      return element.id;
    }
    const tag = element.tagName.toLowerCase();
    const parentId = element.parentElement ? generateElementId(element.parentElement, 0) : 'root';
    return `${parentId}_${tag}_${index}`;
  }

  /**
   * 检查元素是否可见
   */
  function isVisible(element) {
    const style = getComputedStyle(element);
    
    // 检查 CSS 属性
    if (style.display === 'none') return false;
    if (style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity) === 0) return false;
    
    // 检查尺寸
    const rect = element.getBoundingClientRect();
    if (rect.width < CONFIG.MIN_ELEMENT_SIZE || rect.height < CONFIG.MIN_ELEMENT_SIZE) return false;
    
    // 检查是否有内容
    if (element.tagName === 'BR' || element.tagName === 'HR') return true;
    
    return true;
  }

  /**
   * 检查元素是否可交互
   */
  function isInteractive(element) {
    const tag = element.tagName.toUpperCase();
    const interactiveTags = ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA'];
    
    if (interactiveTags.includes(tag)) return true;
    
    // 检查属性
    if (element.hasAttribute('onclick')) return true;
    if (element.hasAttribute('onmouseover')) return true;
    if (element.hasAttribute('onfocus')) return true;
    if (element.hasAttribute('tabindex')) return true;
    if (element.getAttribute('contenteditable') === 'true') return true;
    
    // 检查样式
    const style = getComputedStyle(element);
    if (style.cursor === 'pointer') return true;
    
    // 检查 role
    const role = element.getAttribute('role');
    if (role && ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'textbox', 'slider'].includes(role.toLowerCase())) {
      return true;
    }
    
    return false;
  }

  /**
   * 检查元素是否应该被跳过（装饰性元素）
   */
  function isDecorative(element) {
    const tag = element.tagName.toUpperCase();
    
    // 纯装饰标签
    if (tag === 'BR') return true;
    if (tag === 'HR') return true;
    
    // 空 span
    if (tag === 'SPAN' && !element.textContent.trim()) return true;
    
    // 没有文本且没有子元素可交互的空容器
    if (!element.textContent.trim() && !element.querySelector(CONFIG.INTERACTIVE_SELECTORS)) {
      // 检查是否有背景色或边框
      const style = getComputedStyle(element);
      const hasBg = style.backgroundColor !== 'rgba(0, 0, 0, 0)' && style.backgroundColor !== 'transparent';
      const hasBorder = style.borderWidth !== '0px';
      if (!hasBg && !hasBorder) return true;
    }
    
    return false;
  }

  /**
   * 推断元素的 ARIA role
   */
  function inferRole(element) {
    // 1. 直接的 role 属性
    const roleAttr = element.getAttribute('role');
    if (roleAttr) return roleAttr.toLowerCase();
    
    // 2. 语义化标签
    const tag = element.tagName.toUpperCase();
    const semanticMap = {
      'NAV': 'navigation',
      'HEADER': 'banner',
      'FOOTER': 'contentinfo',
      'MAIN': 'main',
      'ASIDE': 'complementary',
      'ARTICLE': 'article',
      'SECTION': 'region',
      'FORM': 'form',
      'BUTTON': 'button',
      'A': 'link',
      'INPUT': 'textbox',
      'SELECT': 'listbox',
      'TEXTAREA': 'textbox',
      'H1': 'heading',
      'H2': 'heading',
      'H3': 'heading',
      'H4': 'heading',
      'H5': 'heading',
      'H6': 'heading',
      'IMG': 'img',
      'VIDEO': 'video',
      'TABLE': 'table',
      'UL': 'list',
      'OL': 'list',
      'LI': 'listitem',
      'DIALOG': 'dialog',
      'MENU': 'menu',
      'MENUBAR': 'menubar',
      'MENUITEM': 'menuitem'
    };
    
    if (semanticMap[tag]) return semanticMap[tag];
    
    // 3. 特殊标签
    if (tag === 'A' && element.href) return 'link';
    if (tag === 'IMG' && element.alt) return 'img';
    
    return null;
  }

  /**
   * 从 class/id 推断语义类型
   */
  function inferSemanticFromAttributes(element) {
    const cn = element.className;
    const clsName = (typeof cn === 'string' ? cn : (cn && cn.baseVal) || '');
    const classId = (clsName + ' ' + element.id).toLowerCase();
    
    const patterns = {
      'nav': 'navigation',
      'menu': 'menu',
      'navbar': 'navigation',
      'header': 'header',
      'footer': 'footer',
      'sidebar': 'sidebar',
      'aside': 'sidebar',
      'btn': 'button',
      'button': 'button',
      'card': 'card',
      'hero': 'hero',
      'banner': 'banner',
      'modal': 'modal',
      'dialog': 'dialog',
      'tooltip': 'tooltip',
      'dropdown': 'dropdown',
      'select': 'select',
      'tab': 'tab',
      'tabs': 'tab-container',
      'form': 'form',
      'input': 'input',
      'search': 'search',
      'logo': 'logo',
      'icon': 'icon',
      'avatar': 'avatar',
      'badge': 'badge',
      'tag': 'tag',
      'link': 'link',
      'title': 'title',
      'heading': 'heading',
      'text': 'text',
      'paragraph': 'paragraph',
      'image': 'image',
      'img': 'image',
      'video': 'video',
      'list': 'list',
      'item': 'item',
      'row': 'row',
      'column': 'column',
      'container': 'container',
      'wrapper': 'wrapper',
      'section': 'section',
      'content': 'content',
      'overlay': 'overlay',
      'mask': 'mask',
      'backdrop': 'backdrop',
      'progress': 'progress',
      'slider': 'slider',
      'switch': 'switch',
      'checkbox': 'checkbox',
      'radio': 'radio'
    };
    
    for (const [keyword, semantic] of Object.entries(patterns)) {
      if (classId.includes(keyword)) {
        return semantic;
      }
    }
    
    return null;
  }

  /**
   * 获取元素的直接文本内容（不含子元素）
   */
  function getDirectText(element) {
    let text = '';
    for (const node of element.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent;
      }
    }
    return text.trim().slice(0, CONFIG.TEXT_TRUNCATE);
  }

  /**
   * 获取元素的所有文本内容
   */
  function getFullText(element) {
    return element.textContent.trim().slice(0, CONFIG.TEXT_TRUNCATE);
  }

  /**
   * 像素坐标转网格坐标
   */
  function pixelToGrid(x, y, w, h) {
    const gs = CONFIG.GRID_SIZE;
    return {
      gx: Math.floor(x / gs),
      gy: Math.floor(y / gs),
      gw: Math.ceil(w / gs),
      gh: Math.ceil(h / gs)
    };
  }

  /**
   * 计算 DOM 深度
   */
  function getDepth(element) {
    let depth = 0;
    let parent = element.parentElement;
    while (parent) {
      depth++;
      parent = parent.parentElement;
    }
    return depth;
  }

  /**
   * 主扫描函数
   */
  function scanDOM() {
    const startTime = performance.now();
    const elements = [];
    const elementMap = new Map();
    let index = 0;
    
    // 获取视口尺寸
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    
    // 遍历所有元素
    const allElements = document.querySelectorAll('*');
    
    for (const el of allElements) {
      const tag = el.tagName.toUpperCase();
      
      // 跳过脚本和样式等
      if (CONFIG.SKIP_TAGS.includes(tag)) continue;
      
      // 检查可见性
      if (!isVisible(el)) continue;
      
      // 检查装饰性
      if (isDecorative(el)) continue;
      
      // 获取边界
      const rect = el.getBoundingClientRect();
      const bounds = {
        x: rect.left,
        y: rect.top,
        w: rect.width,
        h: rect.height
      };
      
      // 过滤太小的元素
      if (bounds.w < CONFIG.MIN_ELEMENT_SIZE || bounds.h < CONFIG.MIN_ELEMENT_SIZE) continue;
      
      // 生成 ID
      const id = generateElementId(el, index);
      
      // 获取父元素 ID
      let parentId = null;
      if (el.parentElement && !CONFIG.SKIP_TAGS.includes(el.parentElement.tagName.toUpperCase())) {
        parentId = elementMap.get(el.parentElement) || null;
      }
      
      // 构建元素数据
      const elementData = {
        id,
        tag: tag.toLowerCase(),
        role: inferRole(el),
        text: getFullText(el),
        directText: getDirectText(el),
        bounds,
        grid: pixelToGrid(bounds.x, bounds.y, bounds.w, bounds.h),
        interactive: isInteractive(el),
        visible: bounds.x < viewportWidth && bounds.y < viewportHeight,
        depth: getDepth(el),
        parent: parentId,
        children: [],
        semantic: inferSemanticFromAttributes(el),
        computedStyle: {
          display: getComputedStyle(el).display,
          position: getComputedStyle(el).position,
          zIndex: getComputedStyle(el).zIndex,
          cursor: getComputedStyle(el).cursor
        }
      };
      
      elements.push(elementData);
      elementMap.set(el, id);
      index++;
    }
    
    // 构建父子关系
    for (const el of elements) {
      if (el.parent) {
        const parentEl = elements.find(e => e.id === el.parent);
        if (parentEl) {
          parentEl.children.push(el.id);
        }
      }
    }
    
    const endTime = performance.now();
    
    return {
      elements,
      elementMap,
      count: elements.length,
      scanTime: endTime - startTime,
      viewport: { width: viewportWidth, height: viewportHeight },
      gridSize: CONFIG.GRID_SIZE
    };
  }

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.scanner = {
    scanDOM,
    CONFIG
  };

})(window);
