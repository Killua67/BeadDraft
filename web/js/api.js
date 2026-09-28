/**
 * 后端接口封装。所有接口出错时抛出 Error，message 为后端返回的中文 detail。
 */
const Api = (() => {
  async function request(url, options = {}) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      let message = `请求失败（${resp.status}）`;
      try {
        const body = await resp.json();
        if (body.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      } catch (_) { /* 非 JSON 响应，使用默认提示 */ }
      throw new Error(message);
    }
    return resp;
  }

  const json = (url, options) => request(url, options).then((r) => (r.status === 204 ? null : r.json()));
  const jsonBody = (method, body) => ({
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  /** 从 Content-Disposition 中解析文件名（支持 filename*=UTF-8''xxx） */
  function filenameFrom(resp, fallback) {
    const header = resp.headers.get('Content-Disposition') || '';
    const match = header.match(/filename\*=UTF-8''([^;]+)/i);
    return match ? decodeURIComponent(match[1]) : fallback;
  }

  /** 下载接口返回的文件 */
  async function download(url, options, fallbackName) {
    const resp = await request(url, options);
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filenameFrom(resp, fallbackName);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
  }

  return {
    listPalettes: () => json('/api/palettes'),
    getPalette: (id) => json(`/api/palettes/${encodeURIComponent(id)}`),
    createPalette: (payload) => json('/api/palettes', jsonBody('POST', payload)),
    deletePalette: (id) => json(`/api/palettes/${encodeURIComponent(id)}`, { method: 'DELETE' }),

    /** 图片转网格；signal 用于取消上一次尚未完成的请求 */
    convert(file, params, signal) {
      const form = new FormData();
      form.append('file', file);
      form.append('params', JSON.stringify(params));
      return json('/api/convert', { method: 'POST', body: form, signal });
    },

    listPatterns: (limit = 50) => json(`/api/patterns?limit=${limit}`),
    getPattern: (id) => json(`/api/patterns/${id}`),
    createPattern: (payload) => json('/api/patterns', jsonBody('POST', payload)),
    updatePattern: (id, payload) => json(`/api/patterns/${id}`, jsonBody('PUT', payload)),
    deletePattern: (id) => json(`/api/patterns/${id}`, { method: 'DELETE' }),

    /** 导出已保存的图纸 */
    exportSaved(id, format, boardSize, pitchMm) {
      const q = new URLSearchParams({ format, board_size: boardSize });
      if (pitchMm) q.set('pitch_mm', pitchMm);
      return download(`/api/patterns/${id}/export?${q}`, {}, `拼豆图纸.${format}`);
    },
    /** 导出未保存（或已修改未保存）的图纸 */
    exportGrid(payload) {
      return download('/api/export', jsonBody('POST', payload), `拼豆图纸.${payload.format}`);
    },
  };
})();
