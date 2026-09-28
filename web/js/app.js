/**
 * 页面主逻辑：状态管理、参数表单、调用转换接口、手动编辑（画笔/橡皮/吸管/撤销）、保存导出、历史图纸。
 *
 * 数据流：上传图片 / 修改参数 → convert() 调后端 → applyGrid() 更新 state.grid → 画布与右侧面板刷新。
 * 手动编辑直接修改 state.grid（按「笔画」记录撤销栈），保存时把整个网格提交给后端。
 * 翻转 / 旋转记录在 state.orientation 中，重新生成后自动套用，方向不会丢。
 * 施工模式（state.build）按颜色逐个拼，已完成的色号记在 state.done，保存后的图纸会把进度存到后端。
 */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  /** 与后端 ConvertParams 默认值保持一致 */
  const DEFAULT_PARAMS = {
    palette_id: 'mard_221', fit_mode: 'board', board_size: 29, width: 52, height: null, crop_to_subject: true, max_colors: 24,
    resample: 'dominant', line_priority: false, dither: 'none', dither_strength: 0.6,
    remove_background: false, bg_method: 'color', bg_model: 'isnet-general-use', bg_tolerance: 12, min_region_size: 3,
    outline: false, outline_code: null,
    saturation: 1, contrast: 1, brightness: 1, excluded_codes: [],
  };
  const MAX_UPLOAD_MB = 15;
  const SPARE_RATIO = 0.1;

  const state = {
    palettes: [],              // 色卡概要列表
    paletteCache: new Map(),   // 色卡 ID -> 详情
    palette: null,             // 当前色卡详情
    colorMap: new Map(),       // 色号 -> { code, name, hex, rgb }
    file: null,                // 上传的图片 File
    image: null,               // 上传的图片 HTMLImageElement（原图视图用）
    params: { ...DEFAULT_PARAMS },
    grid: null,                // 当前网格 grid[行][列] = 色号 | null
    warnings: [],
    patternId: null,           // 已保存图纸 ID，未保存为 null
    dirty: false,              // 是否有未保存的修改
    tool: 'pan',
    paintCode: null,           // 画笔颜色
    highlight: null,           // 高亮的色号
    undo: [], redo: [], stroke: null,  // 撤销栈条目：{ type: 'cells', changes } 或 { type: 'transform', op }
    orientation: { flip: false, rot: 0 },  // 当前方向：先水平翻转（flip），再顺时针旋转 rot × 90°
    done: new Set(),           // 施工模式：已拼完的色号
    build: { active: false, order: [], index: 0 },  // 施工模式：颜色顺序（按用量从多到少）与当前位置
    convertCtrl: null,         // 进行中的转换请求（新请求会取消旧请求）
    models: new Map(),         // AI 抠图模型 ID -> 状态（见后端 SegModelStatus）
  };
  const BG_METHOD_HINTS = {
    color: '适合纯色、渐变背景的插画和截图。照片或主体颜色接近背景时建议用 AI 抠图。',
    ai: '适合照片（人像、宠物、物品）。首次使用需要下载模型，之后离线可用。',
  };

  // ============================================================ 画布

  const wrap = $('#canvasWrap');
  const tooltip = $('#tooltip');
  const canvas = new PatternCanvas($('#canvas'), wrap, {
    onHover: showTooltip,
    onLeave: () => { tooltip.hidden = true; },
    onCellDown: handleCellDown,
    onCellEnter: (cell) => { if (state.stroke) paintCell(cell); },
    onCellUp: finishStroke,
    onZoom: updateZoomLabel,
  });

  function showTooltip(cell, e) {
    const code = state.grid?.[cell.r][cell.c];
    const color = code && state.colorMap.get(code);
    const name = color && color.name !== code ? ` ${escapeHtml(color.name)}` : '';
    tooltip.innerHTML = `第 ${cell.r + 1} 行 · 第 ${cell.c + 1} 列`
      + (color ? `<br><i style="background:${color.hex}"></i>${escapeHtml(code)}${name}` : '<br>空位');
    const rect = wrap.getBoundingClientRect();
    let x = e.clientX - rect.left + 14;
    let y = e.clientY - rect.top + 14;
    tooltip.hidden = false;
    if (x + tooltip.offsetWidth > rect.width) x -= tooltip.offsetWidth + 28;
    if (y + tooltip.offsetHeight > rect.height) y -= tooltip.offsetHeight + 28;
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
  }

  function updateZoomLabel() {
    $('#zoomLabel').textContent = `${Math.round((canvas.scale / 16) * 100)}%`;
  }

  // ============================================================ 色卡

  async function loadPalettes(selectId) {
    state.palettes = await Api.listPalettes();
    const select = $('#paletteSelect');
    // 内置色卡按品牌分组（保持后端给出的顺序），自定义色卡单独一组
    const groups = new Map();
    for (const p of state.palettes) {
      const label = p.source === 'custom' ? '自定义色卡' : p.brand;
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(p);
    }
    select.innerHTML = [...groups].map(([label, items]) => `<optgroup label="${escapeHtml(label)}">${items.map((p) =>
      `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}（${p.color_count} 色 · ${p.bead_size_mm}mm）</option>`).join('')}</optgroup>`).join('');
    const target = selectId && state.palettes.some((p) => p.id === selectId) ? selectId : state.params.palette_id;
    select.value = state.palettes.some((p) => p.id === target) ? target : state.palettes[0].id;
    await usePalette(select.value);
  }

  async function getPaletteDetail(id) {
    if (!state.paletteCache.has(id)) state.paletteCache.set(id, await Api.getPalette(id));
    return state.paletteCache.get(id);
  }

  /** 切换当前色卡（只更新状态，不触发转换） */
  async function usePalette(id) {
    const palette = await getPaletteDetail(id);
    state.palette = palette;
    state.params.palette_id = id;
    state.colorMap = new Map(palette.colors.map((c) => [c.code, { ...c, rgb: hexToRgb(c.hex) }]));
    $('#paletteSelect').value = id;
    $('#pitchInput').value = palette.bead_size_mm;
    // 排除列表只保留当前色卡中存在的色号
    state.params.excluded_codes = state.params.excluded_codes.filter((c) => state.colorMap.has(c));
    if (state.paintCode && !state.colorMap.has(state.paintCode)) state.paintCode = null;
    // 描边颜色下拉框
    const outline = $('#outlineSelect');
    outline.innerHTML = '<option value="">自动（最深色）</option>'
      + palette.colors.map((c) => `<option value="${escapeHtml(c.code)}">${escapeHtml(colorLabel(c))}</option>`).join('');
    outline.value = state.params.outline_code && state.colorMap.has(state.params.outline_code) ? state.params.outline_code : '';
    updateExcludeCount();
    updatePaintChip();
  }

  $('#paletteSelect').addEventListener('change', async (e) => {
    const id = e.target.value;
    if (!state.file && state.grid) {
      // 从历史打开、没有原图时无法重新生成，色号也不能跨色卡换算
      e.target.value = state.palette.id;
      toast('需要先上传原图，才能换用其他色卡重新生成', 'error');
      return;
    }
    await usePalette(id);
    scheduleConvert(0);
  });

  // ============================================================ 参数表单

  /** 从表单控件读取参数值 */
  function readControl(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'range' || el.type === 'number') return Number(el.value);
    if (el.dataset.param === 'outline_code') return el.value || null;
    return el.value;
  }

  function updateOutputs() {
    $('#widthOut').textContent = $('#widthRange').value;
    const board = state.params.board_size;
    const byBoard = state.params.fit_mode === 'board';
    $$('#fitModeSwitch button').forEach((b) => b.classList.toggle('active', b.dataset.fit === state.params.fit_mode));
    $('#widthFields').hidden = byBoard;
    $('#fitHint').textContent = byBoard
      ? `主体等比缩放后居中放在一块 ${board}×${board} 豆板上`
      : '按宽度生成，图纸上的红线为分板线';
    $('#maxColorsOut').textContent = Number($('#maxColorsRange').value) === 0 ? '不限' : $('#maxColorsRange').value;
    $('#saturationOut').textContent = Number($('#saturationRange').value).toFixed(2);
    $('#contrastOut').textContent = Number($('#contrastRange').value).toFixed(2);
    $('#brightnessOut').textContent = Number($('#brightnessRange').value).toFixed(2);
    $('#bgTolOut').textContent = $('#bgTolRange').value;
    const minRegion = Number($('#minRegionRange').value);
    const dithering = state.params.dither !== 'none';
    $('#minRegionRange').disabled = dithering;
    $('#minRegionOut').textContent = dithering ? '抖动时不合并'
      : minRegion <= 1 ? '关闭' : minRegion === 2 ? '只清理单颗' : `少于 ${minRegion} 颗`;
    $('#ditherOut').textContent = Number($('#ditherRange').value).toFixed(2);
    $('#bgOptions').hidden = !state.params.remove_background;
    $('#bgTolField').hidden = state.params.bg_method !== 'color';
    $('#bgModelField').hidden = state.params.bg_method !== 'ai';
    $('#bgMethodHint').textContent = BG_METHOD_HINTS[state.params.bg_method];
    $$('#bgMethodSwitch button').forEach((b) => b.classList.toggle('active', b.dataset.method === state.params.bg_method));
    updateModelStatus();
    $('#ditherStrengthField').hidden = state.params.dither === 'none';
    $('#linePriorityField').hidden = state.params.resample !== 'dominant';
    $('#outlineSelect').disabled = !state.params.outline;
  }

  /** 把 state.params 写回表单（打开历史图纸时用） */
  function writeParamsToForm() {
    for (const el of $$('[data-param]')) {
      const value = state.params[el.dataset.param];
      if (el.type === 'checkbox') el.checked = Boolean(value);
      else el.value = value ?? '';
    }
    const presetBoard = ['29', '52', '14'].includes(String(state.params.board_size));
    $('#boardSelect').value = presetBoard ? String(state.params.board_size) : 'custom';
    $('#boardCustom').hidden = presetBoard;
    $('#boardCustom').value = state.params.board_size;
    canvas.boardSize = state.params.board_size;
    $('#customHeight').checked = state.params.height != null;
    $('#heightInput').disabled = state.params.height == null;
    if (state.params.height != null) $('#heightInput').value = state.params.height;
    updateOutputs();
    updateExcludeCount();
  }

  for (const el of $$('[data-param]')) {
    const eventName = el.type === 'range' ? 'input' : 'change';
    el.addEventListener(eventName, () => {
      state.params[el.dataset.param] = readControl(el);
      updateOutputs();
      // 滑块拖动过程中频繁变化，多等一会再请求
      scheduleConvert(el.type === 'range' ? 350 : 0);
    });
  }

  $('#customHeight').addEventListener('change', (e) => {
    $('#heightInput').disabled = !e.target.checked;
    state.params.height = e.target.checked ? clampInt($('#heightInput').value, 4, 200) : null;
    scheduleConvert(0);
  });
  $('#heightInput').addEventListener('change', (e) => {
    state.params.height = clampInt(e.target.value, 4, 200);
    e.target.value = state.params.height;
    scheduleConvert(0);
  });

  $('#fitModeSwitch').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-fit]');
    if (!btn || btn.dataset.fit === state.params.fit_mode) return;
    state.params.fit_mode = btn.dataset.fit;
    updateOutputs();
    scheduleConvert(0);
  });

  $('#boardSelect').addEventListener('change', (e) => {
    const custom = e.target.value === 'custom';
    $('#boardCustom').hidden = !custom;
    if (custom) $('#boardCustom').focus();
    setBoardSize(custom ? $('#boardCustom').value : e.target.value);
  });
  $('#boardCustom').addEventListener('change', (e) => setBoardSize(e.target.value));

  /** 修改豆板边长：影响分板线、统计、导出；适配豆板模式下还要重新生成 */
  function setBoardSize(value) {
    const size = clampInt(value, 5, 200);
    $('#boardCustom').value = size;
    if (size === state.params.board_size) return;
    state.params.board_size = size;
    canvas.boardSize = size;
    canvas.render();
    updateOutputs();
    updateStats();
    if (state.params.fit_mode === 'board') scheduleConvert(0);
  }
  $('#pitchInput').addEventListener('change', updateStats);

  // ============================================================ 图片上传

  const dropzone = $('#dropzone');
  $('#fileInput').addEventListener('change', (e) => e.target.files[0] && loadFile(e.target.files[0]));
  for (const target of [dropzone, wrap]) {
    target.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
    target.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    target.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      const file = [...e.dataTransfer.files].find((f) => f.type.startsWith('image/'));
      if (file) loadFile(file);
    });
  }
  document.addEventListener('paste', (e) => {
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (item) loadFile(item.getAsFile());
  });

  function loadFile(file) {
    if (!file.type.startsWith('image/')) return toast('请选择图片文件', 'error');
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return toast(`图片不能超过 ${MAX_UPLOAD_MB}MB`, 'error');
    if (!confirmDiscard()) return;

    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      state.file = file;
      state.image = img;
      state.patternId = null;
      state.orientation = { flip: false, rot: 0 };
      state.done.clear();
      if (state.build.active) exitBuild();
      canvas.setImage(img);
      const preview = $('#sourcePreview');
      preview.src = url;
      preview.hidden = false;
      $('#fileName').textContent = `${file.name || '粘贴的图片'} · ${img.naturalWidth}×${img.naturalHeight}`;
      $('#patternName').value = (file.name || '拼豆图纸').replace(/\.[^.]+$/, '');
      $('#emptyState').hidden = true;
      updateViewButtons();
      scheduleConvert(0);
    };
    img.onerror = () => toast('图片无法读取', 'error');
    img.src = url;
  }

  // ============================================================ 转换

  let convertTimer = 0;
  function scheduleConvert(delay) {
    if (!state.file) return;
    if (aiModelMissing()) return; // 模型下载完成后会自动重新生成
    clearTimeout(convertTimer);
    convertTimer = setTimeout(convert, delay);
  }

  async function convert() {
    if (!state.file) return;
    state.convertCtrl?.abort();
    const ctrl = new AbortController();
    state.convertCtrl = ctrl;
    $('#loading').hidden = false;
    try {
      const result = await Api.convert(state.file, state.params, ctrl.signal);
      state.warnings = result.warnings;
      state.undo = [];
      state.redo = [];
      state.done.clear();  // 重新生成后颜色分布变了，进度作废
      if (state.build.active) exitBuild();
      applyGrid(orientGrid(result.grid, state.orientation), true);
      markDirty(true);
    } catch (err) {
      if (err.name !== 'AbortError') toast(err.message, 'error');
    } finally {
      if (state.convertCtrl === ctrl) {
        state.convertCtrl = null;
        $('#loading').hidden = true;
      }
    }
  }

  /** 设置当前网格并刷新所有相关界面 */
  function applyGrid(grid, keepView = false) {
    state.grid = grid;
    if (state.highlight && !grid.some((row) => row.includes(state.highlight))) state.highlight = null;
    canvas.highlight = state.highlight;
    canvas.setData(grid, state.colorMap, keepView);
    $('#emptyState').hidden = true;
    updateZoomLabel();
    refreshResult();
  }

  // ============================================================ 去背景方式与 AI 模型

  $('#bgMethodSwitch').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-method]');
    if (!btn || btn.dataset.method === state.params.bg_method) return;
    state.params.bg_method = btn.dataset.method;
    updateOutputs();
    if (aiModelMissing()) toast('AI 抠图需要先下载模型，点击「下载」即可');
    scheduleConvert(0);
  });

  /** 当前选择了 AI 抠图、但模型还不可用 */
  function aiModelMissing() {
    const p = state.params;
    return p.remove_background && p.bg_method === 'ai' && state.models.get(p.bg_model)?.status !== 'ready';
  }

  async function loadModels() {
    try {
      for (const m of await Api.listModels()) state.models.set(m.id, m);
    } catch (err) {
      toast(`加载 AI 模型列表失败：${err.message}`, 'error');
      return;
    }
    renderModelOptions();
    updateModelStatus();
    for (const m of state.models.values()) if (m.status === 'downloading') pollModel(m.id);
  }

  function renderModelOptions() {
    const select = $('#bgModelSelect');
    select.innerHTML = [...state.models.values()].map((m) =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}${m.status === 'ready' ? '（已下载）' : `（${formatSize(m.size_mb)}）`}</option>`).join('');
    select.value = state.params.bg_model;
  }

  function updateModelStatus() {
    const m = state.models.get(state.params.bg_model);
    const box = $('#modelStatus');
    const btn = $('#modelDownloadBtn');
    if (!m) return;
    box.dataset.status = m.status;
    $('#modelDesc').textContent = m.description;
    const size = formatSize(m.size_mb);
    const text = {
      ready: '✓ 已就绪',
      not_downloaded: `未下载（约 ${size}）`,
      downloading: `下载中 ${Math.round(m.progress * 100)}%（约 ${size}）`,
      failed: `下载失败：${m.error || '未知错误'}`,
    }[m.status];
    $('#modelStatusText').textContent = text;
    $('#modelStatusText').title = text;
    btn.hidden = m.status === 'ready' || m.status === 'downloading';
    btn.textContent = m.status === 'failed' ? '重试' : '下载';
    $('#modelProgress').hidden = m.status !== 'downloading';
    $('#modelProgressBar').style.width = `${Math.round(m.progress * 100)}%`;
  }

  $('#modelDownloadBtn').addEventListener('click', async () => {
    const id = state.params.bg_model;
    try {
      state.models.set(id, await Api.downloadModel(id));
      updateModelStatus();
      pollModel(id);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  /** 下载中每秒刷新一次进度，完成后自动重新生成 */
  function pollModel(id) {
    const timer = setInterval(async () => {
      try {
        const m = await Api.getModel(id);
        state.models.set(id, m);
        if (id === state.params.bg_model) updateModelStatus();
        if (m.status === 'downloading') return;
        clearInterval(timer);
        renderModelOptions();
        if (m.status === 'ready') {
          toast(`模型「${m.name}」下载完成`);
          scheduleConvert(0);
        } else {
          toast(`模型下载失败：${m.error}`, 'error');
        }
      } catch (err) {
        clearInterval(timer);
        toast(err.message, 'error');
      }
    }, 1000);
  }

  // ============================================================ 右侧：统计、用量清单

  /** 前端统计用量（编辑后实时更新，不必请求后端） */
  function computeBom() {
    const counts = new Map();
    for (const row of state.grid || []) {
      for (const code of row) if (code != null) counts.set(code, (counts.get(code) || 0) + 1);
    }
    const order = new Map(state.palette.colors.map((c, i) => [c.code, i]));
    return [...counts].map(([code, count]) => ({ ...state.colorMap.get(code), count }))
      .sort((a, b) => b.count - a.count || order.get(a.code) - order.get(b.code));
  }

  function refreshResult() {
    const bom = computeBom();
    updateStats(bom);
    renderBom(bom);
    renderWarnings();
    const hasGrid = Boolean(state.grid);
    $('#saveBtn').disabled = !hasGrid;
    $$('[data-export], [data-transform]').forEach((b) => { b.disabled = !hasGrid; });
    updateUndoButtons();
    updateBuildButton(bom);
  }

  function updateStats(bom = computeBom()) {
    if (!state.grid) return;
    const rows = state.grid.length;
    const cols = state.grid[0].length;
    const beads = bom.reduce((sum, item) => sum + item.count, 0);
    const board = state.params.board_size;
    const boards = Math.ceil(cols / board) * Math.ceil(rows / board);
    const pitch = Number($('#pitchInput').value) || state.palette.bead_size_mm;
    $('#statSize').textContent = `${cols}×${rows}`;
    $('#statBeads').textContent = beads.toLocaleString();
    $('#statColors').textContent = bom.length;
    $('#statBoards').textContent = boards;
    $('#statPhysical').textContent =
      `成品约 ${(cols * pitch / 10).toFixed(1)} × ${(rows * pitch / 10).toFixed(1)} cm（按 ${pitch}mm 豆距）`;
  }

  function renderWarnings() {
    $('#warnings').innerHTML = state.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
  }

  function renderBom(bom) {
    const box = $('#bom');
    if (!bom.length) {
      box.innerHTML = '<p class="hint">没有需要放豆的格子</p>';
      return;
    }
    const current = state.build.active ? state.build.order[state.build.index] : null;
    box.innerHTML = bom.map((item) => `
      <button type="button" class="bom-row ${state.highlight === item.code ? 'active' : ''} ${state.done.has(item.code) ? 'done' : ''} ${current === item.code ? 'current' : ''}" data-code="${escapeHtml(item.code)}"
        title="建议购买 ${Math.ceil(item.count * (1 + SPARE_RATIO))} 颗（含 10% 损耗）">
        <span class="bom-swatch" style="background:${item.hex}"></span>
        <span class="bom-name"><b>${escapeHtml(item.code)}</b>${item.name !== item.code ? `<span>${escapeHtml(item.name)}</span>` : ''}</span>
        <span class="bom-count">${item.count}<small>颗</small></span>
      </button>`).join('');
  }

  $('#bom').addEventListener('click', (e) => {
    const row = e.target.closest('.bom-row');
    if (!row) return;
    const code = row.dataset.code;
    if (state.build.active) {
      state.build.index = Math.max(0, state.build.order.indexOf(code));
      updateBuild();
      return;
    }
    if (state.tool === 'brush') {
      state.paintCode = code;
      updatePaintChip();
      toast(`画笔颜色：${code}`);
      return;
    }
    state.highlight = state.highlight === code ? null : code;
    state.paintCode = code;
    updatePaintChip();
    canvas.highlight = state.highlight;
    canvas.render();
    renderBom(computeBom());
  });

  // ============================================================ 视图与工具

  $('#viewSwitch').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-view]');
    if (!btn || btn.disabled) return;
    $$('#viewSwitch button').forEach((b) => b.classList.toggle('active', b === btn));
    canvas.view = btn.dataset.view;
    canvas.render();
  });

  function updateViewButtons() {
    const imageBtn = $('#viewSwitch [data-view="image"]');
    imageBtn.disabled = !state.image;
    imageBtn.title = state.image ? '' : '从历史打开的图纸没有原图';
    if (!state.image && canvas.view === 'image') $('#viewSwitch [data-view="chart"]').click();
  }

  function setTool(tool) {
    state.tool = tool;
    canvas.tool = tool;
    wrap.dataset.tool = tool;
    $$('#toolSwitch [data-tool]').forEach((b) => b.classList.toggle('active', b.dataset.tool === tool));
  }
  $('#toolSwitch').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-tool]');
    if (!btn) return;
    if (state.build.active && btn.dataset.tool !== 'pan') return toast('施工模式下不能编辑，请先退出', 'error');
    setTool(btn.dataset.tool);
  });
  $('#paintChip').addEventListener('click', openPaintPicker);

  $('#gridToggle').addEventListener('change', (e) => { canvas.showGrid = e.target.checked; canvas.render(); });
  $('#boardToggle').addEventListener('change', (e) => { canvas.showBoards = e.target.checked; canvas.render(); });
  $('#zoomIn').addEventListener('click', () => canvas.zoomBy(1.25));
  $('#zoomOut').addEventListener('click', () => canvas.zoomBy(0.8));
  $('#zoomFit').addEventListener('click', () => { canvas.fit(); updateZoomLabel(); });

  function updatePaintChip() {
    const chip = $('#paintChip');
    const color = state.paintCode && state.colorMap.get(state.paintCode);
    chip.style.background = color ? color.hex : '';
    chip.title = color ? `画笔颜色：${colorLabel(color)}（点击更换）` : '选择画笔颜色';
  }

  // ============================================================ 手动编辑

  function handleCellDown(cell) {
    if (!state.grid) return;
    if (state.tool === 'picker') {
      const code = state.grid[cell.r][cell.c];
      if (code) {
        state.paintCode = code;
        updatePaintChip();
        setTool('brush');
        toast(`已吸取 ${code}，切换到画笔`);
      }
      return;
    }
    if (state.tool === 'brush' && !state.paintCode) {
      toast('请先选择画笔颜色（点击用量清单或工具栏的色块）', 'error');
      return;
    }
    state.stroke = [];
    paintCell(cell);
  }

  function paintCell({ r, c }) {
    const value = state.tool === 'eraser' ? null : state.paintCode;
    const old = state.grid[r][c];
    if (old === value) return;
    state.stroke.push({ r, c, old, value });
    state.grid[r][c] = value;
    canvas.render();
  }

  function finishStroke() {
    const stroke = state.stroke;
    state.stroke = null;
    if (!stroke?.length) return;
    state.undo.push({ type: 'cells', changes: stroke });
    state.redo = [];
    markDirty(true);
    refreshResult();
  }

  function undo() {
    const entry = state.undo.pop();
    if (!entry) return;
    if (entry.type === 'transform') {
      applyTransform(INVERSE_TRANSFORM[entry.op], false);
    } else {
      for (let i = entry.changes.length - 1; i >= 0; i--) {
        const { r, c, old } = entry.changes[i];
        state.grid[r][c] = old;
      }
    }
    state.redo.push(entry);
    markDirty(true);
    canvas.render();
    refreshResult();
  }

  function redo() {
    const entry = state.redo.pop();
    if (!entry) return;
    if (entry.type === 'transform') applyTransform(entry.op, false);
    else for (const { r, c, value } of entry.changes) state.grid[r][c] = value;
    state.undo.push(entry);
    markDirty(true);
    canvas.render();
    refreshResult();
  }

  function updateUndoButtons() {
    $('#undoBtn').disabled = !state.undo.length;
    $('#redoBtn').disabled = !state.redo.length;
  }
  $('#undoBtn').addEventListener('click', undo);
  $('#redoBtn').addEventListener('click', redo);

  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select, dialog[open]')) return;
    const mod = e.metaKey || e.ctrlKey;
    const key = e.key.toLowerCase();
    if (state.build.active && !mod) {
      const actions = { enter: completeCurrent, ' ': completeCurrent, arrowright: () => stepBuild(1),
        arrowleft: () => stepBuild(-1), escape: exitBuild };
      if (actions[key]) { e.preventDefault(); return actions[key](); }
    }
    if (mod && key === 'z') { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
    if (mod && key === 'y') { e.preventDefault(); return redo(); }
    if (mod) return;
    const tools = { h: 'pan', v: 'pan', b: 'brush', e: 'eraser', i: 'picker' };
    if (tools[key] && !state.build.active) setTool(tools[key]);
    else if (key === 'm') applyTransform('flipH');
    else if (key === 'r') applyTransform('rotCW');
    else if (key === '=' || key === '+') canvas.zoomBy(1.25);
    else if (key === '-') canvas.zoomBy(0.8);
    else if (key === '0') { canvas.fit(); updateZoomLabel(); }
  });

  // ============================================================ 翻转与旋转

  /** 网格变换（都返回新数组，不修改原网格） */
  const TRANSFORMS = {
    flipH: (g) => g.map((row) => [...row].reverse()),
    flipV: (g) => [...g].reverse().map((row) => [...row]),
    rotCW: (g) => g[0].map((_, c) => g.map((row) => row[c]).reverse()),
    rotCCW: (g) => g[0].map((_, c) => g.map((row) => row[row.length - 1 - c])),
  };
  const INVERSE_TRANSFORM = { flipH: 'flipH', flipV: 'flipV', rotCW: 'rotCCW', rotCCW: 'rotCW' };

  /**
   * 方向合成：方向记为「先水平翻转 flip，再顺时针旋转 rot 次」。
   * 利用二面体群关系 翻转∘旋转 = 旋转⁻¹∘翻转、垂直翻转 = 旋转180°∘水平翻转 推导得出。
   */
  function composeOrientation({ flip, rot }, op) {
    if (op === 'rotCW') return { flip, rot: (rot + 1) % 4 };
    if (op === 'rotCCW') return { flip, rot: (rot + 3) % 4 };
    if (op === 'flipH') return { flip: !flip, rot: (4 - rot) % 4 };
    return { flip: !flip, rot: (6 - rot) % 4 }; // flipV
  }

  /** 把方向套用到新生成的网格上（重新生成后保持用户选择的翻转 / 旋转） */
  function orientGrid(grid, { flip, rot }) {
    let g = flip ? TRANSFORMS.flipH(grid) : grid;
    for (let i = 0; i < rot; i++) g = TRANSFORMS.rotCW(g);
    return g;
  }

  function applyTransform(op, record = true) {
    if (!state.grid) return;
    state.grid = TRANSFORMS[op](state.grid);
    state.orientation = composeOrientation(state.orientation, op);
    if (record) {
      state.undo.push({ type: 'transform', op });
      state.redo = [];
    }
    canvas.setData(state.grid, state.colorMap, true); // 尺寸变化（非正方形旋转）时会自动适应窗口
    updateZoomLabel();
    markDirty(true);
    refreshResult();
  }
  $$('[data-transform]').forEach((btn) => btn.addEventListener('click', () => applyTransform(btn.dataset.transform)));

  // ============================================================ 逐色施工模式

  function updateBuildButton(bom = computeBom()) {
    const btn = $('#buildBtn');
    btn.disabled = !state.grid || !bom.length;
    const done = bom.filter((c) => state.done.has(c.code)).length;
    btn.textContent = state.build.active ? '退出施工' : done ? `继续拼豆（${done}/${bom.length}）` : '开始拼豆';
  }
  $('#buildBtn').addEventListener('click', () => (state.build.active ? exitBuild() : enterBuild()));

  function enterBuild() {
    const order = computeBom().map((c) => c.code);
    if (!order.length) return;
    const firstTodo = order.findIndex((c) => !state.done.has(c));
    state.build = { active: true, order, index: Math.max(0, firstTodo) };
    setTool('pan');
    state.highlight = null;
    canvas.highlight = null;
    wrap.classList.add('building');
    $('#buildBar').hidden = false;
    updateBuild();
    canvas.insetTop = $('#buildBar').offsetHeight + 16; // 图纸让出施工栏的位置，不被遮挡
    canvas.fit();
    updateZoomLabel();
  }

  function exitBuild() {
    state.build.active = false;
    canvas.build = null;
    wrap.classList.remove('building');
    $('#buildBar').hidden = true;
    canvas.insetTop = 0;
    canvas.fit();
    updateZoomLabel();
    refreshResult();
  }

  /** 刷新施工栏：当前颜色、第几色、按颗数计算的整体进度 */
  function updateBuild() {
    const b = state.build;
    const bom = computeBom();
    const code = b.order[b.index];
    const item = bom.find((c) => c.code === code);
    if (!item) return exitBuild();
    const totalBeads = bom.reduce((sum, c) => sum + c.count, 0);
    const doneItems = bom.filter((c) => state.done.has(c.code));
    const doneBeads = doneItems.reduce((sum, c) => sum + c.count, 0);

    canvas.build = { current: code, done: state.done };
    canvas.render();
    $('#buildSwatch').style.background = item.hex;
    $('#buildCode').textContent = code;
    $('#buildName').textContent = item.name !== code ? item.name : '';
    $('#buildCount').textContent = `${item.count} 颗`;
    $('#buildSub').textContent = `第 ${b.index + 1}/${b.order.length} 色 · 已完成 ${doneItems.length} 色、${doneBeads}/${totalBeads} 颗`;
    $('#buildProgress').style.width = `${(doneBeads / totalBeads) * 100}%`;
    $('#buildDone').textContent = state.done.has(code) ? '↺ 标记未完成' : '✓ 完成此色';
    renderBom(bom);
    updateBuildButton(bom);
  }

  /** 完成（或取消完成）当前颜色，完成后自动跳到下一个未完成的颜色 */
  function completeCurrent() {
    const b = state.build;
    const code = b.order[b.index];
    if (state.done.has(code)) {
      state.done.delete(code);
    } else {
      state.done.add(code);
      const later = b.order.findIndex((c, i) => i > b.index && !state.done.has(c));
      const next = later >= 0 ? later : b.order.findIndex((c) => !state.done.has(c));
      if (next >= 0) b.index = next;
      else toast('🎉 全部颜色都拼完了！');
    }
    saveProgress();
    updateBuild();
  }

  function stepBuild(delta) {
    const b = state.build;
    b.index = (b.index + delta + b.order.length) % b.order.length;
    updateBuild();
  }

  $('#buildDone').addEventListener('click', completeCurrent);
  $('#buildPrev').addEventListener('click', () => stepBuild(-1));
  $('#buildNext').addEventListener('click', () => stepBuild(1));
  $('#buildExit').addEventListener('click', exitBuild);

  /** 进度保存到后端（已保存的图纸才有 ID），连续点击时合并成一次请求 */
  let progressTimer = 0;
  let progressHintShown = false;
  function saveProgress() {
    if (!state.patternId) {
      if (!progressHintShown) toast('保存图纸后，拼豆进度也会一起记住');
      progressHintShown = true;
      return;
    }
    clearTimeout(progressTimer);
    progressTimer = setTimeout(async () => {
      try {
        await Api.updatePattern(state.patternId, { done_codes: [...state.done] });
        loadHistory();
      } catch (err) {
        toast(`进度保存失败：${err.message}`, 'error');
      }
    }, 400);
  }

  // ============================================================ 保存

  function markDirty(dirty) {
    state.dirty = dirty;
    const saved = state.patternId != null;
    $('#saveBtn').textContent = saved ? '保存修改' : '保存';
    $('#saveAsBtn').hidden = !saved;
    $('#saveHint').textContent = !state.grid ? ''
      : saved ? (dirty ? `图纸 #${state.patternId} 有未保存的修改` : `已保存为图纸 #${state.patternId}`)
        : '尚未保存';
  }

  async function save(asNew = false) {
    if (!state.grid) return;
    const name = $('#patternName').value.trim() || '拼豆图纸';
    $('#patternName').value = name;
    try {
      let detail;
      if (state.patternId && !asNew) {
        detail = await Api.updatePattern(state.patternId, { name, grid: state.grid, done_codes: [...state.done] });
      } else {
        detail = await Api.createPattern({
          name,
          palette_id: state.palette.id,
          grid: state.grid,
          params: state.file ? state.params : null,
          source_filename: state.file?.name || null,
        });
        if (state.done.size) detail = await Api.updatePattern(detail.id, { done_codes: [...state.done] });
      }
      state.patternId = detail.id;
      markDirty(false);
      toast(`已保存「${name}」`);
      loadHistory();
    } catch (err) {
      toast(err.message, 'error');
    }
  }
  $('#saveBtn').addEventListener('click', () => save(false));
  $('#saveAsBtn').addEventListener('click', () => save(true));

  function confirmDiscard() {
    return !(state.dirty && state.patternId && !confirm('当前图纸有未保存的修改，确定放弃吗？'));
  }
  window.addEventListener('beforeunload', (e) => {
    if (state.dirty && state.patternId) e.preventDefault();
  });

  // ============================================================ 导出

  $$('[data-export]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!state.grid) return;
    const format = btn.dataset.export;
    const pitch = Number($('#pitchInput').value) || null;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '导出中…';
    try {
      if (state.patternId && !state.dirty) {
        await Api.exportSaved(state.patternId, format, state.params.board_size, pitch);
      } else {
        await Api.exportGrid({
          name: $('#patternName').value.trim() || '拼豆图纸',
          palette_id: state.palette.id,
          grid: state.grid,
          format,
          board_size: state.params.board_size,
          pitch_mm: pitch,
        });
      }
      if (format === 'pdf') toast('PDF 豆板页请按「实际大小 / 100%」打印');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }));

  // ============================================================ 历史图纸

  async function loadHistory() {
    try {
      const { items } = await Api.listPatterns(50);
      const box = $('#history');
      if (!items.length) {
        box.innerHTML = '<p class="hint">还没有保存的图纸</p>';
        return;
      }
      box.innerHTML = items.map((p) => `
        <div class="history-item ${p.id === state.patternId ? 'active' : ''}" data-id="${p.id}" role="button" tabindex="0">
          <img src="${p.thumbnail_url}" alt="" loading="lazy">
          <div class="history-meta">
            <b>${escapeHtml(p.name)}</b>
            <span>${p.width}×${p.height} · ${p.bead_count} 颗 · ${p.color_count} 色${p.done_color_count
              ? ` · 已拼 ${p.done_color_count}/${p.color_count} 色` : ''} · ${formatTime(p.updated_at)}</span>
          </div>
          <button type="button" class="btn btn-ghost btn-sm" data-delete="${p.id}" title="删除">✕</button>
        </div>`).join('');
    } catch (err) {
      toast(`加载历史失败：${err.message}`, 'error');
    }
  }

  $('#history').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-delete]');
    if (del) {
      e.stopPropagation();
      const id = Number(del.dataset.delete);
      const name = del.closest('.history-item').querySelector('b').textContent;
      if (!confirm(`确定删除图纸「${name}」吗？删除后无法恢复。`)) return;
      try {
        await Api.deletePattern(id);
        if (state.patternId === id) { state.patternId = null; markDirty(true); }
        toast(`已删除「${name}」`);
        loadHistory();
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    const item = e.target.closest('.history-item');
    if (item) openPattern(Number(item.dataset.id));
  });
  $('#history').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.classList.contains('history-item')) openPattern(Number(e.target.dataset.id));
  });

  async function openPattern(id) {
    if (!confirmDiscard()) return;
    try {
      const detail = await Api.getPattern(id);
      state.convertCtrl?.abort();
      state.params = { ...DEFAULT_PARAMS, ...detail.params, palette_id: detail.palette_id };
      await usePalette(detail.palette_id);
      writeParamsToForm();
      // 历史图纸没有保存原图，清空上传状态
      state.file = null;
      state.image = null;
      canvas.setImage(null);
      $('#sourcePreview').hidden = true;
      $('#fileInput').value = '';
      $('#fileName').textContent = detail.source_filename
        ? `原图：${detail.source_filename}（重新上传后可调整参数）` : '重新上传原图后可调整参数';
      updateViewButtons();

      state.patternId = detail.id;
      state.warnings = [];
      state.undo = [];
      state.redo = [];
      state.orientation = { flip: false, rot: 0 };  // 保存的网格已经是翻转 / 旋转后的结果
      state.done = new Set(detail.done_codes || []);
      if (state.build.active) exitBuild();
      $('#patternName').value = detail.name;
      applyGrid(detail.grid);
      markDirty(false);
      loadHistory();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ============================================================ 排除颜色弹窗

  const excludeDialog = $('#excludeDialog');
  let excludeDraft = new Set();

  function renderExcludeGrid() {
    const used = new Set((state.grid || []).flat().filter(Boolean));
    $('#excludeGrid').innerHTML = state.palette.colors.map((c) => `
      <button type="button" class="swatch ${excludeDraft.has(c.code) ? 'excluded' : ''} ${used.has(c.code) ? 'used' : ''}"
        data-code="${escapeHtml(c.code)}" title="${escapeHtml(colorLabel(c))}">
        <i style="background:${c.hex}"></i>${escapeHtml(c.code)}
      </button>`).join('');
  }

  $('#excludeBtn').addEventListener('click', () => {
    excludeDraft = new Set(state.params.excluded_codes);
    renderExcludeGrid();
    excludeDialog.showModal();
  });
  $('#excludeGrid').addEventListener('click', (e) => {
    const sw = e.target.closest('.swatch');
    if (!sw) return;
    const code = sw.dataset.code;
    if (excludeDraft.has(code)) excludeDraft.delete(code); else excludeDraft.add(code);
    sw.classList.toggle('excluded');
  });
  $('#excludeNone').addEventListener('click', () => { excludeDraft.clear(); renderExcludeGrid(); });
  $('#excludeUnused').addEventListener('click', () => {
    const used = new Set((state.grid || []).flat().filter(Boolean));
    if (!used.size) return toast('还没有生成图纸', 'error');
    excludeDraft = new Set(state.palette.colors.map((c) => c.code).filter((c) => !used.has(c)));
    renderExcludeGrid();
  });
  excludeDialog.addEventListener('close', () => {
    if (excludeDialog.returnValue !== 'apply') return;
    if (excludeDraft.size >= state.palette.colors.length) return toast('至少要保留一种颜色', 'error');
    state.params.excluded_codes = [...excludeDraft];
    updateExcludeCount();
    scheduleConvert(0);
  });

  function updateExcludeCount() {
    const n = state.params.excluded_codes.length;
    $('#excludeCount').textContent = n ? ` · 已排除 ${n} 色` : '';
  }

  // ============================================================ 画笔颜色选择弹窗

  const paintDialog = $('#paintDialog');
  function openPaintPicker() {
    if (!state.palette) return;
    $('#paintGrid').innerHTML = state.palette.colors.map((c) => `
      <button type="button" class="swatch" value="${escapeHtml(c.code)}" title="${escapeHtml(colorLabel(c))}">
        <i style="background:${c.hex}"></i>${escapeHtml(c.code)}
      </button>`).join('');
    paintDialog.showModal();
  }
  $('#paintGrid').addEventListener('click', (e) => {
    const sw = e.target.closest('.swatch');
    if (!sw) return;
    state.paintCode = sw.value;
    updatePaintChip();
    setTool('brush');
    paintDialog.close();
  });

  // ============================================================ 自定义色卡弹窗

  const paletteDialog = $('#paletteDialog');

  function renderCustomPalettes() {
    const custom = state.palettes.filter((p) => p.source === 'custom');
    $('#customPaletteList').innerHTML = custom.length
      ? custom.map((p) => `
        <div class="custom-item">
          <div>${escapeHtml(p.name)}<span>${escapeHtml(p.brand)} · ${p.color_count} 色 · ${p.bead_size_mm}mm</span></div>
          <button type="button" class="btn btn-sm" data-delete-palette="${escapeHtml(p.id)}">删除</button>
        </div>`).join('')
      : '<p class="hint">还没有自定义色卡。可以按自己手上的实物色卡拍照取色后导入，颜色会更准。</p>';
  }

  $('#managePalettesBtn').addEventListener('click', () => {
    renderCustomPalettes();
    $('#cpHint').textContent = '';
    paletteDialog.showModal();
  });

  $('#customPaletteList').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-delete-palette]');
    if (!btn) return;
    const id = btn.dataset.deletePalette;
    const palette = state.palettes.find((p) => p.id === id);
    if (!confirm(`确定删除色卡「${palette.name}」吗？使用该色卡保存的图纸将无法打开。`)) return;
    try {
      await Api.deletePalette(id);
      state.paletteCache.delete(id);
      await loadPalettes(state.palette.id === id ? DEFAULT_PARAMS.palette_id : state.palette.id);
      renderCustomPalettes();
      toast('已删除色卡');
    } catch (err) { toast(err.message, 'error'); }
  });

  /** 解析「色号, 名称, #RRGGBB」多行文本 */
  function parseColorLines(text) {
    const colors = [];
    const errors = [];
    text.split(/\r?\n/).forEach((raw, i) => {
      const line = raw.trim();
      if (!line) return;
      const tokens = line.split(/[\s,，;；\t]+/).filter(Boolean);
      const hexIndex = tokens.findLastIndex((t) => /^#?[0-9a-f]{6}$/i.test(t));
      if (hexIndex < 1) { errors.push(`第 ${i + 1} 行格式不对：${line}`); return; }
      colors.push({
        code: tokens[0],
        name: tokens.slice(1, hexIndex).join(' ') || tokens[0],
        hex: `#${tokens[hexIndex].replace('#', '').toUpperCase()}`,
      });
    });
    return { colors, errors };
  }

  $('#cpSubmit').addEventListener('click', async () => {
    const name = $('#cpName').value.trim();
    const { colors, errors } = parseColorLines($('#cpColors').value);
    const hint = $('#cpHint');
    if (!name) { hint.textContent = '请填写色卡名称'; return; }
    if (errors.length) { hint.textContent = errors.slice(0, 3).join('；'); return; }
    if (!colors.length) { hint.textContent = '请至少填写一个颜色'; return; }
    try {
      const created = await Api.createPalette({
        name,
        brand: $('#cpBrand').value.trim() || '自定义',
        bead_size_mm: Number($('#cpSize').value) || 5,
        colors,
      });
      toast(`已导入「${created.name}」，共 ${created.color_count} 色`);
      $('#cpColors').value = '';
      $('#cpName').value = '';
      paletteDialog.close();
      state.paletteCache.set(created.id, created);
      await loadPalettes(created.id);
      scheduleConvert(0);
    } catch (err) {
      hint.textContent = err.message;
    }
  });

  // ============================================================ 工具函数

  function toast(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type === 'error' ? 'error' : ''}`;
    el.textContent = message;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), type === 'error' ? 5000 : 2600);
  }

  function hexToRgb(hex) {
    const h = hex.replace('#', '');
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }

  function colorLabel(c) { return c.name && c.name !== c.code ? `${c.code} ${c.name}` : c.code; }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function formatSize(mb) { return `${mb.toFixed(mb < 10 ? 1 : 0)}MB`; }

  function clampInt(v, min, max) { return Math.min(max, Math.max(min, Math.round(Number(v) || min))); }

  function formatTime(iso) {
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ============================================================ 初始化

  (async function init() {
    setTool('pan');
    writeParamsToForm();
    updateViewButtons();
    try {
      await loadPalettes();
    } catch (err) {
      toast(`加载色卡失败：${err.message}`, 'error');
    }
    loadModels();
    loadHistory();
  })();
})();
