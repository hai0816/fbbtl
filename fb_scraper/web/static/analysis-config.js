// Các tab cấu hình: Nội dung tương tự, Rule & ngưỡng, Từ khóa & nguồn, Nhập dữ liệu.
import {$, api, el, fmt, toast} from './shell.js';
import {badge, day, empty, guard, openPost} from './analysis-review.js';

const RULE_CATEGORIES = {JOB: 'Tuyển dụng', INVESTMENT: 'Đầu tư', COMMON: 'Chung', COMBO: 'Kết hợp'};
const KW_GROUPS = {JOB: 'Tuyển dụng', INVESTMENT: 'Đầu tư', OTHER: 'Khác'};
const SOURCE_TYPES = {profile: 'Cá nhân', page: 'Trang', group: 'Nhóm', dataset: 'Dataset', keyword: 'Từ khóa'};
const STATUS = {ok: 'Ổn định', error: 'Lỗi', pending: 'Đang chờ'};
const SETTING_KEYS = ['warning_context_factor', 'income_per_day_threshold', 'return_per_month_threshold', 'similarity_threshold'];
let refreshAll = () => {}, rules = [];

// ---------- Nội dung tương tự ----------
export async function loadSimilar() {
  const threshold = Number($('sim-threshold').value).toFixed(2); $('sim-value').textContent = threshold;
  const data = await api(`/api/analysis/similar?threshold=${threshold}&flagged=${$('sim-flagged').checked ? 1 : 0}`);
  $('sim-note').textContent = `${fmt(data.clusters.length)} cụm từ ${fmt(data.documents)} bài. ${data.note}`;
  $('clusters').replaceChildren(...(data.clusters.length ? data.clusters.map(c => {
    const card = el('article', 'panel cluster'); const title = el('h3', '', `Cụm ${c.cluster_id}`);
    title.append(el('span', 'tag', `${c.size} bài`), el('span', 'tag', `${c.flagged} gắn cờ`), el('span', 'tag', `tương đồng tối đa ${c.max_similarity}`));
    const chips = el('div', 'chips'); chips.append(...c.top_terms.map(t => el('span', 'chip', t)));
    card.append(title, chips);
    if (c.shared_domains.length || c.shared_phones.length) card.append(el('p', 'muted', `Dùng chung: ${[...c.shared_domains, ...c.shared_phones].join(', ')}`));
    for (const post of c.posts) { const row = el('button', 'cluster-post'); row.type = 'button'; row.append(badge(post.risk_level), el('span', 'preview', `#${post.post_id} ${post.preview}`)); row.addEventListener('click', guard(() => openPost(post.post_id))); card.append(row); }
    return card;
  }) : [empty('Không có cụm nào ở ngưỡng này. Thử giảm ngưỡng.')]));
}

// ---------- Rule & ngưỡng ----------
export function fillSettings(s) {
  const form = $('settings-form');
  form.medium.value = s.risk_thresholds.medium; form.high.value = s.risk_thresholds.high;
  for (const key of SETTING_KEYS) form[key].value = s[key];
  $('sim-threshold').value = s.similarity_threshold; $('sim-value').textContent = Number(s.similarity_threshold).toFixed(2);
}
function renderRules() {
  const query = $('rule-filter').value.trim().toLowerCase();
  const shown = rules.filter(r => !query || `${r.rule_id} ${r.name} ${r.description} ${r.group}`.toLowerCase().includes(query));
  $('rules-table').tBodies[0].replaceChildren(...shown.map(r => {
    const tr = el('tr'); const weight = el('input'); weight.type = 'number'; weight.min = 0; weight.max = 100; weight.value = r.weight; weight.setAttribute('aria-label', `Trọng số ${r.rule_id}`);
    weight.addEventListener('change', guard(async () => { await api('/api/analysis/rules', {rule_id: r.rule_id, weight: Number(weight.value)}); toast(`Đã đổi trọng số ${r.rule_id} và chấm lại.`); refreshAll(); }));
    const toggle = el('input'); toggle.type = 'checkbox'; toggle.checked = r.enabled; toggle.setAttribute('aria-label', `Bật ${r.rule_id}`);
    toggle.addEventListener('change', guard(async () => { await api('/api/analysis/rules', {rule_id: r.rule_id, enabled: toggle.checked}); toast(`${toggle.checked ? 'Đã bật' : 'Đã tắt'} ${r.rule_id} và chấm lại.`); refreshAll(); }));
    const name = el('td'); name.append(el('strong', '', r.name), el('div', 'muted', r.description));
    const cells = [el('td', 'mono', r.rule_id), name, el('td', '', RULE_CATEGORIES[r.category] || r.category), el('td', 'mono', r.group), el('td', 'num', r.priority), el('td', 'num'), el('td')];
    cells[5].append(weight); cells[6].append(toggle); tr.append(...cells); return tr;
  }));
  if (!shown.length) { const tr = el('tr'); const td = el('td', 'empty', 'Không có rule phù hợp.'); td.colSpan = 7; tr.append(td); $('rules-table').tBodies[0].append(tr); }
}
export async function loadRules() {
  const data = await api('/api/analysis/rules'); fillSettings(data.settings); rules = data.rules; renderRules();
  const ev = await api('/api/analysis/evaluate'); const box = $('evaluation'); box.replaceChildren();
  box.append(el('p', '', `${fmt(ev.samples)} mẫu có nhãn — lừa đảo ${fmt(ev.positives)}, bình thường ${fmt(ev.negatives)} (từ duyệt ${fmt(ev.sources.review)}, từ dataset ${fmt(ev.sources.dataset)}).`));
  if (ev.note) box.append(el('p', 'muted', ev.note));
  if (!ev.curve.length) return;
  const current = [ev.current.medium, ev.current.high];
  const table = el('table', 'compact'); const head = el('tr'); ['Ngưỡng', 'Precision', 'Recall', 'F1', 'FPR'].forEach((h, i) => head.append(el('th', i ? 'num' : '', h))); table.append(el('thead')); table.tHead.append(head);
  const body = el('tbody');
  ev.curve.filter(row => row.threshold % 10 === 0 || current.includes(row.threshold)).forEach(row => { const tr = el('tr', current.includes(row.threshold) ? 'current' : ''); [row.threshold, row.precision, row.recall, row.f1, row.fpr].forEach((v, i) => tr.append(el('td', i ? 'num' : '', v))); body.append(tr); });
  table.append(body);
  const apply = el('button', 'secondary-button', `Áp dụng ngưỡng gợi ý ${ev.suggested.medium} / ${ev.suggested.high}`); apply.type = 'button';
  apply.addEventListener('click', guard(async () => { await api('/api/analysis/settings', {key: 'risk_thresholds', value: ev.suggested}); toast('Đã áp dụng ngưỡng và chấm lại.'); refreshAll(); }));
  const wrap = el('div', 'table-wrap'); wrap.append(table); box.append(wrap, apply);
}

// ---------- Từ khóa, đề xuất, nguồn ----------
function keywordStat(p) {
  if (!p || (!p.runs && !p.collected)) return {text: 'Chưa dùng', tone: ''};
  const yieldText = p.yield !== null ? ` · ${Math.round(p.yield * 100)}% gắn cờ` : '';
  const text = p.runs ? `${p.runs} lượt · lưu ${p.collected}/${p.seen} · gắn cờ ${p.flagged}${yieldText}` : `lưu ${p.collected} · gắn cờ ${p.flagged}${yieldText}`;
  const tone = p.yield !== null && p.collected >= 3 ? (p.yield >= 0.3 ? 'good' : p.yield < 0.1 ? 'poor' : '') : '';
  return {text, tone};
}
export async function loadKeywords() {
  const data = await api('/api/analysis/keywords'); const perf = Object.fromEntries(data.performance.map(item => [item.id, item]));
  $('kw-count').textContent = `${fmt(data.keywords.filter(k => k.enabled).length)} / ${fmt(data.keywords.length)} đang bật`;
  $('keywords').replaceChildren(...Object.entries(KW_GROUPS).map(([key, label]) => {
    const items = data.keywords.filter(item => item.category === key);
    const block = el('div', 'kw-group'); const title = el('h3', '', label); title.append(el('span', '', fmt(items.length))); block.append(title);
    if (!items.length) block.append(empty('Chưa có từ khóa.'));
    for (const k of items) {
      const row = el('div', `kw ${k.enabled ? '' : 'off'}`); const toggle = el('input'); toggle.type = 'checkbox'; toggle.checked = !!k.enabled; toggle.setAttribute('aria-label', `Bật từ khóa ${k.keyword}`);
      toggle.addEventListener('change', guard(async () => { await api('/api/analysis/keywords', {action: 'toggle', id: k.id, enabled: toggle.checked}); loadKeywords(); }));
      const remove = el('button', 'text-button', 'Xóa'); remove.type = 'button'; remove.setAttribute('aria-label', `Xóa từ khóa ${k.keyword}`);
      remove.addEventListener('click', guard(async () => { await api('/api/analysis/keywords', {action: 'remove', id: k.id}); toast(k.origin === 'default' ? 'Từ khóa mặc định được tắt thay vì xóa.' : 'Đã xóa từ khóa.'); loadKeywords(); }));
      const stat = keywordStat(perf[k.id]); const name = el('div', 'kw-name'); name.append(el('span', '', k.keyword));
      if (k.origin !== 'default') name.append(el('small', '', k.origin === 'expansion' ? 'Từ đề xuất' : 'Tự thêm'));
      const statEl = el('span', `kw-stat ${stat.tone}`, stat.text); statEl.title = 'Tỷ lệ bài gắn cờ (Trung bình/Cao) trên số bài đã lưu';
      row.append(toggle, name, statEl, remove); block.append(row);
    }
    return block;
  }));
  $('suggestions').replaceChildren(...data.suggestions.map(s => {
    const row = el('div', 'suggestion'); row.append(el('strong', '', s.phrase), el('span', 'muted', `${s.support} bài · ${KW_GROUPS[s.category] || s.category}`));
    for (const [label, accept] of [['Chấp nhận', true], ['Bỏ qua', false]]) { const b = el('button', accept ? 'secondary-button' : 'text-button', label); b.type = 'button'; b.addEventListener('click', guard(async () => { await api('/api/analysis/suggestions', {id: s.id, accept}); toast(accept ? `Đã thêm “${s.phrase}”.` : 'Đã bỏ qua đề xuất.'); loadKeywords(); })); row.append(b); }
    return row;
  }));
}
export async function loadSources() {
  const data = await api('/api/analysis/sources'); const body = $('sources-table').tBodies[0];
  body.replaceChildren(...data.sources.map(s => {
    const tr = el('tr'); const toggle = el('input'); toggle.type = 'checkbox'; toggle.checked = !!s.monitor_enabled; toggle.disabled = s.source_type === 'dataset'; toggle.setAttribute('aria-label', `Theo dõi ${s.source_label}`);
    toggle.addEventListener('change', guard(async () => { await api('/api/analysis/sources', {id: s.id, enabled: toggle.checked}); loadSources(); }));
    const name = el('td'); name.append(el('div', '', s.source_label), el('div', 'muted mono', s.source_ref || ''));
    const status = el('td', `status-${s.collection_status || 'none'}`, STATUS[s.collection_status] || s.collection_status || '—'); if (s.last_error) status.title = s.last_error;
    const cells = [name, el('td', '', SOURCE_TYPES[s.source_type] || s.source_type), el('td', '', day(s.last_checked_at, true)), status, el('td', 'num', fmt(s.post_count)), el('td', 'num', fmt(s.flagged_count)), el('td')];
    cells[6].append(toggle); tr.append(...cells); return tr;
  }));
  if (!data.sources.length) { const tr = el('tr'); const td = el('td', 'empty', 'Chưa có nguồn nào.'); td.colSpan = 7; tr.append(td); body.append(tr); }
}

// ---------- Nhập dữ liệu ----------
export async function loadImports() {
  const data = await api('/api/analysis/imports'); const body = $('imports-table').tBodies[0];
  body.replaceChildren(...data.batches.map(b => { const tr = el('tr'); [day(b.imported_at, true), b.file_name, (b.file_format || '').toUpperCase()].forEach(v => tr.append(el('td', '', v))); [b.total_rows, b.inserted, b.updated, b.duplicates, JSON.parse(b.errors || '[]').length].forEach(v => tr.append(el('td', 'num', fmt(v)))); return tr; }));
  if (!data.batches.length) { const tr = el('tr'); const td = el('td', 'empty', 'Chưa nhập dataset nào.'); td.colSpan = 8; tr.append(td); body.append(tr); }
}
function showFile() { const file = $('import-form').file.files[0]; $('file-name').textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : 'Chọn tệp hoặc kéo thả vào đây'; }

function collectOptions(f) { return {max_posts: Number(f.max_posts.value), max_comments: Number(f.max_comments.value), ocr: f.ocr.checked, scam_filter: f.scam_filter.checked}; }

export function initConfig(onRefresh) {
  refreshAll = onRefresh;
  $('sim-threshold').addEventListener('change', guard(loadSimilar));
  $('sim-threshold').addEventListener('input', () => { $('sim-value').textContent = Number($('sim-threshold').value).toFixed(2); });
  $('sim-flagged').addEventListener('change', guard(loadSimilar));
  $('rule-filter').addEventListener('input', renderRules);
  $('settings-form').addEventListener('submit', guard(async event => {
    event.preventDefault(); const form = event.target;
    await api('/api/analysis/settings', {key: 'risk_thresholds', value: {medium: Number(form.medium.value), high: Number(form.high.value)}});
    for (const key of SETTING_KEYS) await api('/api/analysis/settings', {key, value: Number(form[key].value)});
    toast('Đã lưu cấu hình và chấm lại.'); refreshAll();
  }));
  $('keyword-form').addEventListener('submit', guard(async event => { event.preventDefault(); const f = event.target; const r = await api('/api/analysis/keywords', {action: 'add', keyword: f.keyword.value, category: f.category.value}); toast(r.created ? 'Đã thêm từ khóa.' : 'Từ khóa đã có, đã bật lại.'); f.keyword.value = ''; loadKeywords(); }));
  $('expand-form').addEventListener('submit', guard(async event => { event.preventDefault(); const f = event.target; const r = await api('/api/analysis/expand', {text: f.text.value, level: f.level.value}); await loadKeywords(); toast(r.suggestions.length ? `Có ${r.suggestions.length} đề xuất; duyệt bên dưới.` : 'Không tìm thấy cụm từ mới.'); }));
  $('collect-form').addEventListener('submit', guard(async event => { event.preventDefault(); const f = event.target; const r = await api('/api/analysis/collect', {target: 'keywords', category: f.category.value, recent_first: f.recent_first.checked, ...collectOptions(f)}); toast(`Đã bắt đầu thu thập ${r.sources} từ khóa.`); }));
  $('collect-monitored').addEventListener('click', guard(async () => { const r = await api('/api/analysis/collect', {target: 'monitored', ...collectOptions($('collect-form'))}); toast(`Đã bắt đầu thu thập ${r.sources} nguồn theo dõi.`); }));
  $('source-form').addEventListener('submit', guard(async event => { event.preventDefault(); const f = event.target; await api('/api/analysis/sources', {action: 'add', url: f.url.value, kind: f.kind.value}); toast('Đã thêm nguồn và bật theo dõi.'); f.reset(); loadSources(); }));
  const zone = $('dropzone'); const input = $('import-form').file;
  input.addEventListener('change', showFile);
  ['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, () => zone.classList.add('drag')));
  ['dragleave', 'drop'].forEach(name => zone.addEventListener(name, () => zone.classList.remove('drag')));
  $('import-form').addEventListener('submit', guard(async event => {
    event.preventDefault(); const f = event.target; const file = f.file.files[0]; if (!file) return;
    if (file.size > 20 * 1024 * 1024) throw new Error('Tệp lớn hơn 20 MB.');
    const buffer = new Uint8Array(await file.arrayBuffer()); let binary = '';
    for (let i = 0; i < buffer.length; i += 0x8000) binary += String.fromCharCode(...buffer.subarray(i, i + 0x8000));
    const button = f.querySelector('button[type=submit]'); button.disabled = true;
    try {
      const r = await api('/api/analysis/import', {file_name: file.name, data_base64: btoa(binary), dataset: f.dataset.value, format: f.format.value, table: f.table.value});
      const box = $('import-result'); box.hidden = false; box.replaceChildren(
        el('strong', '', `Dataset “${r.dataset}” (${r.format.toUpperCase()})`),
        el('div', '', `${fmt(r.total_rows)} dòng · thêm ${fmt(r.inserted)} · cập nhật ${fmt(r.updated)} · trùng ${fmt(r.duplicates)} · lỗi ${fmt(r.errors.length)} · cảnh báo ${fmt(r.warnings.length)}`),
        el('div', '', `Đã phân tích ${fmt(r.analysis.analyzed)} bài — Cao ${fmt(r.analysis.levels.HIGH)}, Trung bình ${fmt(r.analysis.levels.MEDIUM)}, Thấp ${fmt(r.analysis.levels.LOW)}`));
      const issues = [...r.errors.map(e => `Dòng ${e.row}: ${e.error}`), ...r.warnings.map(e => `Dòng ${e.row}: ${e.warning}`)];
      if (issues.length) box.append(el('pre', '', issues.join('\n')));
      toast('Đã nhập và phân tích dataset.'); f.reset(); showFile(); refreshAll();
    } finally { button.disabled = false; }
  }));
}
