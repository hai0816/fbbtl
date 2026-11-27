"""Chuẩn hóa văn bản tiếng Việt cho so khớp rule, TF-IDF và đề xuất từ khóa.

Hai dạng văn bản được tạo từ nội dung gốc (nội dung gốc KHÔNG bị sửa trong database):
- normalize(): NFC, bỏ ký tự vô hình, gộp khoảng trắng. Dùng để hiển thị/trích bằng chứng.
- fold(): chữ thường, bỏ dấu (đ → d), mở rộng viết tắt, bỏ ký tự chèn để né lọc
  ("n.ạ.p t.i.ề.n" → "nap tien"). Rule viết có dấu cũng được fold() trước khi so khớp,
  nên "nạp tiền", "nap tien", "NẠP TIỀN" đều khớp như nhau.
"""
import re
import unicodedata

ZERO_WIDTH_RE = re.compile("[​-‏⁠﻿­]")
SPACE_RE = re.compile(r"[ \t\r\f\v 　]+")
NEWLINES_RE = re.compile(r"\n{3,}")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|vn|net|org|info|xyz|top|"
                    r"io|me|cc|club|online|site|shop|app|link|ly)(?:/[^\s<>\"']*)?", re.I)
# Chữ cái bị tách bằng dấu chấm/gạch/sao/khoảng trắng đơn: "n.ạ.p", "n-a-p", "z a l o".
SPLIT_LETTERS_RE = re.compile(r"\b(?:[^\W\d_][.\-_*·]){2,}[^\W\d_]\b")
SPACED_LETTERS_RE = re.compile(r"\b(?:[^\W\d_] ){3,}[^\W\d_]\b")
LEET = str.maketrans({"@": "a", "$": "s"})
SENTENCE_RE = re.compile(r"(?<=[.!?…;])\s+|\n+|\s*[•●▪►✅✔☑❌⭐🔥👉📌💥💰]+\s*")
TOKEN_RE = re.compile(r"[^\W_]+(?:[.,]\d+)*%?", re.U)

# Viết tắt/teencode thường gặp trong bài tuyển dụng, đầu tư (áp dụng trên chữ thường có dấu).
# Chỉ gồm viết tắt ít nhập nhằng ("hong" không có vì trùng "hoa hồng", "kh" trùng "khách hàng").
ABBREVIATIONS = {
    "ctv": "cộng tác viên", "nv": "nhiệm vụ", "ln": "lợi nhuận", "ib": "inbox", "ibx": "inbox",
    "ko": "không", "k0": "không", "hok": "không", "khg": "không",
    "dc": "được", "đc": "được", "dk": "đăng ký", "đk": "đăng ký", "sdt": "số điện thoại", "sđt": "số điện thoại",
    "tk": "tài khoản", "stk": "số tài khoản", "ck": "chuyển khoản", "tn": "thu nhập",
    "vnd": "đồng", "vnđ": "đồng", "zl": "zalo", "tele": "telegram", "tlg": "telegram",
    "kn": "kinh nghiệm", "hh": "hoa hồng", "sl": "số lượng",
    "mmo": "kiếm tiền online", "x2": "nhân đôi", "x3": "nhân ba",
}
ABBREVIATION_RE = re.compile(r"(?<![\w])(" + "|".join(sorted(map(re.escape, ABBREVIATIONS), key=len, reverse=True))
                             + r")(?![\w])")
# "k" đứng riêng là "không" (k cần kinh nghiệm), nhưng "500k", "500 k" là số tiền.
LONE_K_RE = re.compile(r"(?<![\w])(?<!\d )k(?![\w])")

STOPWORDS = set("""
va la cua co cho voi cac nhung mot nay do thi ma de duoc khi tu den trong tren duoi ra vao nhu
cung da dang se rat lai nen hay hoac neu vi bi o tai theo ve ban minh toi chung ta ai gi nao
a oi ah uh ok roi con chi moi chua nua thoi luon day kia ay vay sao
""".split())


def normalize(text):
    """NFC, bỏ ký tự vô hình, chuẩn khoảng trắng; giữ nguyên chữ hoa, dấu và xuống dòng."""
    if not text:
        return ""
    value = unicodedata.normalize("NFC", str(text))
    value = ZERO_WIDTH_RE.sub("", value)
    value = value.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    lines = [SPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _base_char(char):
    if char in "đĐ":
        return "d" if char == "đ" else "D"
    decomposed = unicodedata.normalize("NFD", char)
    return decomposed[0] if decomposed else char


def strip_accents(text):
    """Bỏ dấu từng ký tự; với chuỗi NFC không có dấu rời, độ dài được giữ nguyên."""
    return "".join(_base_char(char) for char in text if unicodedata.category(char) != "Mn")


def _join_split(match):
    return re.sub(r"[.\-_*· ]", "", match.group(0))


def lower_form(text):
    """Dạng so khớp CÓ DẤU: chữ thường, gộp chữ bị tách, mở rộng viết tắt, giữ URL.

    fold(text) == strip_accents(lower_form(text)) và hai chuỗi dài bằng nhau, nên vị trí khớp
    trên bản không dấu dùng được trên bản có dấu (để kiểm tra phủ định, từ dễ nhập nhằng).
    """
    if not text:
        return ""
    value = "".join(char for char in normalize(text).lower() if unicodedata.category(char) != "Mn")
    urls = []

    def keep_url(match):
        urls.append(match.group(0))
        return f" \x00{len(urls) - 1}\x00 "

    value = URL_RE.sub(keep_url, value)
    value = SPLIT_LETTERS_RE.sub(_join_split, value)
    value = SPACED_LETTERS_RE.sub(_join_split, value)
    value = value.translate(LEET)
    value = ABBREVIATION_RE.sub(lambda match: ABBREVIATIONS[match.group(1)], value)
    value = LONE_K_RE.sub("không", value)
    value = re.sub(r"\x00(\d+)\x00", lambda match: urls[int(match.group(1))], value)
    return SPACE_RE.sub(" ", value).strip()


def fold(text):
    """Dạng so khớp KHÔNG DẤU: "N.Ạ.P T.I.Ề.N" → "nap tien", "CTV" → "cong tac vien"."""
    return strip_accents(lower_form(text))


def accent_ratio(lower, folded):
    """Tỷ lệ chữ cái có dấu; văn bản tiếng Việt bình thường khoảng 0,3–0,5, viết không dấu ≈ 0."""
    letters = sum(char.isalpha() for char in folded)
    if not letters:
        return 0.0
    return sum(a != b for a, b in zip(lower, folded)) / letters


def sentences(text):
    """Tách câu theo dấu câu, xuống dòng và ký hiệu gạch đầu dòng (emoji) phổ biến trong bài quảng cáo."""
    parts = []
    for piece in SENTENCE_RE.split(normalize(text)):
        piece = (piece or "").strip(" -–—:,")
        if not piece:
            continue
        # Câu quá dài (không có dấu câu) được cắt mềm để bằng chứng trích ra vẫn ngắn gọn.
        while len(piece) > 400:
            cut = piece.rfind(",", 0, 400)
            cut = cut if cut > 100 else 400
            parts.append(piece[:cut].strip())
            piece = piece[cut:].strip(" ,")
        if piece:
            parts.append(piece)
    return parts


def tokens(text, *, folded=False):
    """Tách từ theo âm tiết (tiếng Việt viết cách nhau từng âm tiết) trên dạng fold."""
    value = text if folded else fold(text)
    return [token for token in TOKEN_RE.findall(value) if token]


def content_tokens(text, *, folded=False):
    """Bỏ stopword và token chỉ gồm số để biểu diễn nội dung."""
    return [token for token in tokens(text, folded=folded)
            if token not in STOPWORDS and not token.replace(".", "").replace(",", "").isdigit() and len(token) > 1]


def ngrams(items, low=1, high=2):
    for size in range(low, high + 1):
        for start in range(len(items) - size + 1):
            yield " ".join(items[start:start + size])


def keyword_norm(text):
    """Khóa duy nhất của một từ khóa: fold + gộp khoảng trắng ("Việc  làm Online" == "viec lam online")."""
    return " ".join(tokens(text))
