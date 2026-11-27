import {element, renderPost} from './comments.js';
import {$, api, initTheme, renderIcons, session, showBrowserState, toast as showToast} from './shell.js';
const RISK_LABELS = {HIGH: 'Cao', MEDIUM: 'Trung bình', LOW: 'Thấp'};
const risks = new Map();

renderIcons();
initTheme();
const format = number => new Intl.NumberFormat('vi-VN').format(number);

const statuses = {idle:'Sẵn sàng', running:'Đang thu thập', stopping:'Đang dừng', completed:'Hoàn tất', cancelled:'Đã dừng', failed:'Có lỗi'};
let busy = false, submitting = false, offset = 0, total = 0, searchTimer, postRequest = 0;
let lastFingerprint = '', lastHistory = '';

function updateInput() {
  const lines = [...new Set($('sources').value.split('\n').map(line => line.trim()).filter(Boolean))];
  $('source-count').textContent = `${lines.length} nguồn`;
  const mode = document.querySelector('[name="mode"]:checked').value;
  $('source-hint').textContent = {auto:'Tự phân biệt từ khóa và URL', keyword:'Mỗi dòng là một từ khóa tìm bài', profile:'Nhập URL tài khoản cá nhân', page:'Nhập URL Trang / Fanpage Facebook', group:'Nhập URL nhóm Facebook công khai'}[mode];
  $('start-button').disabled = !session.token || busy || submitting || !lines.length;
}
function time(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('vi-VN', {hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit'});
}
function updateJob(job) {
  busy = Boolean(job && ['running','stopping'].includes(job.status));
  const status = job?.status || 'idle';
  const browserJob = job?.action === 'browser';
  const badge = $('job-badge');
  badge.className = `status-badge ${status}`;
  badge.replaceChildren(element('i'), document.createTextNode(browserJob && busy ? 'Đang mở Chrome' : statuses[status]));
  const titles = {idle:'Sẵn sàng cho lượt thu thập mới', running:'Đang thu thập bài viết…', stopping:'Đang dừng và lưu kết quả…', completed:'Lượt thu thập đã kết thúc', cancelled:'Đã dừng lượt thu thập', failed:'Lượt chạy gặp lỗi'};
  $('activity-title').textContent = browserJob ? (busy ? 'Đang mở Chrome để đăng nhập…' : status === 'completed' ? 'Chrome đã được mở' : 'Chưa mở được Chrome') : titles[status];
  const descriptions = {idle:'Nhập nguồn và bấm bắt đầu. Bài được lưu ngay khi đọc xong.', running:'Bài đọc được sẽ lưu ngay trong quá trình chạy.', stopping:'Đang kết thúc thao tác và xuất dữ liệu đã có. Vui lòng chờ.', completed:'Xem kết quả đã lưu bên dưới. Nếu chưa có bài, kiểm tra nhật ký và phiên đăng nhập Facebook.', cancelled:'Những bài đã lưu vẫn còn trong kết quả. Bạn có thể bắt đầu lượt mới.', failed:'Xem nhật ký để biết nguyên nhân. Các bài đã lưu trước khi gặp lỗi vẫn được giữ.'};
  $('activity-description').textContent = browserJob && status === 'completed' ? 'Đăng nhập Facebook trong cửa sổ Chrome của project, sau đó quay lại đây để thu thập.' : descriptions[status];
  $('progress-label').textContent = job ? (browserJob ? 'Thiết lập phiên Facebook' : 'Nguồn đã xử lý') : 'Chưa có lượt chạy';
  $('progress-count').textContent = job && !browserJob ? `${job.completed} / ${job.total}` : '—';
  $('job-progress').max = Math.max(job?.total || 1, 1);
  $('job-progress').value = job?.completed || 0;
  $('current-source').textContent = job?.label || '';
  const logs = $('job-logs');
  const nearBottom = logs.scrollHeight - logs.scrollTop - logs.clientHeight < 50;
  const logText = job?.logs?.join('\n') || 'Nhật ký sẽ xuất hiện tại đây…';
  if (logs.textContent !== logText) {
    logs.textContent = logText;
    if (nearBottom) logs.scrollTop = logs.scrollHeight;
  }
  $('log-dot').classList.toggle('live', busy);
  $('stop-button').hidden = !busy || browserJob;
  $('stop-button').disabled = status === 'stopping';
  $('open-browser').disabled = busy || submitting;
  $('open-browser-mobile').disabled = busy || submitting;
  updateInput();
}
function updateHistory(history) {
  const fingerprint = JSON.stringify(history.map(job => [job.id, job.status, job.completed]));
  if (fingerprint === lastHistory) return;
  lastHistory = fingerprint;
  const list = $('history-list');
  list.replaceChildren();
  if (!history.length) return list.append(element('span', 'history-empty', 'Chưa có lượt chạy trong phiên này.'));
  for (const job of history.slice(0, 6)) {
    const card = element('div', 'history-item');
    card.append(element('strong', '', job.action === 'browser' ? 'Mở Chrome' : `${job.total} nguồn`), element('span', `status-badge ${job.status}`, statuses[job.status]), element('time', '', time(job.started_at)));
    list.append(card);
  }
}
async function loadPosts() {
  const sequence = ++postRequest;
  const query = $('search-posts').value.trim();
  const result = await api(`/api/posts?q=${encodeURIComponent(query)}&offset=${offset}&limit=12`);
  if (sequence !== postRequest) return;
  total = result.total;
  if (!result.items.length && offset > 0 && total > 0) { offset = 0; return loadPosts(); }
  $('result-count').textContent = format(total);
  const list = $('post-list');
  list.replaceChildren();
  for (const post of result.items) {
    const row = element('button', 'post-row');
    row.type = 'button';
    row.dataset.postId = post.post_id;
    const author = element('div', 'author-cell');
    const name = element('div');
    name.append(element('strong', '', post.author || 'Chưa rõ tác giả'), element('small', '', post.posted_raw || time(post.collected_at)));
    author.append(element('span', 'avatar', (post.author || '?').trim().slice(0, 1).toUpperCase()), name);
    risks.set(post.post_id, post.risk ? {...post.risk, id: post.id} : null);
    if (post.risk) {
      const pill = element('span', `risk-pill risk-${post.risk.level.toLowerCase()}`, `${RISK_LABELS[post.risk.level]} · ${post.risk.score}`);
      pill.title = `Điểm rủi ro lừa đảo ${post.risk.score}/100`;
      name.append(pill);
    }
    const source = post.source_label || ({profile:'Cá nhân', page:'Trang', group:'Nhóm Facebook'}[post.source_type]) || post.source || 'Facebook';
    const numbers = element('div', 'post-numbers');
    const replies = (post.top_comments || []).filter(comment => comment.is_reply).length;
    const roots = (post.top_comments?.length || 0) - replies;
    numbers.append(element('strong', '', `${roots} bình luận`), element('div', '', `${replies} phản hồi · ${post.images?.length || 0} ảnh`));
    row.append(author, element('span', 'post-excerpt', post.text || '(Bài viết có ảnh)'), element('span', 'source-chip', source), numbers);
    row.querySelector('.source-chip').title = post.source_ref || source;
    row.addEventListener('click', () => openPost(post.post_id));
    list.append(row);
  }
  $('empty-results').hidden = total > 0;
  $('empty-results').querySelector('h3').textContent = query ? 'Không tìm thấy bài viết phù hợp' : 'Chưa có bài viết nào';
  $('empty-results').querySelector('p').textContent = query ? 'Thử từ khóa khác hoặc xóa bộ lọc để xem tất cả.' : 'Thêm nguồn và bắt đầu lượt thu thập đầu tiên.';
  $('pagination').hidden = total === 0;
  $('page-label').textContent = `${offset + 1}–${Math.min(offset + 12, total)} / ${format(total)} bài viết`;
  $('previous-page').disabled = offset === 0;
  $('next-page').disabled = offset + 12 >= total;
}
async function openPost(identity) {
  try {
    const post = await api(`/api/posts/${encodeURIComponent(identity)}`);
    $('detail-title').textContent = post.author || 'Chi tiết bài viết';
    renderPost(post, $('post-detail'));
    const risk = risks.get(identity);
    if (risk) {
      const banner = element('a', `risk-banner risk-${risk.level.toLowerCase()}`);
      banner.href = `/analysis?post=${risk.id}`;
      banner.append(element('strong', '', `Rủi ro ${RISK_LABELS[risk.level].toLowerCase()} · ${risk.score}/100`), element('span', '', 'Xem lý do và duyệt bài →'));
      $('post-detail').prepend(banner);
    }
    if (!$('post-dialog').open) $('post-dialog').showModal();
  } catch (error) { showToast(error.message); }
}
async function poll() {
  try {
    const state = await api('/api/state');
    session.token = state.token;
    $('connection-error').hidden = true;
    for (const key of ['posts','comments','images']) $(`stat-${key}`).textContent = format(state.stats[key]);
    $('nav-count').textContent = format(state.stats.posts);
    showBrowserState(state.browser_ready);
    updateJob(state.current);
    updateHistory(state.history);
    const fingerprint = JSON.stringify([state.stats, state.current?.id, state.current?.status]);
    if (fingerprint !== lastFingerprint || busy) { await loadPosts(); lastFingerprint = fingerprint; }
  } catch (error) {
    $('connection-error').hidden = false;
    session.token = '';
    updateInput();
  } finally { setTimeout(poll, 2000); }
}
$('collect-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || submitting || !$('collect-form').reportValidity()) return;
  submitting = true;
  $('form-error').hidden = true;
  updateInput();
  try {
    await api('/api/run', {sources:$('sources').value, mode:document.querySelector('[name="mode"]:checked').value, max_posts:Number($('max-posts').value), max_comments:Number($('max-comments').value), ocr:$('ocr').checked, recent_first:$('recent-first').checked, scam_filter:$('scam-filter').checked});
    const state = await api('/api/state');
    updateJob(state.current);
    updateHistory(state.history);
    showToast('Đã bắt đầu lượt thu thập.');
  } catch (error) { $('form-error').textContent = error.message; $('form-error').hidden = false; }
  finally { submitting = false; updateInput(); }
});
$('sources').addEventListener('input', updateInput);
document.querySelectorAll('[name="mode"]').forEach(input => input.addEventListener('change', updateInput));
async function openBrowser() {
  if (submitting || busy) return;
  submitting = true;
  $('open-browser').disabled = true;
  $('open-browser-mobile').disabled = true;
  try { await api('/api/browser', {}); updateJob((await api('/api/state')).current); showToast('Đang mở Chrome của project…'); }
  catch (error) { showToast(error.message); }
  finally { submitting = false; $('open-browser').disabled = busy; $('open-browser-mobile').disabled = busy; updateInput(); }
}
$('open-browser').addEventListener('click', openBrowser);
$('open-browser-mobile').addEventListener('click', openBrowser);
$('stop-button').addEventListener('click', async () => {
  $('stop-button').disabled = true;
  try { await api('/api/stop', {}); updateJob((await api('/api/state')).current); }
  catch (error) { $('stop-button').disabled = false; showToast(error.message); }
});
$('toggle-logs').addEventListener('click', () => {
  $('job-logs').hidden = !$('job-logs').hidden;
  $('toggle-logs').textContent = $('job-logs').hidden ? 'Mở nhật ký' : 'Thu gọn';
  $('toggle-logs').setAttribute('aria-expanded', String(!$('job-logs').hidden));
});
$('search-posts').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { offset = 0; loadPosts().catch(error => showToast(error.message)); }, 250); });
$('previous-page').addEventListener('click', () => { offset = Math.max(0, offset - 12); loadPosts().catch(error => showToast(error.message)); });
$('next-page').addEventListener('click', () => { offset += 12; loadPosts().catch(error => showToast(error.message)); });
$('close-dialog').addEventListener('click', () => $('post-dialog').close());
$('post-dialog').addEventListener('click', event => { if (event.target === $('post-dialog')) { const box = event.target.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.target.close(); } });
document.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', () => { document.querySelector('.nav-link.active')?.classList.remove('active'); link.classList.add('active'); }));
poll();
