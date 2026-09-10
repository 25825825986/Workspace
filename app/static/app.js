/* AI_Review 前端通用脚本（原生 JS，零构建）
   - postJSON：统一请求 + 错误提示（供各页面内联脚本调用）
   - [data-post] / [data-confirm]：声明式操作按钮（确认 → 请求 → 刷新页面）
*/
async function postJSON(url, payload) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* 非 JSON 响应 */ }
  if (!r.ok) {
    alert(data.error || ('请求失败：HTTP ' + r.status));
    throw new Error(data.error || 'request failed');
  }
  return data;
}
window.postJSON = postJSON;

document.querySelectorAll('[data-post]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    if (btn.dataset.confirm && !confirm(btn.dataset.confirm)) return;
    btn.disabled = true;
    try {
      await postJSON(btn.dataset.post, {});
      location.reload();
    } catch (e) {
      btn.disabled = false;
    }
  });
});

/* 顶栏快速切换主题（浅 ↔ 深）：本地立即生效，并写入设置以便下次打开保持 */
(function () {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const el = document.documentElement;
    const next = el.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    el.setAttribute('data-theme', next);
    try {
      await postJSON('/api/settings', { theme: next });
    } catch (e) {
      /* 写入失败也保留本次视觉切换 */
    }
  });
})();
