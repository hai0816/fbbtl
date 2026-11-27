// Tab "Duyệt bài": lọc, sắp xếp, phân trang và hộp thoại duyệt. Dữ liệu bài viết luôn gán bằng textContent.
import {$, api, el, fmt, toast} from './shell.js';

export const LEVELS = {HIGH: 'Cao', MEDIUM: 'Trung bình', LOW: 'Thấp'};
export const REVIEWS = {PENDING: 'Chờ duyệt', CONFIRMED_SCAM: 'Xác nhận lừa đảo', FALSE_POSITIVE: 'Báo nhầm', NEEDS_INFO: 'Cần thêm thông tin'};
const PAGE = 20;
const FILTERS = {level: 'filter-level', category: 'filter-category', review: 'filter-review', sort: 'filter-sort', q: 'filter-q'};
let scope = null, offset = 0, total = 0, pageIds = [], currentId = null, request = 0, onChanged = () => {};

export const guard = action => async (...args) => { try { await action(...args); } catch (error) { toast(error.message); } };
export function badge(level) { return el('span', `level level-${(level || 'LOW').toLowerCase()}`, LEVELS[level] || level); }
export function scoreBox(item) { return el('span', `score score-${(item.risk_level || 'LOW').toLowerCase()}`, item.risk_score); }
export function reviewPill(status) { return el('span', `review-pill review-${status}`, REVIEWS[status] || status); }
export function day(value, withTime = false) {
  const d = new Date(value);
  if (!value || Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('vi-VN', withTime ? {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'} : {day: '2-digit', month: '2-digit', year: 'numeric'});
}
export function empty(text) { return el('p', 'empty', text); }

function filterParams() {
  const params = new URLSearchParams();
  for (const [key, id] of Object.entries(FILTERS)) { const value = $(id).value.trim(); if (value && !(key === 'sort' && value === 'score')) params.set(key, value); }
  if (scope) params.set('ids', scope.ids.join(','));
  return params;
}
export function setFilters(values = {}) {
  for (const [key, id] of Object.entries(FILTERS)) $(id).value = values[key] ?? (key === 'sort' ? 'score' : '');
  scope = values.ids ? {ids: values.ids, label: values.scope || `${values.ids.length} bài đã chọn`} : null;
  offset = 0;
}

export async function loadPosts() {
  const sequence = ++request;
  const params = filterParams();
  $('export-filtered').href = `/api/analysis/export?${params}`;
  $('filter-reset').hidden = ![...params.keys()].length;
  $('filter-scope').hidden = !scope; $('filter-scope').textContent = scope ? scope.label : '';
  params.set('offset', offset); params.set('limit', PAGE);
  const data = await api(`/api/analysis/posts?${params}`);
  if (sequence !== request) return;
  total = data.total; pageIds = data.items.map(item => item.post_id);
  $('risk-summary').textContent = `${fmt(total)} bài phù hợp`;
  $('risk-list').replaceChildren(...(data.items.length ? data.items.map(riskRow) : [empty('Không có bài phù hợp bộ lọc.')]));
  $('risk-page').textContent = total ? `${fmt(offset + 1)}–${fmt(Math.min(offset + PAGE, total))} / ${fmt(total)}` : '';
  $('risk-prev').disabled = offset === 0; $('risk-next').disabled = offset + PAGE >= total;
}
function riskRow(item) {
  const row = el('button', 'risk-row'); row.type = 'button';
  const body = el('span', 'risk-body'); const head = el('span', 'risk-head');
  head.append(badge(item.risk_level), el('span', 'cat', item.category_label), reviewPill(item.review_status));
  const meta = el('span', 'risk-meta'); meta.append(el('span', '', item.author || item.source_label || 'Chưa rõ tác giả'));
  if (item.matched_rules.length) { const rules = el('span', 'rules'); item.matched_rules.forEach(id => rules.append(el('span', 'rule-chip', id))); meta.append(rules); }
  body.append(head, el('span', 'preview', item.preview || '(Không có nội dung chữ)'), meta);
  row.append(scoreBox(item), body, el('span', 'risk-date', day(item.posted_at || item.collected_at)));
  row.addEventListener('click', guard(() => openPost(item.post_id)));
  return row;
}

function highlight(text, terms) {
  const frag = document.createDocumentFragment(); const lower = text.toLowerCase(); let pos = 0;
  const hits = terms.map(t => [lower.indexOf(String(t).toLowerCase()), String(t).length]).filter(([i]) => i >= 0).sort((a, b) => a[0] - b[0]);
  for (const [index, length] of hits) { if (index < pos) continue; frag.append(text.slice(pos, index), el('mark', '', text.slice(index, index + length))); pos = index + length; }
  frag.append(text.slice(pos)); return frag;
}
function safeLink(url, text) {
  try { const parsed = new URL(url); if (!['http:', 'https:'].includes(parsed.protocol)) return null; } catch { return null; }
  const link = el('a', 'secondary-button', text); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
}
function updateNav() {
  const index = pageIds.indexOf(currentId);
  const known = index >= 0;
  $('detail-prev').hidden = $('detail-next').hidden = !known;
  $('detail-prev').disabled = !known || (index === 0 && offset === 0);
  $('detail-next').disabled = !known || (index === pageIds.length - 1 && offset + PAGE >= total);
  $('detail-kicker').textContent = known ? `Bài ${fmt(offset + index + 1)} / ${fmt(total)}` : 'Chi tiết đánh giá';
}
async function step(direction) {
  const index = pageIds.indexOf(currentId); if (index < 0) return;
  let target = index + direction;
  if (target < 0 && offset > 0) { offset -= PAGE; await loadPosts(); target = pageIds.length - 1; }
  else if (target >= pageIds.length && offset + PAGE < total) { offset += PAGE; await loadPosts(); target = 0; }
  if (pageIds[target] !== undefined) await openPost(pageIds[target]);
}

export function renderEvidence(box, matches) {
  for (const m of matches) {
    const card = el('div', `evidence ${m.counted ? '' : 'not-counted'}`);
    const head = el('div', 'ev-head'); head.append(el('span', '', `${m.rule_id} · ${m.name}`), el('span', 'ev-points', m.counted ? `+${m.points}${m.multiplier !== 1 ? ` (×${m.multiplier} ngữ cảnh cảnh báo)` : ''}` : 'Không cộng (trùng nhóm)'));
    card.append(head, el('div', 'muted', m.reason));
    for (const ev of m.evidence) { const quote = el('blockquote'); quote.append(el('span', 'tag', ({ocr: 'OCR', post: 'Bài', rules: 'Kết hợp', features: 'Đặc trưng'})[ev.source] || ev.source), ' ', highlight(ev.text, ev.terms)); card.append(quote); }
    box.append(card);
  }
  if (!matches.length) box.append(empty('Không có rule nào kích hoạt.'));
}
export async function openPost(id) {
  const p = await api(`/api/analysis/posts/${id}`);
  currentId = p.post_id;
  $('detail-title').textContent = p.author || p.source_label || `Bài #${p.post_id}`;
  const box = $('post-detail'); box.replaceChildren();
  const summary = el('div', 'detail-summary');
  summary.append(scoreBox(p), badge(p.risk_level), el('span', 'cat', p.category_label), reviewPill(p.review_status), el('span', 'spacer'));
  const link = safeLink(p.url, 'Mở bài gốc ↗'); if (link) summary.append(link);
  box.append(summary, el('p', 'explanation', p.explanation), reviewForm(p));

  box.append(el('h3', '', `Rule kích hoạt (${p.evidence.length})`));
  renderEvidence(box, p.evidence);

  const f = p.features, ctx = p.context, cc = p.comment_context;
  const facts = [['Ngày đăng', p.posted_at ? day(p.posted_at, true) : ''], ['Nguồn', p.source_label], ['Liên kết', f.urls.join(', ')], ['Số điện thoại', f.phones.join(', ')], ['Kênh liên hệ', f.contact_channels.join(', ')],
    ['Thu nhập công bố', f.money_rates.map(r => `${r.text} ≈ ${fmt(r.per_day)}đ/ngày`).join('; ')], ['Lợi nhuận công bố', f.return_rates.map(r => r.text).join('; ')],
    ['Từ cảnh báo', ctx.warning_terms.join(', ')], ['Kêu gọi liên hệ', ctx.promotion_terms.join(', ')], ['Trùng nội dung với', p.duplicate_of ? `#${p.duplicate_of}` : ''],
    ['Quảng cáo', f.is_sponsored === true ? 'Có (Được tài trợ)' : ''],
    ['Từ khóa tìm kiếm', f.search_keyword ? `“${f.search_keyword}” · liên quan ${Math.round((f.keyword_relevance ?? 0) * 100)}%${(f.keyword_relevance ?? 1) < 0.5 ? ' — có thể lệch chủ đề' : ''}` : ''],
    ['Bình luận', cc.total ? `${fmt(cc.total)} · nhắc lừa đảo ${cc.warning_mentions} · khen/“đã rút được” ${cc.shill_like} · mời inbox ${cc.solicitation} (không tính điểm)` : '']];
  const dl = el('dl', 'facts'); facts.filter(([, v]) => v).forEach(([k, v]) => dl.append(el('dt', '', k), el('dd', '', v)));
  if (dl.childElementCount) box.append(el('h3', '', 'Đặc trưng'), dl);
  box.append(el('h3', '', 'Nội dung'), el('div', 'content', p.content || '(Không có nội dung chữ)'));
  if (p.ocr_text) box.append(el('h3', '', 'Chữ trong ảnh (OCR)'), el('div', 'content', p.ocr_text));
  updateNav();
  if (!$('post-dialog').open) $('post-dialog').showModal();
  $('post-dialog').scrollTop = 0;
}
function reviewForm(p) {
  const form = el('form', 'review-box'); const options = el('div', 'review-options'); options.setAttribute('role', 'radiogroup'); options.setAttribute('aria-label', 'Kết quả duyệt');
  for (const [key, label] of Object.entries(REVIEWS)) { const option = el('label'); const input = el('input'); input.type = 'radio'; input.name = 'status'; input.value = key; input.checked = key === p.review_status; option.append(input, el('span', '', label)); options.append(option); }
  const row = el('div', 'review-form'); const note = el('input'); note.name = 'note'; note.placeholder = 'Ghi chú (tùy chọn)'; note.value = p.reviewer_note || ''; note.maxLength = 1000; note.setAttribute('aria-label', 'Ghi chú duyệt');
  const index = pageIds.indexOf(p.post_id); const hasNext = index >= 0 && (index < pageIds.length - 1 || offset + PAGE < total);
  const save = el('button', 'primary-button', hasNext ? 'Lưu và sang bài sau' : 'Lưu kết quả duyệt'); save.type = 'submit';
  row.append(note, save); form.append(options, row);
  form.addEventListener('submit', guard(async event => {
    event.preventDefault(); save.disabled = true;
    try {
      await api('/api/analysis/review', {post_id: p.post_id, status: form.status.value, note: note.value});
      toast(`Đã lưu: ${REVIEWS[form.status.value]}.`); onChanged(); await loadPosts();
      if (hasNext && pageIds.indexOf(p.post_id) >= 0) await step(1); else await openPost(p.post_id);
    } finally { save.disabled = false; }
  }));
  return form;
}

export function initReview(onReviewChanged) {
  onChanged = onReviewChanged;
  ['filter-level', 'filter-category', 'filter-review', 'filter-sort'].forEach(id => $(id).addEventListener('change', () => { offset = 0; guard(loadPosts)(); }));
  let timer; $('filter-q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { offset = 0; guard(loadPosts)(); }, 300); });
  $('filter-reset').addEventListener('click', () => { setFilters(); guard(loadPosts)(); });
  $('risk-prev').addEventListener('click', () => { offset = Math.max(0, offset - PAGE); guard(loadPosts)(); });
  $('risk-next').addEventListener('click', () => { offset += PAGE; guard(loadPosts)(); });
  $('detail-prev').addEventListener('click', guard(() => step(-1)));
  $('detail-next').addEventListener('click', guard(() => step(1)));
  $('close-dialog').addEventListener('click', () => $('post-dialog').close());
  $('post-dialog').addEventListener('click', event => { if (event.target === $('post-dialog')) $('post-dialog').close(); });
  $('post-dialog').addEventListener('keydown', event => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
    if (event.key === 'ArrowLeft' && !$('detail-prev').disabled && !$('detail-prev').hidden) { event.preventDefault(); guard(() => step(-1))(); }
    if (event.key === 'ArrowRight' && !$('detail-next').disabled && !$('detail-next').hidden) { event.preventDefault(); guard(() => step(1))(); }
  });
}
