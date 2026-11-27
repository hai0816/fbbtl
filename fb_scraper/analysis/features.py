"""Trích xuất đặc trưng có cấu trúc từ văn bản đã fold: liên kết, liên hệ, số tiền, % lợi nhuận.

Các đặc trưng là SỐ LIỆU QUAN SÁT được (ví dụ "thu nhập công bố 800.000đ/ngày"), dùng làm điều kiện
cho rule kiểu feature và để thống kê liên kết/số điện thoại được dùng lại giữa nhiều bài.
"""
import re
from urllib.parse import urlparse

from fb_scraper.analysis.text import URL_RE, fold

PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){8,10}(?!\d)")
CONTACT_RE = re.compile(r"\b(zalo|telegram|tele|whatsapp|messenger|inbox|ib|t\.me)\b[\s:/.@-]*([\w.@+-]{3,40})?")
HASHTAG_RE = re.compile(r"#[^\s#]{2,60}")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")

# Số tiền: "500k", "1tr5", "1,5 triệu", "300.000đ", "2 củ" (+ đơn vị thời gian nếu có).
AMOUNT = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(k|nghin|ngan|tr|trieu|cu|m|ty|d|dong)?(\d)?"
PER = r"\s*(?:/|mot|moi|1|tren|hang|mỗi)?\s*(ngay|gio|h|tieng|tuan|thang|buoi|ca)\b"
MONEY_RATE_RE = re.compile(r"(?<![\w.,])" + AMOUNT + PER)
MONEY_RE = re.compile(r"(?<![\w.,])" + AMOUNT + r"(?![\w%])")
UNITS = {"k": 1e3, "nghin": 1e3, "ngan": 1e3, "tr": 1e6, "trieu": 1e6, "cu": 1e6, "m": 1e6, "ty": 1e9,
         "d": 1, "dong": 1, None: 1}
PER_DAY = {"ngay": 1, "gio": 8, "h": 8, "tieng": 8, "buoi": 1, "ca": 1, "tuan": 1 / 7, "thang": 1 / 30}

# % lợi nhuận chỉ được tính khi có từ chỉ lợi nhuận đứng gần (tránh "giảm giá 50%").
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%" + r"(?:\s*(?:/|mot|moi|1|hang|mỗi)?\s*(ngay|tuan|thang|nam)\b)?")
PROFIT_WORDS = re.compile(r"\b(lai|loi nhuan|sinh loi|hoa hong|hoan|loi|tang truong|chiet khau|thuong)\b")
PER_MONTH = {"ngay": 30, "tuan": 30 / 7, "thang": 1, "nam": 1 / 12}
MULTIPLIER_RE = re.compile(r"\b(nhan doi|nhan ba|gap doi|gap ba|x\s?[2-9]|gap (\d+) lan)\b")


def _amount(number, unit, tail):
    raw = number.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        value = float(re.sub(r"[.,]", "", raw))
    else:
        value = float(raw.replace(",", "."))
    value *= UNITS.get(unit, 1)
    if tail and unit in {"tr", "trieu", "cu", "m"}:
        value += int(tail) * 1e5  # 1tr5 = 1.500.000
    return value


def money_rates(folded):
    """Các mức tiền có đơn vị thời gian, quy về đồng/ngày. Bỏ số quá nhỏ (không có đơn vị tiền)."""
    rates = []
    for match in MONEY_RATE_RE.finditer(folded):
        number, unit, tail, period = match.groups()
        value = _amount(number, unit, tail)
        if unit is None and value < 10000:
            continue  # "8/ngày", "2 buổi" không phải số tiền
        rates.append({"text": match.group(0).strip(), "per_day": round(value * PER_DAY[period])})
    return rates


def return_rates(folded):
    """% lợi nhuận quy về %/tháng; chỉ lấy khi 40 ký tự trước có từ chỉ lợi nhuận."""
    rates = []
    for match in PERCENT_RE.finditer(folded):
        before = folded[max(0, match.start() - 40):match.start()]
        before = re.split(r"[.!?;\n]\s", before)[-1]  # chỉ xét trong cùng câu
        if not PROFIT_WORDS.search(before) or re.search(r"\b(giam|giam gia|sale|khuyen mai)\b", before):
            continue
        value = float(match.group(1).replace(",", "."))
        period = match.group(2)
        rates.append({"text": match.group(0).strip(), "percent": value,
                      "per_month": round(value * PER_MONTH[period], 2) if period else None})
    return rates


def domains(urls):
    found = []
    for url in urls:
        host = urlparse(url if "://" in url else "http://" + url).hostname or ""
        host = host.removeprefix("www.")
        if host and host not in found:
            found.append(host)
    return found


def extract(text, folded=None):
    """Trả về dict đặc trưng của một văn bản (bài viết + OCR)."""
    folded = fold(text) if folded is None else folded
    urls = sorted({url.rstrip(".,;)!") for url in URL_RE.findall(text or "")})
    phones = sorted({re.sub(r"\D", "", phone).removeprefix("84") for phone in PHONE_RE.findall(folded)})
    phones = ["0" + phone if not phone.startswith("0") else phone for phone in phones]
    contacts = sorted({(kind if kind not in {"tele", "t.me"} else "telegram") for kind, _ in CONTACT_RE.findall(folded)})
    incomes = money_rates(folded)
    returns = return_rates(folded)
    monthly = [item["per_month"] for item in returns if item["per_month"] is not None]
    return {
        "urls": urls, "domains": domains(urls), "phones": phones, "contact_channels": contacts,
        "hashtags": sorted(set(HASHTAG_RE.findall(text or "")))[:30],
        "emoji_count": len(EMOJI_RE.findall(text or "")),
        "money_rates": incomes[:10], "max_daily_income": max((item["per_day"] for item in incomes), default=None),
        "return_rates": returns[:10], "max_monthly_return_pct": max(monthly, default=None),
        "max_return_pct": max((item["percent"] for item in returns), default=None),
        "multiplier_claims": [match.group(0) for match in MULTIPLIER_RE.finditer(folded)][:5],
        "money_mentions": len(MONEY_RE.findall(folded)),
    }
