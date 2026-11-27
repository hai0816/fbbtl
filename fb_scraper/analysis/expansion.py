"""Query Expansion: trích cụm từ quan trọng từ bài đáng ngờ để ĐỀ XUẤT từ khóa tìm kiếm mới.

1. Tách từ: tiếng Việt viết cách theo âm tiết ("cộng tác viên" là 3 âm tiết), nên câu được tách
   thành ĐƠN VỊ bằng so khớp dài nhất với từ điển = cụm từ trong rule + bộ từ khóa + LEXICON bên dưới.
   Câu còn được cắt tại dấu câu và stopword ("để", "và"...).
2. Ứng viên: 1–3 đơn vị liền nhau (2–6 âm tiết), không chứa số; cụm 1 đơn vị phải là từ ghép.
3. Điểm:  score = support × idf × (1 + signal) × hệ_số_độ_dài
     support = số bài đáng ngờ chứa cụm; idf = ln((N + 1) / (df + 1)) + 1 trên mọi bài đã phân tích;
     signal  = 1 nếu cụm chứa cụm từ của rule đang bật ("nạp tiền", "hoa hồng"...), ngược lại 0;
     hệ_số_độ_dài = 1,0 / 1,05 / 0,8 cho cụm 1 / 2 / 3 đơn vị (cụm quá dài ít hữu ích để tìm kiếm).
Đề xuất chỉ được lưu ở trạng thái PENDING; người dùng phải chấp nhận thì mới thành từ khóa.
"""
from collections import Counter, defaultdict
import json
import math
import re

from fb_scraper.analysis import store
from fb_scraper.analysis.text import STOPWORDS, TOKEN_RE, fold, keyword_norm, lower_form, sentences

LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
LENGTH_FACTOR = {1: 1.0, 2: 1.05, 3: 0.8}
# Từ quá chung: không đề xuất riêng lẻ (vẫn có thể nằm trong cụm dài hơn).
GENERIC = {"lien he", "inbox", "nhan tin", "ket ban", "thu nhap", "cam ket", "cong viec", "viec lam", "tham gia",
           "dang ky", "ho tro", "thoi gian", "so tien", "nhan vien", "san pham", "dien thoai", "tai khoan", "don gian"}
LEXICON = """cộng tác viên|nhiệm vụ|hoa hồng|kích hoạt|nạp tiền|chuyển khoản|đầu tư|lợi nhuận|sinh lời|thu nhập|
tài khoản|công việc|việc làm|kinh nghiệm|điện thoại|đơn hàng|rút tiền|đặt cọc|tuyển dụng|trực tuyến|tại nhà|mỗi ngày|
hàng ngày|cam kết|rủi ro|bảo toàn|uy tín|hoàn tiền|tiền ảo|lương cao|việc nhẹ|chốt đơn|thả tim|đánh giá|sản phẩm|
hoàn thành|nhân viên|cơ hội|tham gia|đăng ký|liên hệ|nhắn tin|kết bạn|hỗ trợ|lấy lại|thời gian|số tiền|giải ngân|
xác minh|nâng cấp|chuyên gia|tín hiệu|vào lệnh|ngoại hối|chứng khoán|cổ phiếu|tài chính|cấp độ|đơn giản|làm thêm|
bán thời gian|ngân hàng|tiền điện tử|thương mại điện tử|sàn giao dịch|hoàn vốn|tiền cọc|phí dịch vụ|xem video|
kiếm tiền|online|ứng dụng|đường link|mã giới thiệu|gói vip|thành viên|ổn định|hấp dẫn|nhanh chóng|dễ dàng""".replace("\n", "")


def _signal_phrases(rules):
    phrases = set()
    for rule in rules:
        for clause in rule["conditions"]:
            for item in clause.get("any", []):
                if not item.startswith("re:"):
                    phrases.add(keyword_norm(item.removeprefix("acc:")))
    return {phrase for phrase in phrases if phrase}


def build_lexicon(extra=()):
    units = {keyword_norm(item) for item in LEXICON.split("|")} | {keyword_norm(item) for item in extra}
    return {tuple(unit.split()) for unit in units if " " in unit}


def segment(words, lexicon, longest=4):
    """Tách từ: so khớp dài nhất (tối đa 4 âm tiết) với từ điển; âm tiết lạ là một đơn vị riêng."""
    units, position = [], 0
    while position < len(words):
        for size in range(min(longest, len(words) - position), 1, -1):
            if tuple(words[position:position + size]) in lexicon:
                units.append((position, position + size))
                position += size
                break
        else:
            units.append((position, position + 1))
            position += 1
    return units


def candidates(text, lexicon=None):
    """{dạng chuẩn hóa: (dạng có dấu để hiển thị, số đơn vị)} cho mọi cụm ứng viên trong văn bản."""
    lexicon = build_lexicon() if lexicon is None else lexicon
    found = {}
    for sentence in sentences(text):
        for chunk in re.split(r"[,;:()\"!?.]+", sentence):
            folded_words = TOKEN_RE.findall(fold(chunk))
            lower_words = TOKEN_RE.findall(lower_form(chunk))
            if len(folded_words) != len(lower_words):
                lower_words = folded_words
            units = segment(folded_words, lexicon)
            for start in range(len(units)):
                for count in (1, 2, 3):
                    group = units[start:start + count]
                    if len(group) < count:
                        break
                    words = folded_words[group[0][0]:group[-1][1]]
                    if any(word in STOPWORDS for word in words) or any(re.search(r"\d", word) for word in words):
                        break  # stopword/số cắt cụm: không nối qua "để", "và"
                    if count == 1 and len(words) < 2 or not 2 <= len(words) <= 6:
                        continue
                    if count > 1 and group[-1][1] - group[-1][0] == 1 and words[-1] not in {"online"}:
                        continue  # không kết thúc bằng âm tiết lẻ ("nhiệm vụ nhận")
                    shown = " ".join(lower_words[group[0][0]:group[-1][1]])
                    found.setdefault(" ".join(words), (shown, count))
    return found


def suggest(db, *, min_level="MEDIUM", post_ids=None, text=None, limit=20, save=True):
    """Sinh đề xuất. text: văn bản tự nhập (ví dụ một bài vừa thấy) thay cho bài trong database."""
    keywords = store.list_keywords(db)
    signals = _signal_phrases(store.load_rules(db))
    lexicon = build_lexicon(signals | {row["keyword"] for row in keywords})
    signal_re = re.compile("|".join(r"(?<![a-z0-9])" + re.escape(item) + r"(?![a-z0-9])"
                                    for item in sorted(signals, key=len, reverse=True)) or "$^")
    existing = {row["keyword_norm"] for row in keywords}
    decided = {row[0] for row in db.execute("SELECT phrase_norm FROM keyword_suggestions WHERE status!='PENDING'")}
    sources = []  # (post_id | None, category, văn bản bằng chứng)
    if text:
        sources.append((None, "OTHER", text))
    else:
        query = ("SELECT post_id, category, risk_level, evidence FROM risk_assessments "
                 "WHERE category NOT IN ('WARNING_OR_AWARENESS', 'NO_INDICATOR')")
        for post_id, category, level, evidence in db.execute(query):
            if post_ids is not None and post_id not in post_ids:
                continue
            if post_ids is None and LEVEL_ORDER[level] < LEVEL_ORDER[min_level]:
                continue
            snippets = [item["text"] for match in json.loads(evidence) for item in match["evidence"]
                        if item["source"] in {"post", "ocr"}]
            sources.append((post_id, category, "\n".join(dict.fromkeys(snippets))))
    support, display, example, categories = Counter(), {}, {}, defaultdict(Counter)
    for post_id, category, body in sources:
        for norm, shown in candidates(body, lexicon).items():
            support[norm] += 1
            display.setdefault(norm, shown)
            example.setdefault(norm, post_id)
            categories[norm][category] += 1
    background = [row[0] for row in db.execute("SELECT folded_text FROM post_analysis")]
    scored = []
    for norm, count in support.items():
        if norm in existing or norm in decided or norm in GENERIC:
            continue
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(norm) + r"(?![a-z0-9])")
        idf = math.log((len(background) + 1) / (sum(1 for body in background if pattern.search(body)) + 1)) + 1
        signal = 1 if signal_re.search(norm) else 0
        shown, units = display[norm]
        scored.append({"phrase": shown, "norm": norm, "support": count, "signal": bool(signal), "units": units,
                       "score": round(count * idf * (1 + signal) * LENGTH_FACTOR[units], 3),
                       "category": _keyword_category(categories[norm]) if not text else _text_category(norm),
                       "example_post_id": example[norm]})
    scored.sort(key=lambda item: (-item["score"], item["norm"]))
    picked = []
    for item in scored:
        # Cụm nằm trọn trong một cụm đã chọn (≤ 2 đơn vị) với cùng support thì không đề xuất lặp.
        if any(f" {item['norm']} " in f" {other['norm']} " and item["support"] == other["support"]
               and other["units"] <= 2 for other in picked):
            continue
        picked.append(item)
        if len(picked) >= limit:
            break
    if save and picked:
        store.save_suggestions(db, picked)
    return picked


def _text_category(norm):
    invest = re.search(r"\b(dau tu|loi nhuan|sinh loi|von|san|coin|usdt|rut tien|lai)\b", norm)
    job = re.search(r"\b(tuyen|viec|cong tac vien|nhiem vu|hoa hong|luong|don hang)\b", norm)
    return "INVESTMENT" if invest and not job else "JOB" if job else "OTHER"


def _keyword_category(counter):
    job = sum(count for category, count in counter.items() if category.startswith("JOB"))
    invest = sum(count for category, count in counter.items() if category.startswith(("INVESTMENT", "JOB_TO")))
    if not job and not invest:
        return "OTHER"
    return "JOB" if job >= invest else "INVESTMENT"
