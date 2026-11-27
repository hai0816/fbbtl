// Tab Kiểm tra nhanh: dán nội dung một bài viết, chấm bằng cùng bộ rule/ngưỡng, không lưu vào dữ liệu.
import {$, api, el, fmt, iconEl} from './shell.js';
import {badge, guard, openPost, renderEvidence} from './analysis-review.js';

const SAMPLE = 'Tuyển CTV làm việc tại nhà, việc nhẹ lương cao 500k–1tr/ngày, không cần kinh nghiệm, chỉ cần điện thoại. '
  + 'Đóng trước 200k phí giữ chỗ, hoàn lại sau 3 ngày. Inbox Zalo 0912345678 để nhận việc ngay!';
const VERDICTS = {
  HIGH: ['Nhiều dấu hiệu lừa đảo', 'Không chuyển tiền, không gửi giấy tờ hay mã OTP trước khi xác minh người đăng.'],
  MEDIUM: ['Có dấu hiệu đáng ngờ', 'Nên xác minh công ty, người đăng và điều kiện trước khi liên hệ.'],
  LOW: ['Ít dấu hiệu lừa đảo', 'Bộ rule hiện tại không thấy dấu hiệu đáng kể. Vẫn nên cẩn trọng nếu bị yêu cầu chuyển tiền.'],
};

function verdict(r) {
  if (r.category === 'WARNING_OR_AWARENESS') return ['Nội dung cảnh báo lừa đảo', 'Bài đang cảnh báo người khác; điểm các dấu hiệu đã được giảm theo ngữ cảnh.'];
  return VERDICTS[r.risk_level];
}

function gauge(r) {
  const wrap = el('div', 'gauge');
  const track = el('div', 'gauge-track');
  const fill = el('span', `gauge-fill fill-${r.risk_level.toLowerCase()}`); fill.style.width = `${r.risk_score}%`;
  track.append(fill);
  for (const [key, label] of [['medium', 'Trung bình'], ['high', 'Cao']]) {
    const tick = el('i', 'gauge-tick'); tick.style.left = `${r.thresholds[key]}%`; tick.title = `Ngưỡng ${label}: ${r.thresholds[key]}`; track.append(tick);
  }
  const scale = el('div', 'gauge-scale'); scale.append(el('span', '', '0'), el('span', '', `Ngưỡng ${r.thresholds.medium} / ${r.thresholds.high}`), el('span', '', '100'));
  wrap.append(track, scale); return wrap;
}

function render(r) {
  const box = $('check-result'); box.replaceChildren(); box.classList.remove('is-empty');
  const [title, advice] = verdict(r);
  const head = el('div', `verdict verdict-${r.risk_level.toLowerCase()}`);
  const number = el('div', 'verdict-score'); number.append(el('strong', '', r.risk_score), el('span', '', '/100'));
  const text = el('div', 'verdict-text'); const line = el('div', 'verdict-line'); line.append(badge(r.risk_level), el('span', 'cat', r.category_label));
  text.append(el('h2', '', title), el('p', '', advice), line);
  head.append(number, text); box.append(head, gauge(r));
  if (r.same_as) {
    const same = el('button', 'same-as', `Nội dung trùng với bài #${r.same_as} đã thu thập — xem chi tiết`); same.type = 'button';
    same.prepend(iconEl('layers')); same.addEventListener('click', guard(() => openPost(r.same_as))); box.append(same);
  }
  const counted = r.matches.filter(m => m.counted);
  box.append(el('h3', '', counted.length ? `Dấu hiệu phát hiện (${counted.length})` : 'Dấu hiệu phát hiện'));
  renderEvidence(box, r.matches);
  const f = r.features;
  const facts = [['Số điện thoại', f.phones.join(', ')], ['Kênh liên hệ', f.contact_channels.join(', ')], ['Liên kết', f.urls.join(', ')],
    ['Thu nhập công bố', f.money_rates.map(x => `${x.text} ≈ ${fmt(x.per_day)}đ/ngày`).join('; ')], ['Lợi nhuận công bố', f.return_rates.map(x => x.text).join('; ')],
    ['Từ cảnh báo', r.context.warning_terms.join(', ')]];
  const dl = el('dl', 'facts'); facts.filter(([, v]) => v).forEach(([k, v]) => dl.append(el('dt', '', k), el('dd', '', v)));
  if (dl.childElementCount) box.append(el('h3', '', 'Thông tin trích xuất'), dl);
  box.append(el('p', 'check-note', 'Điểm dựa trên bộ rule ở tab “Rule & ngưỡng”: đây là mức ưu tiên kiểm tra, không phải kết luận pháp lý.'));
}

export function initCheck() {
  const form = $('check-form'), area = form.text, count = $('check-count');
  const update = () => { count.textContent = `${fmt(area.value.length)} ký tự`; };
  area.addEventListener('input', update);
  area.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) form.requestSubmit(); });
  $('check-sample').addEventListener('click', () => { area.value = SAMPLE; update(); area.focus(); });
  $('check-clear').addEventListener('click', () => {
    area.value = ''; update(); area.focus();
    const box = $('check-result'); box.classList.add('is-empty'); box.replaceChildren(el('p', 'empty', 'Kết quả đánh giá sẽ hiện ở đây.'));
  });
  form.addEventListener('submit', guard(async event => {
    event.preventDefault();
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try { render(await api('/api/analysis/check', {text: area.value})); if (innerWidth < 930) $('check-result').scrollIntoView({behavior: 'smooth', block: 'start'}); }
    finally { button.disabled = false; }
  }));
}
