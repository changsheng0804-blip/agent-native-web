/**
 * query.js - Query API
 * Agent 查询接口
 */

(function(global) {
  'use strict';

  /**
   * 创建查询 API
   */
  function createQueryAPI(worldState) {
    const { elements, occupancyGrid, semanticTree, topology, regions } = worldState;
    const elementMap = new Map(elements.map(e => [e.id, e]));
    
    const queryAPI = {
      /**
       * 按语义角色查找
       */
      findByRole(role) {
        return semanticTree.bySemantic[role] || [];
      },
      
      /**
       * 按标签查找
       */
      findByTag(tag) {
        return elements.filter(e => e.tag === tag.toLowerCase()).map(e => e.id);
      },
      
      /**
       * 所有可交互元素
       */
      findInteractive() {
        return elements.filter(e => e.interactive).map(e => e.id);
      },
      
      /**
       * 所有可见元素
       */
      findVisible() {
        return elements.filter(e => e.visible).map(e => e.id);
      },
      
      /**
       * 获取元素详情
       */
      getElement(id) {
        return elementMap.get(id) || null;
      },
      
      /**
       * 获取邻近元素
       */
      getNeighbors(elementId) {
        return topology.neighbors[elementId] || null;
      },
      
      /**
       * 获取元素所在区域
       */
      getRegionOf(elementId) {
        const el = elementMap.get(elementId);
        if (!el) return null;
        
        // 找到包含该元素的区域
        for (const region of regions.regions) {
          const { x, y, width, height } = region.grid;
          const elGrid = el.grid;
          
          // 检查元素中心是否在区域内
          const elCenterX = elGrid.gx + Math.floor(elGrid.gw / 2);
          const elCenterY = elGrid.gy + Math.floor(elGrid.gh / 2);
          
          if (elCenterX >= x && elCenterX < x + width &&
              elCenterY >= y && elCenterY < y + height) {
            return region;
          }
        }
        
        return null;
      },
      
      /**
       * 所有空白区域
       */
      findEmptyRegions() {
        return regions.regions;
      },
      
      /**
       * 建议新元素放置位置
       */
      suggestPlacement(type) {
        const typeScores = {
          'widget': { shape: 'square', minArea: 4, maxArea: 16 },
          'banner': { shape: 'wide', minArea: 10, maxArea: 50 },
          'card': { shape: 'vertical', minArea: 6, maxArea: 25 },
          'button': { shape: 'any', minArea: 1, maxArea: 4 },
          'input': { shape: 'horizontal', minArea: 2, maxArea: 8 },
          'text': { shape: 'any', minArea: 2, maxArea: 20 },
          'image': { shape: 'square', minArea: 4, maxArea: 30 },
          'icon': { shape: 'square', minArea: 1, maxArea: 2 }
        };
        
        const criteria = typeScores[type] || { shape: 'any', minArea: 2, maxArea: 20 };
        
        // 筛选合适的区域
        const suitableRegions = regions.regions.filter(r => {
          const area = r.grid.width * r.grid.height;
          if (area < criteria.minArea || area > criteria.maxArea) return false;
          
          if (criteria.shape === 'wide') {
            return r.grid.width > r.grid.height;
          } else if (criteria.shape === 'vertical') {
            return r.grid.height > r.grid.width;
          } else if (criteria.shape === 'square') {
            return Math.abs(r.grid.width - r.grid.height) < 3;
          }
          
          return true;
        });
        
        // 按分数排序
        suitableRegions.sort((a, b) => b.usage.score - a.usage.score);
        
        return suitableRegions.slice(0, 5).map(r => ({
          regionId: r.id,
          pixelPosition: { x: r.pixel.x, y: r.pixel.y },
          gridPosition: { x: r.grid.x, y: r.grid.y },
          maxSize: { width: r.pixel.width, height: r.pixel.height },
          score: r.usage.score,
          recommendedTypes: r.usage.recommendedTypes
        }));
      },
      
      /**
       * 两元素间的空间路径
       */
      findPath(fromId, toId) {
        return topology.findPathBetween(fromId, toId, topology.navigationPath, elements);
      },
      
      /**
       * 自然语言布局描述
       */
      describeLayout() {
        if (global.AgentRuntime && global.AgentRuntime.semantic && global.AgentRuntime.semantic.generateLayoutDescription) {
          return global.AgentRuntime.semantic.generateLayoutDescription(semanticTree);
        }
        // fallback: 生成简单描述
        const totalElements = elements.length;
        const interactiveCount = elements.filter(e => e.interactive).length;
        const regionNames = Object.keys(semanticTree.bySemantic || {});
        return `页面包含 ${totalElements} 个元素，${interactiveCount} 个可交互，${regions.regionCount || 0} 个空白区域。语义类型: ${regionNames.join(', ')}`;
      },
      
      /**
       * 密度热图
       */
      getDensityMap() {
        if (global.AgentRuntime && global.AgentRuntime.regions && global.AgentRuntime.regions.generateDensityMap) {
          return global.AgentRuntime.regions.generateDensityMap(occupancyGrid);
        }
        // fallback
        return { regionCount: regions.regionCount || 0, regions: regions.regions || [] };
      },
      
      /**
       * 页面整体摘要
       */
      getPageSummary() {
        const interactiveElements = elements.filter(e => e.interactive);
        const visibleElements = elements.filter(e => e.visible);
        const byRole = semanticTree.bySemantic;
        
        // 识别主要导航
        const navigation = byRole.navigation || byRole.menu || [];
        const headers = elements.filter(e => 
          e.semantic === 'header' || e.semantic === 'banner' || 
          (e.tag === 'header' && e.bounds.y < 100)
        );
        const footers = elements.filter(e => 
          e.semantic === 'footer' || e.semantic === 'contentinfo' ||
          (e.tag === 'footer' && e.bounds.y > window.innerHeight - 150)
        );
        
        // 识别 CTA
        const ctas = byRole.cta || [];
        const buttons = byRole.button || [];
        
        return {
          statistics: {
            totalElements: elements.length,
            interactiveElements: interactiveElements.length,
            visibleElements: visibleElements.length,
            emptyRegions: regions.regionCount,
            semanticTypes: Object.keys(byRole).length
          },
          structure: {
            hasNavigation: navigation.length > 0,
            hasHeader: headers.length > 0,
            hasFooter: footers.length > 0,
            ctaCount: ctas.length + buttons.length
          },
          density: (function() {
            try {
              if (global.AgentRuntime && global.AgentRuntime.regions && global.AgentRuntime.regions.generateDensityMap) {
                return global.AgentRuntime.regions.generateDensityMap(occupancyGrid).healthScore;
              }
            } catch(e) {}
            return Math.round((1 - (regions.regionCount || 0) / Math.max(elements.length, 1)) * 100);
          })(),
          topSemantics: Object.entries(byRole)
            .sort((a, b) => b[1].length - a[1].length)
            .slice(0, 10)
            .map(([role, ids]) => ({ role, count: ids.length })),
          topAttention: semanticTree.sortedByAttention.slice(0, 5).map(e => ({
            id: e.id,
            text: e.text,
            semantic: e.semantic,
            weight: e.attentionWeight
          }))
        };
      },
      
      /**
       * 完整世界快照
       */
      getSnapshot() {
        return {
          timestamp: Date.now(),
          url: window.location.href,
          viewport: { width: window.innerWidth, height: window.innerHeight },
          elements: elements.map(e => ({
            id: e.id,
            tag: e.tag,
            role: e.role,
            semantic: e.semantic,
            bounds: e.bounds,
            grid: e.grid,
            interactive: e.interactive,
            visible: e.visible,
            depth: e.depth,
            attentionWeight: e.attentionWeight
          })),
          occupancyGrid: {
            width: occupancyGrid.width,
            height: occupancyGrid.height,
            gridSize: occupancyGrid.gridSize,
            totalCells: occupancyGrid.width * occupancyGrid.height
          },
          topology: {
            neighborCount: Object.keys(topology.neighbors).length,
            navigationPathRows: topology.navigationPath.length
          },
          regions: {
            count: regions.regionCount,
            totalEmptyCells: regions.totalEmptyCells
          },
          summary: queryAPI.getPageSummary()
        };
      },
      
      /**
       * 搜索元素
       */
      search(criteria) {
        let results = [...elements];
        
        if (criteria.role) {
          results = results.filter(e => e.role === criteria.role || e.semantic === criteria.role);
        }
        
        if (criteria.tag) {
          results = results.filter(e => e.tag === criteria.tag.toLowerCase());
        }
        
        if (criteria.interactive !== undefined) {
          results = results.filter(e => e.interactive === criteria.interactive);
        }
        
        if (criteria.visible !== undefined) {
          results = results.filter(e => e.visible === criteria.visible);
        }
        
        if (criteria.text) {
          const searchText = criteria.text.toLowerCase();
          results = results.filter(e => 
            e.text && e.text.toLowerCase().includes(searchText)
          );
        }
        
        if (criteria.semantic) {
          results = results.filter(e => e.semantic === criteria.semantic);
        }
        
        // 限制返回数量
        return results.slice(0, criteria.limit || 50).map(e => e.id);
      }
    };
    
    return queryAPI;
  }

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.query = {
    createQueryAPI
  };

})(window);
