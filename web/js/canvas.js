/**
 * 图纸画布：只绘制可见区域（大图也不会超出浏览器 canvas 尺寸限制），支持缩放、平移、触摸双指缩放。
 *
 * 三种视图：
 *   bead  拼豆效果（圆形带孔的豆子）
 *   chart 图纸（方格 + 色号）
 *   image 原图（对照用）
 *
 * 施工模式（build 不为空）：当前颜色正常显示并加红圈，已完成的颜色淡显，其余颜色几乎隐藏。
 *
 * 与业务的交互通过构造参数里的回调完成：onHover / onLeave / onCellDown / onCellEnter / onCellUp。
 */
class PatternCanvas {
  constructor(canvas, wrap, callbacks = {}) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.ctx = canvas.getContext('2d');
    this.cb = callbacks;

    this.grid = null;          // grid[行][列] = 色号 | null
    this.colors = new Map();   // 色号 -> { rgb, fill, hole, text }
    this.image = null;         // 原图 HTMLImageElement
    this.view = 'bead';
    this.tool = 'pan';
    this.showGrid = true;
    this.showBoards = true;
    this.boardSize = 29;
    this.highlight = null;     // 高亮的色号
    this.build = null;         // 施工模式：{ current: 当前色号, done: Set<已完成色号> }
    this.insetTop = 0;         // 顶部被浮层（施工栏）占用的高度，适应窗口时让出这部分

    this.scale = 16;           // 每格 CSS 像素
    this.ox = 0;               // 网格左上角相对画布的偏移（CSS 像素）
    this.oy = 0;
    this.minScale = 1;
    this.maxScale = 80;
    this._fitted = true;       // 当前是否处于「适应窗口」状态（窗口尺寸变化时自动重新适应）
    this._size = null;         // 上次画布尺寸

    this._pointers = new Map();
    this._pan = null;
    this._painting = false;
    this._lastCell = null;
    this._frame = 0;

    new ResizeObserver(() => this._resize()).observe(wrap);
    this._bindEvents();
  }

  // ------------------------------------------------------------ 数据

  /** 设置网格和颜色表；keepView=true 时保留当前缩放位置（编辑、重新生成同尺寸时用） */
  setData(grid, colorMap, keepView = false) {
    const sizeChanged = !this.grid || !grid || this.grid.length !== grid.length || this.grid[0].length !== grid[0].length;
    this.grid = grid;
    this.colors = new Map();
    for (const [code, c] of colorMap) {
      const [r, g, b] = c.rgb;
      this.colors.set(code, {
        rgb: c.rgb,
        fill: `rgb(${r},${g},${b})`,
        hole: `rgb(${Math.round(r * 0.72)},${Math.round(g * 0.72)},${Math.round(b * 0.72)})`,
        text: luminance(c.rgb) < 0.35 ? '#fff' : '#222',
      });
    }
    if (!keepView || sizeChanged) this.fit();
    else this.render();
  }

  setImage(img) { this.image = img; this.render(); }
  get cols() { return this.grid ? this.grid[0].length : 0; }
  get rows() { return this.grid ? this.grid.length : 0; }

  // ------------------------------------------------------------ 视图变换

  fit() {
    if (!this.grid) return this.render();
    const { width, height } = this.wrap.getBoundingClientRect();
    const pad = 24;
    const top = this.insetTop;
    const s = Math.min((width - pad * 2) / this.cols, (height - top - pad * 2) / this.rows);
    this.scale = clamp(s, this.minScale, this.maxScale);
    this.ox = (width - this.cols * this.scale) / 2;
    this.oy = top + (height - top - this.rows * this.scale) / 2;
    this._fitted = true;
    this.render();
  }

  /** 以画布上 (cx, cy) 为中心缩放 */
  zoomBy(factor, cx, cy) {
    if (!this.grid) return;
    const rect = this.wrap.getBoundingClientRect();
    cx = cx ?? rect.width / 2;
    cy = cy ?? rect.height / 2;
    const next = clamp(this.scale * factor, this.minScale, this.maxScale);
    const k = next / this.scale;
    this.ox = cx - (cx - this.ox) * k;
    this.oy = cy - (cy - this.oy) * k;
    this.scale = next;
    this._fitted = false;
    this.render();
    this.cb.onZoom?.(this.scale);
  }

  /** 屏幕坐标 -> 格子 {r, c}，不在网格内返回 null */
  cellAt(clientX, clientY) {
    if (!this.grid) return null;
    const rect = this.canvas.getBoundingClientRect();
    const c = Math.floor((clientX - rect.left - this.ox) / this.scale);
    const r = Math.floor((clientY - rect.top - this.oy) / this.scale);
    return r >= 0 && c >= 0 && r < this.rows && c < this.cols ? { r, c } : null;
  }

  // ------------------------------------------------------------ 绘制

  render() {
    if (this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = 0;
      this._draw();
    });
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const { width, height } = this.wrap.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.round(width * dpr));
    this.canvas.height = Math.max(1, Math.round(height * dpr));
    const prev = this._size;
    this._size = { width, height };
    if (this._fitted) {
      this.fit();
      this.cb.onZoom?.(this.scale);
    } else if (prev) { // 保持画面中心不变
      this.ox += (width - prev.width) / 2;
      this.oy += (height - prev.height) / 2;
    }
    this.render();
  }

  _draw() {
    const { ctx } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.width / dpr;
    const h = this.canvas.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!this.grid) return;

    const s = this.scale;
    const gw = this.cols * s;
    const gh = this.rows * s;
    // 可见格子范围
    const c0 = Math.max(0, Math.floor(-this.ox / s));
    const c1 = Math.min(this.cols, Math.ceil((w - this.ox) / s));
    const r0 = Math.max(0, Math.floor(-this.oy / s));
    const r1 = Math.min(this.rows, Math.ceil((h - this.oy) / s));

    // 底板
    ctx.fillStyle = this.view === 'bead' ? '#F8F7F4' : '#FFFFFF';
    ctx.shadowColor = 'rgba(0,0,0,.12)';
    ctx.shadowBlur = 12;
    ctx.fillRect(this.ox, this.oy, gw, gh);
    ctx.shadowBlur = 0;

    if (this.view === 'image') this._drawImage(gw, gh);
    else this._drawCells(r0, r1, c0, c1);

    this._drawLines(r0, r1, c0, c1, gw, gh);
  }

  _drawImage(gw, gh) {
    if (!this.image) return;
    this.ctx.imageSmoothingEnabled = true;
    this.ctx.drawImage(this.image, this.ox, this.oy, gw, gh);
  }

  _drawCells(r0, r1, c0, c1) {
    const { ctx } = this;
    const s = this.scale;
    const bead = this.view === 'bead';
    const asCircle = bead && s >= 4;
    const showText = this.view === 'chart' && s >= 16;
    if (showText) {
      ctx.font = `600 ${Math.max(8, Math.round(s * 0.34))}px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
    }

    for (let r = r0; r < r1; r++) {
      const row = this.grid[r];
      const y = this.oy + r * s;
      for (let c = c0; c < c1; c++) {
        const code = row[c];
        const x = this.ox + c * s;
        if (code == null) {
          if (bead && s >= 8) { // 空位画出豆板的小钉子
            ctx.fillStyle = '#DEDAD2';
            ctx.beginPath();
            ctx.arc(x + s / 2, y + s / 2, s * 0.09, 0, Math.PI * 2);
            ctx.fill();
          }
          continue;
        }
        const color = this.colors.get(code);
        if (!color) continue;
        ctx.globalAlpha = this._alpha(code);
        if (asCircle) {
          ctx.fillStyle = color.fill;
          ctx.beginPath();
          ctx.arc(x + s / 2, y + s / 2, s * 0.46, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = color.hole;
          ctx.beginPath();
          ctx.arc(x + s / 2, y + s / 2, s * 0.16, 0, Math.PI * 2);
          ctx.fill();
        } else {
          ctx.fillStyle = color.fill;
          ctx.fillRect(x, y, s + 0.5, s + 0.5); // +0.5 避免缩放时出现细缝
          if (showText) {
            ctx.fillStyle = color.text;
            ctx.fillText(code, x + s / 2, y + s / 2 + 0.5, s - 2);
          }
        }
      }
    }
    ctx.globalAlpha = 1;
    if (this.build && s >= 5) this._ringCurrent(r0, r1, c0, c1);
  }

  /** 格子透明度：施工模式 > 高亮 > 正常 */
  _alpha(code) {
    if (this.build) {
      if (code === this.build.current) return 1;
      return this.build.done.has(code) ? 0.3 : 0.07;
    }
    return this.highlight && this.highlight !== code ? 0.12 : 1;
  }

  /** 施工模式：给当前颜色的格子描红圈，散落的单颗豆也容易找到 */
  _ringCurrent(r0, r1, c0, c1) {
    const { ctx } = this;
    const s = this.scale;
    ctx.beginPath();
    ctx.strokeStyle = '#F2545B';
    ctx.lineWidth = Math.max(1.5, s * 0.09);
    for (let r = r0; r < r1; r++) {
      for (let c = c0; c < c1; c++) {
        if (this.grid[r][c] !== this.build.current) continue;
        const x = this.ox + c * s + s / 2;
        const y = this.oy + r * s + s / 2;
        ctx.moveTo(x + s * 0.5, y);
        ctx.arc(x, y, s * 0.5, 0, Math.PI * 2);
      }
    }
    ctx.stroke();
  }

  _drawLines(r0, r1, c0, c1, gw, gh) {
    const { ctx } = this;
    const s = this.scale;
    const line = (x1, y1, x2, y2) => { ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); };
    const snap = (v) => Math.round(v) + 0.5;
    const top = Math.max(this.oy, 0) - 1;
    const bottom = Math.min(this.oy + gh, this.canvas.height) + 1;
    const left = Math.max(this.ox, 0) - 1;
    const right = Math.min(this.ox + gw, this.canvas.width) + 1;

    if (this.showGrid) {
      // 细线：每格（拼豆视图不画，格子太小时也不画）
      if (this.view !== 'bead' && s >= 6) {
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(0,0,0,.12)';
        ctx.lineWidth = 1;
        for (let c = c0; c <= c1; c++) line(snap(this.ox + c * s), top, snap(this.ox + c * s), bottom);
        for (let r = r0; r <= r1; r++) line(left, snap(this.oy + r * s), right, snap(this.oy + r * s));
        ctx.stroke();
      }
      // 粗线：每 10 格
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(0,0,0,.4)';
      ctx.lineWidth = 1;
      for (let c = Math.ceil(c0 / 10) * 10; c <= c1; c += 10) line(snap(this.ox + c * s), top, snap(this.ox + c * s), bottom);
      for (let r = Math.ceil(r0 / 10) * 10; r <= r1; r += 10) line(left, snap(this.oy + r * s), right, snap(this.oy + r * s));
      ctx.stroke();
    }

    // 分板线
    if (this.showBoards && this.boardSize > 0) {
      ctx.beginPath();
      ctx.strokeStyle = '#F2545B';
      ctx.lineWidth = 2;
      for (let c = this.boardSize; c < this.cols; c += this.boardSize) line(this.ox + c * s, top, this.ox + c * s, bottom);
      for (let r = this.boardSize; r < this.rows; r += this.boardSize) line(left, this.oy + r * s, right, this.oy + r * s);
      ctx.stroke();
    }

    // 外框
    ctx.strokeStyle = 'rgba(0,0,0,.35)';
    ctx.lineWidth = 1;
    ctx.strokeRect(snap(this.ox), snap(this.oy), Math.round(gw), Math.round(gh));
  }

  // ------------------------------------------------------------ 交互

  _bindEvents() {
    const el = this.canvas;

    el.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      // 触控板双指缩放会带 ctrlKey，灵敏度更高一些
      const k = e.ctrlKey ? 0.01 : 0.0015;
      this.zoomBy(Math.exp(-e.deltaY * k), e.clientX - rect.left, e.clientY - rect.top);
    }, { passive: false });

    el.addEventListener('pointerdown', (e) => {
      el.setPointerCapture(e.pointerId);
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (this._pointers.size === 2) { // 双指：切换为缩放平移
        this._endPaint();
        this._startPinch();
        return;
      }
      const panMode = this.tool === 'pan' || e.button === 1 || e.button === 2 || this.view === 'image';
      if (panMode) {
        this._pan = { x: e.clientX, y: e.clientY, ox: this.ox, oy: this.oy };
        this.wrap.classList.add('panning');
        return;
      }
      const cell = this.cellAt(e.clientX, e.clientY);
      if (cell) {
        this._painting = true;
        this._lastCell = cell;
        this.cb.onCellDown?.(cell);
      }
    });

    el.addEventListener('pointermove', (e) => {
      if (this._pointers.has(e.pointerId)) this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (this._pinch && this._pointers.size === 2) return this._movePinch();
      if (this._pan) {
        this.ox = this._pan.ox + e.clientX - this._pan.x;
        this.oy = this._pan.oy + e.clientY - this._pan.y;
        this._fitted = false;
        this.render();
        return;
      }
      const cell = this.cellAt(e.clientX, e.clientY);
      if (this._painting && cell && (cell.r !== this._lastCell?.r || cell.c !== this._lastCell?.c)) {
        // 快速拖动时补齐中间跳过的格子
        for (const p of lineCells(this._lastCell, cell)) this.cb.onCellEnter?.(p);
        this._lastCell = cell;
      }
      if (cell) this.cb.onHover?.(cell, e);
      else this.cb.onLeave?.();
    });

    const end = (e) => {
      this._pointers.delete(e.pointerId);
      if (this._pointers.size < 2) this._pinch = null;
      if (this._pointers.size === 0) {
        this._pan = null;
        this.wrap.classList.remove('panning');
        this._endPaint();
      }
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('pointerleave', () => this.cb.onLeave?.());
    el.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  _endPaint() {
    if (this._painting) {
      this._painting = false;
      this._lastCell = null;
      this.cb.onCellUp?.();
    }
  }

  _startPinch() {
    const [a, b] = [...this._pointers.values()];
    this._pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
    this._pan = null;
  }

  _movePinch() {
    const [a, b] = [...this._pointers.values()];
    const dist = Math.hypot(a.x - b.x, a.y - b.y);
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    const rect = this.canvas.getBoundingClientRect();
    this.ox += mid.x - this._pinch.mid.x;
    this.oy += mid.y - this._pinch.mid.y;
    this.zoomBy(dist / this._pinch.dist, mid.x - rect.left, mid.y - rect.top);
    this._pinch = { dist, mid };
  }
}

// ------------------------------------------------------------ 工具函数

function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }

/** 相对亮度 0~1，用来决定色块上的文字颜色 */
function luminance([r, g, b]) {
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** 两格之间直线经过的格子（不含起点），用于拖动画笔时补点 */
function lineCells(from, to) {
  const cells = [];
  const steps = Math.max(Math.abs(to.r - from.r), Math.abs(to.c - from.c));
  for (let i = 1; i <= steps; i++) {
    cells.push({ r: Math.round(from.r + ((to.r - from.r) * i) / steps), c: Math.round(from.c + ((to.c - from.c) * i) / steps) });
  }
  return cells;
}
