/* AI_Review 页面通用脚本（原生 JS，零构建） */
async function apiPost(url, payload) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ('请求失败: HTTP ' + r.status));
  return d;
}
