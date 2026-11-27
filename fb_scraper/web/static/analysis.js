// Trang Phân tích rủi ro: điều hướng tab và tab Tổng quan. Dữ liệu bài viết luôn gán bằng textContent (không innerHTML).
import {$, api, el, fmt, initTheme, renderIcons, session, showBrowserState, toast} from './shell.js';
import {LEVELS, badge, day, empty, guard, initReview, loadPosts, openPost, scoreBox, setFilters} from './analysis-review.js';
import {initCheck} from './analysis-check.js';
import {initActors, loadActors} from './analysis-actors.js';
import {fillSettings, initConfig, loadImports, loadKeywords, loadRules, loadSimilar, loadSources} from './analysis-config.js';

const RULE_CATEGORIES = {JOB: 'Tuyển dụng', INVESTMENT: 'Đầu tư', COMMON: 'Chung', COMBO: 'Kết hợp'};
const SERIES = [['JOB', 'job', 'Tuyển dụng'], ['INVESTMENT', 'inv', 'Đầu tư'], ['OTHER', 'other', 'Khác']];
const SVG = 'http://www.w3.org/2000/svg';
let categories = {}, timelineDays = [];

function pct(part, whole) { return whole ? `${Math.round(part / whole * 100)}%` : '0%'; }
function goto(tab, filters) { if (filters) setFilters(filters); showTab(tab); }

// ---------- Tổng quan ----------
async function loadOverview() {
  const data = await api('/api/analysis/overview');
  categories = data.categories;
  const o = data.overview; const reviewed = o.analyzed - (o.reviews.PENDING || 0);
  $('nav-flagged').textContent = fmt(o.flagged);
  $('tab-pending').textContent = fmt(o.flagged_pending); $('tab-pending').hidden = !o.flagged_pending;
  $('analysis-meta').textContent = o.analyzed ? `${fmt(o.analyzed)} bài đã phân tích · ${fmt(o.keywords)} từ khóa đang bật · ${fmt(o.sources)} nguồn · cập nhật ${day(o.last_assessed, true)}` : 'Chưa có dữ liệu. Thu thập hoặc nhập dataset để bắt đầu.';
  renderKpis(o, reviewed);
  renderDistribution(o);
  renderBars($('rule-bars'), data.signals.rules.slice(0, 10), r => [`${r.rule_id}`, r.name], r => r.posts, r => r.flagged_posts,
    r => ({q: r.rule_id}), r => `${r.rule_id} · ${r.name} (${RULE_CATEGORIES[r.category] || r.category}): ${r.posts} bài, ${r.flagged_posts} gắn cờ`, 'Chưa có rule nào kích hoạt.');
  renderBars($('category-bars'), data.signals.categories, c => ['', c.label], c => c.posts, null,
    c => ({category: c.category}), c => `${c.label}: ${c.posts} bài`, 'Chưa có dữ liệu.');
  timelineDays = data.timeline.days.slice(-31); renderTimeline();
  $('phrases').replaceChildren(...(data.phrases.length ? data.phrases.map(p => {
    const chip = el('button', 'chip', p.phrase); chip.type = 'button'; chip.append(el('b', '', p.posts)); chip.title = `Xem ${p.posts} bài chứa “${p.phrase}”`;
    chip.addEventListener('click', () => goto('review', {q: p.phrase})); return chip;
  }) : [empty('Cần ít nhất 2 bài gắn cờ có chung cụm từ.')]));
  renderReused(data.reused);
  const select = $('filter-category');
  if (select.options.length === 1) for (const [key, label] of Object.entries(categories)) select.append(new Option(label, key));
  fillSettings(data.settings);
  await loadPriority();
}
function kpi(label, value, sub, tone, filters, extra) {
  const card = el(filters ? 'button' : 'div', `kpi ${tone || ''}`); if (filters) { card.type = 'button'; card.addEventListener('click', () => goto('review', filters)); }
  const head = el('span', 'kpi-label'); if (tone) head.append(el('i')); head.append(label);
  card.append(head, el('strong', 'kpi-value', fmt(value)), el('span', 'kpi-sub', sub)); if (extra) card.append(extra); return card;
}
function renderKpis(o, reviewed) {
  const meter = el('div', 'meter'); const fill = el('span'); fill.style.width = pct(o.flagged - o.flagged_pending, o.flagged); meter.append(fill);
  meter.setAttribute('role', 'img'); meter.setAttribute('aria-label', `Đã duyệt ${pct(o.flagged - o.flagged_pending, o.flagged)} bài gắn cờ`);
  $('kpis').replaceChildren(
    kpi('Bài đã phân tích', o.analyzed, `${fmt(o.comments)} bình luận · ${fmt(o.posts_with_ocr)} bài có OCR`, '', {}),
    kpi('Gắn cờ', o.flagged, `${pct(o.flagged, o.analyzed)} số bài · mức Trung bình trở lên`, 'flag', {level: 'FLAGGED'}),
    kpi('Rủi ro cao', o.levels.HIGH, `${pct(o.levels.HIGH, o.analyzed)} số bài`, 'high', {level: 'HIGH'}),
    kpi('Chờ duyệt', o.flagged_pending, `Đã duyệt ${fmt(o.flagged - o.flagged_pending)} / ${fmt(o.flagged)} bài gắn cờ`, 'pending', {level: 'FLAGGED', review: 'PENDING'}, meter));
}
function renderDistribution(o) {
  const bar = el('div', 'dist-bar'); bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', `Cao ${o.levels.HIGH}, Trung bình ${o.levels.MEDIUM}, Thấp ${o.levels.LOW}`);
  const legend = el('div', 'dist-legend');
  for (const key of ['HIGH', 'MEDIUM', 'LOW']) {
    const value = o.levels[key]; const cls = `bg-${key.toLowerCase()}`;
    if (value) { const seg = el('span', cls); seg.style.flex = value; seg.title = `${LEVELS[key]}: ${value}`; bar.append(seg); }
    const item = el('button', 'dist-item'); item.type = 'button'; const label = el('span'); label.append(el('i', `dot ${cls}`), LEVELS[key]);
    item.append(label, el('strong', '', fmt(value)), el('small', '', pct(value, o.analyzed))); item.addEventListener('click', () => goto('review', {level: key})); legend.append(item);
  }
  const reviews = o.reviews || {};
  const facts = [['Xác nhận lừa đảo', reviews.CONFIRMED_SCAM || 0], ['Báo nhầm', reviews.FALSE_POSITIVE || 0], ['Cần thêm thông tin', reviews.NEEDS_INFO || 0],
    ['Bài cảnh báo / tin tức', o.warning_posts], ['Trùng nội dung', o.exact_duplicates], ['Ảnh đã lưu', o.images]];
  const grid = el('div', 'facts-grid');
  for (const [label, value] of facts) { const row = el('div', 'fact'); row.append(el('span', '', label), el('strong', '', fmt(value))); grid.append(row); }
  $('distribution').replaceChildren(bar, legend, grid);
}
async function loadPriority() {
  const data = await api('/api/analysis/posts?level=FLAGGED&review=PENDING&limit=5');
  $('priority').replaceChildren(...(data.items.length ? data.items.map(item => {
    const row = el('button', 'priority-item'); row.type = 'button'; const body = el('span', 'priority-body');
    const head = el('span', 'risk-head'); head.append(badge(item.risk_level), el('span', 'cat', item.category_label));
    body.append(head, el('span', 'preview', item.preview || '(Không có nội dung chữ)'));
    row.append(scoreBox(item), body); row.addEventListener('click', guard(() => openPost(item.post_id))); return row;
  }) : [empty('Không còn bài gắn cờ nào chờ duyệt.')]));
}
function renderBars(box, items, label, value, strong, filters, title, emptyText) {
  const max = Math.max(1, ...items.map(value));
  box.replaceChildren(...(items.length ? items.map(item => {
    const row = el('button', 'bar-row'); row.type = 'button'; row.title = title(item);
    const [code, text] = label(item); const name = el('span', 'bar-label'); if (code) name.append(el('b', '', code)); name.append(text);
    const total = value(item); const flagged = strong ? strong(item) : total; const track = el('span', 'bar-track');
    if (flagged) { const part = el('span', 'bar-strong'); part.style.width = `${flagged / max * 100}%`; track.append(part); }
    if (total - flagged) { const rest = el('span', 'bar-soft'); rest.style.width = `${(total - flagged) / max * 100}%`; track.append(rest); }
    const number = el('span', 'bar-value', `${fmt(total)} bài`); if (strong) number.append(el('small', '', ` · ${fmt(flagged)} cờ`));
    row.append(name, track, number); row.addEventListener('click', () => goto('review', filters(item))); return row;
  }) : [empty(emptyText)]));
}
function renderReused(reused) {
  const table = el('table'); const body = el('tbody');
  for (const [field, label] of [['domains', 'Tên miền'], ['phones', 'SĐT'], ['urls', 'Liên kết']]) {
    for (const item of (reused[field] || []).slice(0, 6)) {
      const tr = el('tr'); const value = el('td'); value.append(el('span', 'tag', label), ' ', el('code', '', item.value));
      const posts = el('td', 'post-links'); for (const id of item.post_ids.slice(0, 6)) { const b = el('button', 'link-button', `#${id}`); b.type = 'button'; b.addEventListener('click', guard(() => openPost(id))); posts.append(b); }
      tr.append(value, el('td', 'num', `${item.posts} bài`), posts); body.append(tr);
    }
  }
  table.append(body); const wrap = el('div', 'table-wrap'); wrap.append(table);
  $('reused').replaceChildren(body.childElementCount ? wrap : empty('Không có liên kết hoặc SĐT nào xuất hiện ở nhiều bài.'));
}

// Biểu đồ cột theo ngày: cột xám = tổng số bài, cột chồng bên cạnh = bài gắn cờ theo nhóm. Một trục y.
function svg(tag, attrs) { const node = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); return node; }
// Trục y chia 4 nấc nguyên, mỗi nấc là 1/2/5 × 10^k để nhãn dễ đọc.
function niceMax(value) {
  const raw = Math.max(1, value / 4); const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map(m => m * power).find(s => s >= raw); return Math.max(4, step * 4);
}
function renderTimeline() {
  $('timeline-legend').replaceChildren(...[['total', 'Tổng số bài'], ...SERIES.map(([, cls, label]) => [cls, `Gắn cờ: ${label.toLowerCase()}`])].map(([cls, label]) => { const s = el('span'); s.append(el('i', `sw sw-${cls}`), label); return s; }));
  const box = $('timeline'); const days = timelineDays;
  if (!days.length) { box.replaceChildren(empty('Chưa có dữ liệu theo ngày.')); return; }
  const width = box.clientWidth || 600, height = box.clientHeight || 240, left = 34, bottom = 24, top = 6;
  const max = niceMax(Math.max(1, ...days.map(d => Math.max(d.collected, d.posted, d.flagged))));
  const plotH = height - bottom - top, slot = (width - left) / days.length;
  const barW = Math.max(3, Math.min(18, (slot - 8) / 2));
  const y = v => top + plotH - v / max * plotH;
  const root = svg('svg', {viewBox: `0 0 ${width} ${height}`, 'aria-hidden': 'true'});
  for (let i = 0; i <= 4; i++) { const v = max / 4 * i; root.append(svg('line', {class: 'grid', x1: left, x2: width, y1: y(v), y2: y(v)})); const t = svg('text', {class: 'axis', x: left - 8, y: y(v) + 4, 'text-anchor': 'end'}); t.textContent = Number.isInteger(v) ? v : v.toFixed(1); root.append(t); }
  const every = Math.ceil(days.length / Math.max(1, Math.floor((width - left) / 54)));
  const tip = $('chart-tip');
  days.forEach((d, i) => {
    const cx = left + slot * i + slot / 2; const total = Math.max(d.collected, d.posted);
    // Vẽ cột theo pixel: đáy cố định ở trục, đầu cột bo 3px; các đoạn chồng cách nhau 2px.
    const bar = (x, basePx, h, cls, roundTop) => {
      if (h <= 0.5) return; const y0 = top + plotH - basePx, y1 = y0 - h, r = roundTop ? Math.min(3, barW / 2, h) : 0;
      root.append(svg('path', {class: cls, d: `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + barW - r} Q${x + barW},${y1} ${x + barW},${y1 + r} V${y0} Z`}));
    };
    bar(cx - barW - 1, 0, total / max * plotH, 'total', true);
    const parts = SERIES.filter(([key]) => d[key]); let basePx = 0;
    parts.forEach(([key, cls], index) => { const h = d[key] / max * plotH, gap = index ? 2 : 0; bar(cx + 1, basePx + gap, h - gap, cls, index === parts.length - 1); basePx += h; });
    if (i % every === 0) { const t = svg('text', {class: 'axis', x: cx, y: height - 6, 'text-anchor': 'middle'}); const [yy, mm, dd] = d.day.split('-'); t.textContent = `${dd}/${mm}`; t.dataset.year = yy; root.append(t); }
    const hit = svg('rect', {class: 'hit', x: left + slot * i, y: top, width: slot, height: plotH, rx: 4});
    hit.addEventListener('mousemove', event => {
      const [yy, mm, dd] = d.day.split('-'); tip.replaceChildren(el('strong', '', `${dd}/${mm}/${yy}`));
      for (const [cls, label, value] of [['total', 'Tổng số bài', total], ...SERIES.map(([key, c, l]) => [c, `Gắn cờ: ${l.toLowerCase()}`, d[key]])]) { const row = el('div', 'tip-row'); const name = el('span'); name.append(el('i', `sw sw-${cls}`), label); row.append(name, el('b', '', fmt(value))); tip.append(row); }
      tip.hidden = false; const x = Math.min(event.clientX + 14, innerWidth - tip.offsetWidth - 8); tip.style.left = `${x}px`; tip.style.top = `${event.clientY + 14}px`;
    });
    hit.addEventListener('mouseleave', () => { tip.hidden = true; });
    root.append(hit);
  });
  box.replaceChildren(root);
}

// ---------- Điều hướng ----------
const loaders = {overview: loadOverview, check: async () => $('check-form').text.focus(), review: loadPosts, actors: loadActors, similar: loadSimilar, rules: loadRules, keywords: async () => { await loadKeywords(); await loadSources(); }, import: loadImports};
let current = 'overview';
function showTab(name) {
  current = loaders[name] ? name : 'overview';
  document.querySelectorAll('[role=tab]').forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.tab === current)));
  document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = panel.id !== `tab-${current}`; });
  if (location.hash.slice(1) !== current) history.replaceState(null, '', `#${current}`);
  guard(loaders[current])();
}
function refreshAll() { guard(loadOverview)(); if (current !== 'overview') guard(loaders[current])(); }
async function pollState() {
  try {
    const state = await api('/api/state'); session.token = state.token; $('connection-error').hidden = true;
    showBrowserState(state.browser_ready);
    const job = state.current; const running = job && ['running', 'stopping'].includes(job.status) && job.action !== 'browser';
    $('job-pill').hidden = !running;
    if (running) $('job-pill').lastChild.textContent = `Đang thu thập ${job.completed}/${job.total}`;
  } catch { $('connection-error').hidden = false; }
}

renderIcons();
initTheme(() => renderTimeline());
initReview(() => guard(loadOverview)());
initConfig(refreshAll);
initCheck();
initActors(filters => goto('review', filters));
document.querySelectorAll('[role=tab]').forEach(tab => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
document.querySelector('[role=tablist]').addEventListener('keydown', event => {
  const tabs = [...document.querySelectorAll('[role=tab]')]; const index = tabs.indexOf(document.activeElement); if (index < 0) return;
  const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : null; if (next === null) return;
  const tab = tabs[(next + tabs.length) % tabs.length]; tab.focus(); showTab(tab.dataset.tab);
});
document.querySelectorAll('[data-goto]').forEach(button => button.addEventListener('click', () => goto(button.dataset.goto, {level: button.dataset.level, review: button.dataset.review})));
addEventListener('hashchange', () => { if (location.hash.slice(1) !== current) showTab(location.hash.slice(1)); });
let resizeTimer; addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderTimeline, 120); });
$('run-analysis').addEventListener('click', guard(async () => {
  const button = $('run-analysis'); button.disabled = true;
  try { const r = await api('/api/analysis/run', {force: true}); toast(`Đã phân tích lại ${fmt(r.analyzed)} bài.`); refreshAll(); } finally { button.disabled = false; }
}));
$('open-browser').addEventListener('click', guard(async () => { await api('/api/browser', {}); toast('Đang mở Chrome của project…'); }));
(async () => {
  await pollState(); setInterval(pollState, 5000);
  const first = location.hash.slice(1) || 'overview';
  if (first !== 'overview') guard(loadOverview)();  // số liệu ở sidebar và bộ lọc phân loại
  showTab(first);
  const shared = Number(new URLSearchParams(location.search).get('post'));
  if (shared) { history.replaceState(null, '', `/analysis#${current}`); guard(openPost)(shared); }
})();
