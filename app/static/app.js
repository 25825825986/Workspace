/* =============================================================================
   AI_Review 前端 UI Kit（原生 JS，零构建）
   - ui.toast(msg, {type, actionLabel, onAction})   轻提示（含撤销动作）
   - ui.confirm({title, message, danger, input})    确认弹层（替代原生 confirm/prompt）
   - ui.loading(btn, on, text)                      按钮内联 loading
   - ui.progress.start()/stop()                     顶部不定进度条
   - ui.drawer.open({title, html}) / close()        右侧抽屉（导出预览等）
   - ui.palette                                     ⌘K 全局搜索
   - ui.markDirty() / ui.markSaved()                离开保护 + "已保存"提示
   - [data-post][data-confirm] 声明式操作；[data-autosave] 防抖自动保存
   ========================================================================== */
(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const PREFERS_REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- 容器 ---------- */
  function ensureRoot(id, cls, attrs) {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement('div');
      el.id = id;
      el.className = cls || '';
      Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
      document.body.appendChild(el);
    }
    return el;
  }

  /* ---------- Toast ---------- */
  let toastTimer = null;
  function toast(message, opts) {
    const options = opts || {};
    const host = ensureRoot('ui-toasts', 'ui-toasts', { role: 'status', 'aria-live': 'polite' });
    const el = document.createElement('div');
    el.className = 'ui-toast' + (options.type ? ' ' + options.type : '');
    el.setAttribute('role', options.type === 'error' ? 'alert' : 'status');

    const text = document.createElement('span');
    text.className = 'ui-toast-text';
    text.textContent = message;
    el.appendChild(text);

    if (options.actionLabel) {
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'ui-toast-action';
      action.textContent = options.actionLabel;
      action.addEventListener('click', () => {
        dismiss();
        if (options.onAction) options.onAction();
      });
      el.appendChild(action);
    }
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'ui-toast-close';
    close.setAttribute('aria-label', '关闭提示');
    close.textContent = '×';
    close.addEventListener('click', dismiss);
    el.appendChild(close);

    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('in'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(dismiss, options.duration || (options.actionLabel ? 9000 : 4200));

    function dismiss() {
      el.classList.remove('in');
      setTimeout(() => el.remove(), PREFERS_REDUCED ? 0 : 180);
      if (host.children.length === 0) host.setAttribute('hidden', 'hidden');
    }
    host.removeAttribute('hidden');
    return dismiss;
  }

  /* ---------- 模态（确认 / 输入 / 抽屉共用） ---------- */
  function openOverlay(inner, opts) {
    const options = opts || {};
    const previous = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'ui-overlay' + (options.variant ? ' ' + options.variant : '');

    const panel = document.createElement('div');
    panel.className = 'ui-panel' + (options.size ? ' ' + options.size : '');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    if (options.title) {
      panel.setAttribute('aria-label', options.title);
    }
    panel.appendChild(inner);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    document.body.classList.add('ui-locked');
    requestAnimationFrame(() => overlay.classList.add('in'));

    function close() {
      overlay.classList.remove('in');
      setTimeout(() => overlay.remove(), PREFERS_REDUCED ? 0 : 160);
      document.body.classList.remove('ui-locked');
      document.removeEventListener('keydown', onKey, true);
      if (previous && previous.focus) previous.focus();
    }
    function onKey(event) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        close();
        if (options.onEscape) options.onEscape();
        return;
      }
      if (event.key === 'Tab') {
        const focusables = $$('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])', panel)
          .filter((el) => !el.disabled && el.offsetParent !== null);
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    document.addEventListener('keydown', onKey, true);
    if (options.dismissible !== false) {
      overlay.addEventListener('click', (event) => {
        if (event.target === overlay) { close(); if (options.onEscape) options.onEscape(); }
      });
    }
    return { close, panel, overlay };
  }

  function confirmDialog(options) {
    const opts = options || {};
    return new Promise((resolve) => {
      const wrap = document.createElement('div');
      wrap.className = 'ui-dialog';

      const head = document.createElement('div');
      head.className = 'ui-dialog-head';
      const h = document.createElement('h3');
      h.textContent = opts.title || '请确认';
      head.appendChild(h);
      wrap.appendChild(head);

      if (opts.message) {
        const p = document.createElement('p');
        p.className = 'ui-dialog-msg';
        p.textContent = opts.message;
        wrap.appendChild(p);
      }

      let inputEl = null;
      if (opts.input) {
        const label = document.createElement('label');
        label.className = 'ui-dialog-label';
        label.textContent = opts.input.label || '请输入';
        inputEl = document.createElement('input');
        inputEl.type = opts.input.type || 'text';
        if (opts.input.value !== undefined) inputEl.value = opts.input.value;
        if (opts.input.min !== undefined) inputEl.min = opts.input.min;
        if (opts.input.max !== undefined) inputEl.max = opts.input.max;
        label.appendChild(inputEl);
        wrap.appendChild(label);
      }

      const foot = document.createElement('div');
      foot.className = 'ui-dialog-foot';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'btn';
      cancel.textContent = opts.cancelText || '取消';
      const ok = document.createElement('button');
      ok.type = 'button';
      ok.className = 'btn ' + (opts.danger ? 'danger' : 'primary');
      ok.textContent = opts.confirmText || '确定';
      foot.append(cancel, ok);
      wrap.appendChild(foot);

      const handle = openOverlay(wrap, {
        title: opts.title || '请确认', size: 'sm',
        onEscape: () => resolve(null),
      });
      cancel.addEventListener('click', () => { handle.close(); resolve(null); });
      ok.addEventListener('click', () => {
        const value = inputEl ? inputEl.value.trim() : true;
        if (inputEl && opts.input.required !== false && value === '') {
          inputEl.focus();
          return;
        }
        handle.close();
        resolve(value);
      });
      setTimeout(() => (inputEl || ok).focus(), 30);
    });
  }

  /* ---------- 按钮 loading / 顶部进度条 ---------- */
  function loading(btn, on, text) {
    if (!btn) return;
    if (on) {
      if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
      btn.disabled = true;
      btn.classList.add('is-loading');
      btn.innerHTML = '<span class="ui-spinner" aria-hidden="true"></span>' + (text || '处理中…');
    } else {
      btn.disabled = false;
      btn.classList.remove('is-loading');
      if (btn.dataset.originalHtml) btn.innerHTML = btn.dataset.originalHtml;
    }
  }

  const progress = {
    el: null,
    start(label) {
      if (!progress.el) {
        progress.el = ensureRoot('ui-progress', 'ui-progress', {
          role: 'progressbar', 'aria-valuetext': '处理中',
        });
        progress.el.innerHTML = '<i></i>';
      }
      progress.el.setAttribute('aria-valuetext', label || '处理中');
      progress.el.classList.add('in');
    },
    stop() {
      if (progress.el) progress.el.classList.remove('in');
    },
  };

  /* ---------- 抽屉（导出预览等） ---------- */
  const drawer = {
    handle: null,
    open(opts) {
      const options = opts || {};
      const wrap = document.createElement('div');
      wrap.className = 'ui-drawer';

      const head = document.createElement('div');
      head.className = 'ui-drawer-head';
      const h = document.createElement('h3');
      h.textContent = options.title || '详情';
      head.appendChild(h);
      const actions = document.createElement('div');
      actions.className = 'ops';
      if (options.downloadUrl) {
        const dl = document.createElement('a');
        dl.className = 'btn small primary';
        dl.href = options.downloadUrl;
        dl.innerHTML = icon('download') + '下载 .md';
        actions.appendChild(dl);
      }
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn small';
      close.textContent = '关闭';
      close.addEventListener('click', () => drawer.close());
      actions.appendChild(close);
      head.appendChild(actions);
      wrap.appendChild(head);

      const body = document.createElement('div');
      body.className = 'ui-drawer-body';
      if (options.html) {
        const pre = document.createElement('pre');
        pre.className = 'md-preview';
        pre.textContent = options.html;
        body.appendChild(pre);
      } else if (options.loading) {
        body.innerHTML = skeleton(6);
      }
      wrap.appendChild(body);

      drawer.handle = openOverlay(wrap, { variant: 'right', size: 'lg', title: options.title,
                                          dismissible: true, onEscape: () => { drawer.handle = null; } });
      drawer.body = body;
      return drawer;
    },
    setContent(text) {
      if (!drawer.body) return;
      drawer.body.innerHTML = '';
      const pre = document.createElement('pre');
      pre.className = 'md-preview';
      pre.textContent = text;
      drawer.body.appendChild(pre);
    },
    close() {
      if (drawer.handle) drawer.handle.close();
      drawer.handle = null;
    },
  };

  function skeleton(lines) {
    let html = '<div class="ui-skeleton">';
    for (let i = 0; i < (lines || 4); i += 1) {
      html += `<span class="ui-skeleton-line" style="width:${60 + ((i * 13) % 38)}%"></span>`;
    }
    return html + '</div>';
  }

  /* ---------- 图标 ---------- */
  const ICONS = {
    download: '<path d="M12 3v10m0 0l4-4m-4 4L8 9M4 17h16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="2.6" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    eyeOff: '<path d="M4 4l16 16M10.6 6.1A8.5 8.5 0 0112 6c6 0 9.5 6 9.5 6a17 17 0 01-3.3 3.9M6.4 8.1A17 17 0 002.5 12s3.5 6 9.5 6c1.2 0 2.3-.2 3.3-.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    play: '<path d="M8 5.5l10 6.5-10 6.5z" fill="currentColor"/>',
    plus: '<path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    trash: '<path d="M4 7h16M9 7V5h6v2M6 7l1 12h10l1-12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    check: '<path d="M5 13l4 4L19 7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    search: '<circle cx="11" cy="11" r="6" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M20 20l-4.2-4.2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    more: '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
  };
  function icon(name) {
    const path = ICONS[name] || '';
    return `<svg class="ico" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false">${path}</svg>`;
  }

  /* ---------- 保存状态与离开保护 ---------- */
  let dirty = false;
  function markDirty() {
    dirty = true;
    const state = document.getElementById('save-state');
    if (state) { state.textContent = '有未保存的修改'; state.classList.add('dirty'); }
  }
  function markSaved(text) {
    dirty = false;
    const state = document.getElementById('save-state');
    if (state) {
      const t = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      state.textContent = (text || '已保存') + ' ' + pad(t.getHours()) + ':' + pad(t.getMinutes()) + ':' + pad(t.getSeconds());
      state.classList.remove('dirty');
    }
  }
  window.addEventListener('beforeunload', (event) => {
    if (!dirty) return undefined;
    event.preventDefault();
    event.returnValue = '有未保存的修改，确定离开吗？';
    return event.returnValue;
  });

  /* ---------- 请求 ---------- */
  async function postJSON(url, payload) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* 可能无响应体 */ }
    if (!r.ok) {
      toast(data.error || ('请求失败：HTTP ' + r.status), { type: 'error' });
      throw new Error(data.error || 'request failed');
    }
    return data;
  }

  /* ---------- 声明式操作按钮 ---------- */
  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-post]');
    if (!btn || btn.disabled) return;
    const postUrl = btn.dataset.post;
    const confirmText = btn.dataset.confirm;
    if (confirmText) {
      const ok = await confirmDialog({
        title: btn.dataset.confirmTitle || '请确认',
        message: confirmText,
        confirmText: btn.dataset.confirmOk || '确定',
        danger: btn.classList.contains('danger') || btn.dataset.confirmDanger === '1',
      });
      if (ok === null) return;
    }
    loading(btn, true);
    progress.start('提交中');
    try {
      const result = await postJSON(postUrl, {});
      progress.stop();
      if (result && result.undo_url) {
        toast(result.message || '操作完成', {
          type: 'success', actionLabel: '撤销',
          onAction: async () => {
            try {
              await postJSON(result.undo_url, result.undo_payload || {});
              toast('已撤销', { type: 'success' });
              setTimeout(() => location.reload(), 400);
            } catch (e) { /* 已在 postJSON 提示 */ }
          },
        });
        setTimeout(() => location.reload(), 1200);
      } else {
        toast(result && result.message ? result.message : '操作完成', { type: 'success' });
        setTimeout(() => location.reload(), 350);
      }
    } catch (e) {
      progress.stop();
      loading(btn, false);
    }
  });

  /* ---------- 自动保存（data-autosave + data-autosave-payload） ---------- */
  function bindAutosave(root) {
    $$('[data-autosave]', root).forEach((host) => {
      const url = host.dataset.autosave;
      const map = JSON.parse(host.dataset.autosavePayload || '{}');
      let timer = null;
      const collect = () => {
        const payload = {};
        Object.entries(map).forEach(([key, sel]) => {
          const el = document.querySelector(sel);
          if (el) payload[key] = el.value;
        });
        return payload;
      };
      const schedule = () => {
        markDirty();
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try {
            await postJSON(url, collect());
            markSaved();
          } catch (e) { /* 已提示 */ }
        }, 800);
      };
      Object.values(map).forEach((sel) => {
        const el = document.querySelector(sel);
        if (el) el.addEventListener('input', schedule);
      });
      const form = host.closest('form');
      if (form) {
        form.addEventListener('submit', () => {
          if (timer) clearTimeout(timer);
        });
      }
    });
  }

  /* ---------- ⌘K 全局搜索 ---------- */
  const palette = {
    handle: null,
    open() {
      if (palette.handle) return;
      const wrap = document.createElement('div');
      wrap.className = 'ui-palette';

      const box = document.createElement('div');
      box.className = 'ui-palette-input';
      box.innerHTML = icon('search');
      const input = document.createElement('input');
      input.type = 'search';
      input.placeholder = '搜索面试记录 / 知识点，回车打开…';
      input.setAttribute('aria-label', '全局搜索');
      box.appendChild(input);
      const hint = document.createElement('kbd');
      hint.textContent = 'Esc';
      box.appendChild(hint);
      wrap.appendChild(box);

      const list = document.createElement('div');
      list.className = 'ui-palette-list';
      list.setAttribute('role', 'listbox');
      wrap.appendChild(list);

      palette.handle = openOverlay(wrap, { size: 'md', title: '全局搜索',
                                           onEscape: () => { palette.handle = null; } });

      let items = [];
      let active = 0;
      function render() {
        list.innerHTML = '';
        if (items.length === 0) {
          list.innerHTML = '<div class="ui-palette-empty">没有匹配结果（试试题目关键词或公司名）</div>';
          return;
        }
        items.forEach((item, idx) => {
          const row = document.createElement('a');
          row.className = 'ui-palette-item' + (idx === active ? ' active' : '');
          row.href = item.url;
          row.setAttribute('role', 'option');
          row.innerHTML = `<span class="ui-palette-kind">${item.kind}</span>
            <span class="ui-palette-title">${item.title}</span>
            <span class="ui-palette-sub">${item.sub || ''}</span>`;
          list.appendChild(row);
        });
      }
      async function search(q) {
        if (!q || q.trim().length === 0) {
          items = [
            { kind: '页面', title: '面试记录', url: '/', sub: '全部面试' },
            { kind: '页面', title: '导入面试', url: '/import', sub: '新增转写' },
            { kind: '页面', title: '知识库', url: '/knowledge', sub: '专题与相似' },
            { kind: '页面', title: '模拟面试', url: '/mock', sub: '开始演练' },
            { kind: '页面', title: '设置', url: '/settings', sub: '主题与模型' },
          ];
          active = 0; render(); return;
        }
        try {
          const r = await fetch('/api/search?q=' + encodeURIComponent(q.trim()));
          const data = await r.json();
          items = data.items || [];
          active = 0;
          render();
        } catch (e) { /* 忽略 */ }
      }
      let debounce = null;
      input.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => search(input.value), 180);
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') { event.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); active = Math.max(active - 1, 0); render(); }
        else if (event.key === 'Enter') {
          event.preventDefault();
          const target = items[active];
          if (target) location.href = target.url;
        }
      });
      search('');
      setTimeout(() => input.focus(), 40);
    },
  };

  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      palette.open();
    }
  });

  /* ---------- 声明式：导出预览抽屉（替代独立预览页） ---------- */
  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-export-preview]');
    if (!btn) return;
    event.preventDefault();
    drawer.open({ title: btn.dataset.exportTitle || '导出预览', loading: true });
    try {
      const r = await fetch(btn.dataset.exportPreview);
      const data = await r.json();
      if (!r.ok) { throw new Error(data.error || '导出失败'); }
      drawer.handle && drawer.handle.close();
      drawer.open({
        title: (btn.dataset.exportTitle || '导出预览') + '（Markdown 预览）',
        html: data.content,
        downloadUrl: data.download_url,
      });
      toast('已生成导出文件（同时保存到 ' + (data.saved_to || 'data/export/') + '）', { type: 'success' });
    } catch (e) {
      drawer.close();
      toast(e.message || '导出失败', { type: 'error' });
    }
  });

  /* ---------- 声明式：加载更多（分页片段） ---------- */
  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-load-more]');
    if (!btn) return;
    const target = document.querySelector(btn.dataset.loadTarget || '#rec-list');
    const offset = Number(btn.dataset.offset || 0);
    const params = new URLSearchParams(btn.dataset.params || '');
    params.set('offset', String(offset));
    loading(btn, true, '加载中…');
    try {
      const r = await fetch(btn.dataset.loadMore + '?' + params.toString());
      const html = await r.text();
      if (target) target.insertAdjacentHTML('beforeend', html);
      const added = (html.match(/class="rec-card|class="entry"/g) || []).length;
      const next = offset + (added || Number(btn.dataset.page || 20));
      btn.dataset.offset = String(next);
      const shown = document.querySelectorAll(btn.dataset.countSelector || '.rec-card').length;
      const total = Number(btn.dataset.total || 0);
      const counter = document.getElementById('load-more-count');
      if (counter) counter.textContent = `已显示 ${shown} / 共 ${total}`;
      loading(btn, false);
      if (shown >= total) { btn.remove(); }
    } catch (e) {
      loading(btn, false);
      toast('加载失败，请重试', { type: 'error' });
    }
  });

  /* ---------- 启动 ---------- */
  window.ui = {
    toast, confirm: confirmDialog, loading, progress, drawer, palette,
    markDirty, markSaved, icon, skeleton, openOverlay,
  };
  window.postJSON = postJSON;
  document.addEventListener('DOMContentLoaded', () => bindAutosave(document));
  if (document.readyState !== 'loading') bindAutosave(document);

  /* 顶栏快速切换主题（浅 ↔ 深） */
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', async () => {
      const el = document.documentElement;
      const next = el.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      el.setAttribute('data-theme', next);
      try { await postJSON('/api/settings', { theme: next }); } catch (e) { /* 保留本地切换 */ }
    });
  }

  /* 钉住"更多"浮动菜单：点击外部/Esc 关闭（替代 details 的无障碍问题） */
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-popover-trigger]');
    $$('[data-popover]').forEach((pop) => {
      const owner = pop.previousElementSibling;
      if (trigger && owner === trigger) {
        const open = pop.hasAttribute('hidden');
        $$('[data-popover]').forEach((other) => other.setAttribute('hidden', 'hidden'));
        if (open) { pop.removeAttribute('hidden'); } else { pop.setAttribute('hidden', 'hidden'); }
        trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        return;
      }
      if (!pop.contains(event.target) && pop !== event.target) {
        pop.setAttribute('hidden', 'hidden');
        if (owner) owner.setAttribute('aria-expanded', 'false');
      }
    });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      $$('[data-popover]').forEach((pop) => {
        pop.setAttribute('hidden', 'hidden');
        const owner = pop.previousElementSibling;
        if (owner) owner.setAttribute('aria-expanded', 'false');
      });
    }
  });
})();
