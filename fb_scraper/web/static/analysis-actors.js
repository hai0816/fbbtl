// Tab "Tác giả & nơi đăng": xếp hạng tài khoản và nhóm/trang theo số bài bị gắn cờ. Dữ liệu gán bằng textContent.
import {$, api, el, fmt} from './shell.js';
import {empty, guard} from './analysis-review.js';

const SHOWN = 50;
let data = null, openPosts = () => {};

function safeUrl(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
function pct(value) { return `${Math.round(value * 100)}%`; }

function nameCell(row, kind) {
  const td = el('td', 'actor-name'); const title = el('div', 'actor-title'); title.append(el('strong', '', row.label));
  const href = safeUrl(row.url);
  if (href) {
    const link = el('a', 'actor-link', '↗'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.title = 'Mở trên Facebook'; link.setAttribute('aria-label', `Mở ${row.label} trên Facebook`);
    link.addEventListener('click', event => event.stopPropagation()); title.append(link);
  }
  const sub = el('div', 'actor-sub');
  if (kind === 'places') sub.append(el('span', 'type-chip', row.type_label));
  if (row.keywords.length) sub.append(el('span', '', `Từ khóa: ${row.keywords.join(', ')}`));
  if (kind === 'authors' && row.phones.length) sub.append(el('span', '', `SĐT: ${row.phones.join(', ')}`));
  td.append(title); if (sub.childElementCount) td.append(sub);
  return td;
}
function rateCell(row) {
  const td = el('td', 'rate-cell'); const meter = el('span', 'rate-meter'); const fill = el('span');
  fill.style.width = pct(row.flag_rate); meter.append(fill); td.append(meter, el('span', 'num', pct(row.flag_rate))); return td;
}
function num(value, strong = false) { const td = el('td', 'num'); td.append(strong && value ? el('strong', '', fmt(value)) : fmt(value)); return td; }

function render(kind) {
  const query = $('actors-q').value.trim().toLowerCase(), flaggedOnly = $('actors-flagged').checked;
  const rows = data[kind].filter(row => (!flaggedOnly || row.flagged > 0) && (!query || row.label.toLowerCase().includes(query)));
  const body = $(`${kind}-table`).tBodies[0]; body.replaceChildren();
  for (const row of rows.slice(0, SHOWN)) {
    const tr = el('tr', 'actor-row'); tr.tabIndex = 0;
    tr.title = `Xem ${row.posts} bài của ${row.label}`;
    const open = () => openPosts({ids: row.post_ids, scope: `${kind === 'places' ? 'Nơi đăng' : 'Tác giả'}: ${row.label}`});
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    tr.append(nameCell(row, kind), num(row.posts), num(row.flagged, true), num(row.high), num(row.confirmed));
    if (kind === 'places') tr.append(rateCell(row), num(row.authors));
    else tr.append(num(row.avg_score), num(row.max_score));
    body.append(tr);
  }
  const note = $(`${kind}-note`);
  note.replaceChildren(rows.length ? `Hiển thị ${fmt(Math.min(rows.length, SHOWN))} / ${fmt(rows.length)}` : empty(flaggedOnly ? 'Chưa có mục nào có bài gắn cờ.' : 'Chưa có dữ liệu.'));
}

function renderAll() {
  if (!data) return;
  const s = data.summary;
  $('actors-summary').textContent = `${fmt(s.authors)} tác giả · ${fmt(s.places)} nơi đăng · ${fmt(s.repeat_authors)} tác giả và ${fmt(s.repeat_places)} nơi đăng có từ 2 bài gắn cờ`;
  render('places'); render('authors');
}

export async function loadActors() {
  const min = $('actors-min').value;
  data = await api(`/api/analysis/actors?min_posts=${min}`);
  for (const kind of ['places', 'authors']) $(`export-${kind}`).href = `/api/analysis/actors/export?kind=${kind}&min_posts=${min}`;
  renderAll();
}

export function initActors(onOpenPosts) {
  openPosts = onOpenPosts;
  $('actors-min').addEventListener('change', guard(loadActors));
  $('actors-flagged').addEventListener('change', renderAll);
  let timer; $('actors-q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(renderAll, 200); });
}
