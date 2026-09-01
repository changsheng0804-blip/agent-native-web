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
