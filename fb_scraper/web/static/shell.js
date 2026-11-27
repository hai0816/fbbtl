// Phần dùng chung của hai trang: icon, gọi API có token, thông báo, giao diện sáng/tối, trạng thái Chrome.
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 4 16 4 16 0V5M4 12v7c0 4 16 4 16 0v-7"/>',
  external: '<path d="M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
  document: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6M8 13h8m-8 4h5"/>',
  comment: '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3 2-6a8 8 0 1 1 17-5Z"/><path d="M8 10h8m-8 4h5"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  scan: '<path d="M8 3H4a1 1 0 0 0-1 1v4m13-5h4a1 1 0 0 1 1 1v4M3 16v4a1 1 0 0 0 1 1h4m13-5v4a1 1 0 0 1-1 1h-4M2 12h20"/>',
  play: '<path d="m8 5 11 7-11 7V5Z"/>', lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>', search: '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  upload: '<path d="M12 16V4m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  clock2: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8v.01"/>',
  chevronLeft: '<path d="m15 6-6 6 6 6"/>', chevronRight: '<path d="m9 6 6 6-6 6"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<path d="M12 4 2.5 20h19L12 4Z"/><path d="M12 10v4m0 3v.01"/>', filter: '<path d="M3 5h18l-7 8v6l-4-2v-4L3 5Z"/>'
};
export function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}
export function renderIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(node => { node.innerHTML = icon(node.dataset.icon); });
}
export const $ = id => document.getElementById(id);
export const fmt = value => new Intl.NumberFormat('vi-VN').format(value ?? 0);
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
export function iconEl(name, className = '') {
  const node = el('span', className); node.innerHTML = icon(name); node.setAttribute('aria-hidden', 'true'); return node;
}

export const session = {token: ''};
export async function api(path, payload) {
  const options = {cache: 'no-store'};
  if (payload !== undefined) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Scraper-Token': session.token}, body: JSON.stringify(payload)});
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Chưa thực hiện được thao tác.');
  return result;
}

let toastTimer;
export function toast(message) {
  const box = $('toast'); box.textContent = message; box.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { box.hidden = true; }, 4500);
}

// Giao diện: theo hệ điều hành cho đến khi người dùng chọn; lựa chọn chỉ lưu trên trình duyệt này.
function storedTheme() { try { return localStorage.getItem('fbc-theme'); } catch { return null; } }
function currentTheme() {
  return document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}
function paintThemeButton() {
  const button = $('theme-toggle'); if (!button) return;
  const dark = currentTheme() === 'dark';
  button.replaceChildren(iconEl(dark ? 'sun' : 'moon'), el('span', 'nav-text', dark ? 'Giao diện sáng' : 'Giao diện tối'));
  button.setAttribute('aria-label', dark ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối');
}
export function initTheme(onChange) {
  const saved = storedTheme(); if (saved) document.documentElement.dataset.theme = saved;
  paintThemeButton();
  $('theme-toggle')?.addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('fbc-theme', next); } catch { /* chế độ riêng tư: chỉ áp dụng cho phiên này */ }
    paintThemeButton(); onChange?.();
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { paintThemeButton(); onChange?.(); });
}

export function showBrowserState(ready) {
  $('browser-dot')?.classList.toggle('ready', ready);
  if ($('browser-state')) $('browser-state').textContent = ready ? 'Chrome sẵn sàng kết nối' : 'Chrome chưa được mở';
  $('browser-dot')?.setAttribute('title', ready ? 'Chrome sẵn sàng kết nối' : 'Chrome chưa được mở');
}
