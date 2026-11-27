"""Rule engine: so khớp keyword/phrase/regex, AND/OR, cửa sổ câu, phủ định và ngữ cảnh cảnh báo.

Mỗi câu có hai dạng cùng độ dài (text.lower_form / text.fold):
- lower:  chữ thường CÓ dấu  → dùng cho từ dễ nhập nhằng khi bỏ dấu (lãi/lại, sàn/sản, đừng/dùng).
- folded: chữ thường KHÔNG dấu → dùng cho đa số cụm từ, bắt được cả bài cố tình viết không dấu.
Cụm có tiền tố "acc:" chỉ so khớp có dấu nếu câu được viết có dấu; nếu cả đoạn viết không dấu
thì buộc phải so khớp không dấu. Tiền tố "re:" là regex viết trên dạng không dấu.
"""
import re

from fb_scraper.analysis.text import accent_ratio, fold, lower_form, sentences

# Tăng khi đổi logic so khớp/từ điển trong code để mọi bài được chấm lại ở lần chạy sau.
ENGINE_VERSION = 2
ACCENTED_MIN_RATIO = 0.08
NEGATIONS = ["không", "đừng", "chưa", "chẳng", "chớ", "tránh", "nghiêm cấm", "tuyệt đối không", "không bao giờ",
             "không nên", "không cần", "không phải", "không yêu cầu", "không thu", "không mất", "không có",
             "miễn", "cảnh giác", "nếu ai", "ai yêu cầu", "bất kỳ ai", "bất cứ ai"]
WARNINGS = ["cảnh báo", "lừa đảo", "thủ đoạn", "chiêu trò", "chiêu lừa", "bị lừa", "mất tiền", "coi chừng",
            "tỉnh táo", "thận trọng", "cẩn trọng", "đừng tin", "tố giác", "trình báo", "công an", "nạn nhân",
            "scam", "giả mạo", "mạo danh", "chiếm đoạt", "khuyến cáo", "cảnh giác", "bẫy lừa", "sập bẫy",
            "cẩn thận", "bị chặn", "chặn liên lạc", "mất liên lạc", "không rút được", "bóc phốt", "đề phòng"]
# "Không lừa đảo", "nói không với lừa đảo" là lời TỰ khẳng định của bài quảng cáo, không phải cảnh báo.
SELF_CLAIM_RE = re.compile(r"\b(?:(?:khong|noi khong voi|cam ket khong|chong|khong phai|so|so bi|dung lo bi|"
                           r"dung so bi|ai cung nghi la) (?:lua dao|lua|scam)|khong phai scam)\b")
PROMO_RE = re.compile(r"\b(inbox|lien he|dang ky ngay|ket ban|nhan tin|de lai so dien thoai|tuyen gap|"
                      r"tham gia ngay|dang ky|zalo|telegram|hotline)\b")
AFTER_NEGATION_RE = re.compile(r"^\W{0,3}(mien phi|0 ?d\b|0 dong|khong (mat|thu|can|co)\b|la lua dao|= ?lua dao)")
PUNCT_RE = re.compile(r"[,.;:!?\n()\[\]]")


def _bounded(text):
    return r"(?<![\w])" + re.escape(text) + r"(?![\w])"


class TermSet:
    """Tập cụm từ khớp có dấu (khi văn bản có dấu) hoặc không dấu (khi văn bản viết không dấu)."""
    def __init__(self, phrases):
        self.accented = re.compile("|".join(_bounded(lower_form(item)) for item in phrases))
        self.folded = re.compile("|".join(_bounded(fold(item)) for item in phrases))

    def finditer(self, lower, folded, accented):
        return (self.accented if accented else self.folded).finditer(lower if accented else folded)

    def search(self, lower, folded, accented):
        return next(iter(self.finditer(lower, folded, accented)), None)


NEGATION_TERMS = TermSet(NEGATIONS)
WARNING_TERMS = TermSet(WARNINGS)


def _pattern(phrase):
    if phrase.startswith("re:"):
        return phrase[3:]
    return r"(?<![a-z0-9])" + re.escape(fold(phrase)) + r"(?![a-z0-9])"


def _join(patterns):
    return re.compile("|".join(f"(?:{item})" for item in patterns)) if patterns else None


def compile_rule(rule):
    """Biên dịch điều kiện; trả về bản sao rule kèm regex đã biên dịch cho từng mệnh đề."""
    compiled = []
    for clause in rule["conditions"]:
        if "any" in clause:
            normal = [_pattern(item) for item in clause["any"] if not item.startswith("acc:")]
            sensitive = [item[4:] for item in clause["any"] if item.startswith("acc:")]
            compiled.append({"kind": "text", "regex": _join(normal),
                             "terms": TermSet(sensitive) if sensitive else None})
        elif "feature" in clause:
            compiled.append({"kind": "feature", **clause})
        elif "groups_any" in clause:
            compiled.append({"kind": "groups", "groups": set(clause["groups_any"])})
        else:
            raise ValueError(f"Mệnh đề không hợp lệ trong rule {rule['rule_id']}: {clause}")
    exclude = [re.compile(_pattern(item)) for item in rule.get("exclude", [])]
    text_clauses = sum(item["kind"] == "text" for item in compiled)
    window = rule.get("window", 1 if text_clauses <= 1 else 2)
    return {**rule, "_clauses": compiled, "_exclude": exclude, "_window": window}


def _segment(source, sentence):
    lower = lower_form(sentence)
    folded = fold(sentence)
    accented = accent_ratio(lower, folded) >= ACCENTED_MIN_RATIO
    cleaned_lower = SELF_CLAIM_RE.sub(lambda match: " " * len(match.group(0)), folded)
    masked_lower = "".join(char if keep != " " or char == " " else " "
                           for char, keep in zip(lower, cleaned_lower))
    warning = WARNING_TERMS.search(masked_lower, cleaned_lower, accented) is not None
    return {"source": source, "text": sentence, "lower": lower, "folded": folded,
            "accented": accented, "warning": warning}


def build_segments(content, ocr_texts=()):
    """Tách câu cho bài viết và từng ảnh OCR; mỗi câu giữ bản gốc (để trích dẫn), bản có/không dấu."""
    segments = []
    for source, text in [("post", content)] + [("ocr", item) for item in ocr_texts]:
        for sentence in sentences(text or ""):
            if sentence.strip():
                segments.append(_segment(source, sentence))
    return segments


def is_negated(lower, folded, accented, start, end):
    """Cụm khớp đứng ngay sau từ phủ định/khuyên ngăn (trong cùng vế câu) hoặc ngay trước 'miễn phí'."""
    begin = max(0, start - 40)
    before = folded[begin:start]
    cut = begin + max((match.end() for match in PUNCT_RE.finditer(before)), default=0)
    words_lower, words_folded = lower[cut:start].split()[-4:], folded[cut:start].split()[-4:]
    if NEGATION_TERMS.search(" ".join(words_lower), " ".join(words_folded), accented):
        return True
    return bool(AFTER_NEGATION_RE.search(folded[end:end + 20]))


def _windows(segments, size):
    for start in range(len(segments)):
        for length in range(1, size + 1):
            chunk = segments[start:start + length]
            if len(chunk) < length or len({item["source"] for item in chunk}) > 1:
                break
            yield start, chunk


def _feature_hit(clause, features, settings):
    value = features.get(clause["feature"])
    threshold = settings.get(clause["setting"]) if clause.get("setting") else clause.get("gte")
    if value is None or threshold is None or value < threshold:
        return None
    if clause["feature"] == "max_daily_income":
        examples = [item["text"] for item in features.get("money_rates", []) if item["per_day"] >= threshold]
        return f"thu nhập ≈ {value:,.0f}đ/ngày ({', '.join(examples[:2])})".replace(",", ".")
    examples = [item["text"] for item in features.get("return_rates", []) if (item["per_month"] or 0) >= threshold]
    return f"lợi nhuận ≈ {value:g}%/tháng ({', '.join(examples[:2])})"


def _clause_matches(clause, lower, folded, accented):
    found = list(clause["regex"].finditer(folded)) if clause["regex"] else []
    if clause["terms"]:
        found += list(clause["terms"].finditer(lower, folded, accented))
    return [match for match in found if match.group(0)]


def match_rule(rule, segments, features, settings, max_evidence=3):
    """Trả về danh sách bằng chứng (rỗng nếu rule không kích hoạt)."""
    feature_terms = []
    for clause in rule["_clauses"]:
        if clause["kind"] == "feature":
            term = _feature_hit(clause, features, settings)
            if term is None:
                return []
            feature_terms.append(term)
    text_clauses = [clause for clause in rule["_clauses"] if clause["kind"] == "text"]
    if not text_clauses:
        return [{"source": "features", "text": term, "terms": [term], "warning": False} for term in feature_terms]
    size = rule["_window"]
    windows = ([(0, segments)] if segments else []) if size >= 99 else _windows(segments, size)
    evidence, used = [], set()
    for start, chunk in windows:
        indexes = set(range(start, start + len(chunk)))
        if indexes & used:
            continue
        lower = " ".join(item["lower"] for item in chunk)
        folded = " ".join(item["folded"] for item in chunk)
        if any(pattern.search(folded) for pattern in rule["_exclude"]):
            continue
        accented = accent_ratio(lower, folded) >= ACCENTED_MIN_RATIO
        terms = []
        for clause in text_clauses:
            found = [match for match in _clause_matches(clause, lower, folded, accented)
                     if not (rule.get("negatable") and is_negated(lower, folded, accented, match.start(), match.end()))]
            if not found:
                break
            terms.append(lower[found[0].start():found[0].end()])
        else:
            warning = any(item["warning"] for item in chunk)
            if rule.get("negatable") and warning:
                continue  # câu khuyên ngăn/cảnh báo: "đừng nạp tiền, đây là thủ đoạn lừa đảo"
            used |= indexes
            text = " ".join(item["text"] for item in chunk)
            evidence.append({"source": chunk[0]["source"], "text": text[:500],
                             "terms": list(dict.fromkeys(terms + feature_terms)), "warning": warning})
            if len(evidence) >= max_evidence:
                break
    return evidence


def document_context(segments, features):
    """Phân biệt bài CẢNH BÁO lừa đảo với bài QUẢNG CÁO/mời gọi.

    warning: số loại từ cảnh báo khác nhau (sau khi bỏ 'không lừa đảo' tự khẳng định).
    promotion: số lời kêu gọi liên hệ/đăng ký + kênh liên hệ + số điện thoại trong bài.
    """
    warning_terms, promo_terms = set(), set()
    for item in segments:
        cleaned = SELF_CLAIM_RE.sub(lambda match: " " * len(match.group(0)), item["folded"])
        masked = "".join(char if keep != " " or char == " " else " " for char, keep in zip(item["lower"], cleaned))
        warning_terms |= {fold(match.group(0)) for match in WARNING_TERMS.finditer(masked, cleaned, item["accented"])}
        promo_terms |= {match.group(0) for match in PROMO_RE.finditer(item["folded"])}
    promotion = len(promo_terms) + len(features.get("phones", [])) + len(features.get("contact_channels", []))
    warning = len(warning_terms)
    if warning >= 2 and warning > promotion:
        kind = "WARNING"
    elif promotion:
        kind = "PROMOTION"
    else:
        kind = "NEUTRAL"
    return {"type": kind, "warning_terms": sorted(warning_terms), "promotion_terms": sorted(promo_terms),
            "warning_score": warning, "promotion_score": promotion,
            "warning_sentences": sum(item["warning"] for item in segments)}


def mention_groups(segments, mentions):
    found = set()
    for group, phrases in mentions.items():
        terms = TermSet(phrases)
        if any(terms.search(item["lower"], item["folded"], item["accented"]) for item in segments):
            found.add(group)
    return found
