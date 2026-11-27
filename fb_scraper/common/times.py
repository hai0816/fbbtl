"""Đọc mốc thời gian Facebook hiển thị (tiếng Việt/tiếng Anh) thành ISO UTC.

Hỗ trợ:
- tương đối: "39 phút trước", "2 giờ", "3 ngày", "1 tuần", "2 tháng", "1 năm", "5m", "2h", "3d", "1w", "1y",
  "Vừa xong", "Just now";
- "Hôm qua lúc 10:05", "Yesterday at 10:05 AM";
- tuyệt đối: "Thứ Sáu, 28 Tháng 8, 2026 lúc 17:35", "10 Tháng 11 lúc 08:03" (không có năm → năm hiện tại),
  "November 10, 2026 at 8:03 AM", "November 10 at 8:03 AM", "10 November 2026".
Giờ tuyệt đối là giờ địa phương của máy (Facebook hiển thị theo múi giờ trình duyệt).
`now` cho phép tính lại thời gian tương đối theo thời điểm đã thu thập (collected_at).
"""
from datetime import datetime, timedelta, timezone
import re

UNITS = {"phút": "minutes", "phut": "minutes", "m": "minutes", "min": "minutes", "mins": "minutes",
         "minute": "minutes", "minutes": "minutes",
         "giờ": "hours", "gio": "hours", "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "hours": "hours",
         "ngày": "days", "ngay": "days", "d": "days", "day": "days", "days": "days",
         "tuần": "weeks", "tuan": "weeks", "w": "weeks", "week": "weeks", "weeks": "weeks",
         "tháng": "months", "thang": "months", "mo": "months", "month": "months", "months": "months",
         "năm": "years", "nam": "years", "y": "years", "yr": "years", "year": "years", "years": "years"}
RELATIVE_RE = re.compile(r"^(?:khoảng\s+|about\s+|an?\s+)?(\d+|một|an|a)\s*(" + "|".join(
    sorted(map(re.escape, UNITS), key=len, reverse=True)) + r")\b(?!\s*\d)(?:\s*(?:trước|ago))?\s*(?:[·•].*)?$", re.I)
JUST_NOW_RE = re.compile(r"^(?:vừa xong|vừa mới|just now|now)\b", re.I)
CLOCK = r"(\d{1,2})[:h](\d{2})\s*(AM|PM|SA|CH)?"
YESTERDAY_RE = re.compile(r"^(?:hôm qua|yesterday)(?:\s+(?:lúc|at)\s+" + CLOCK + r")?", re.I)
VI_DATE_RE = re.compile(r"(\d{1,2})\s+tháng\s+(\d{1,2})(?:,?\s+(\d{4}))?(?:\s+lúc\s+" + CLOCK + r")?", re.I)
VI_NUMERIC_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(?:lúc\s+)?" + CLOCK + r")?")
MONTHS = {name: index for index, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
     ("november", "nov"), ("december", "dec")], 1) for name in names}
MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
EN_DATE_RE = re.compile(r"\b(" + MONTH_NAMES + r")\.?\s+(\d{1,2})\b(?:,?\s+(\d{4}))?(?:\s+at\s+" + CLOCK + r")?", re.I)
EN_DAY_FIRST_RE = re.compile(r"\b(\d{1,2})\s+(" + MONTH_NAMES + r")\.?(?:,?\s+(\d{4}))?(?:\s+at\s+" + CLOCK + r")?", re.I)


def _hour(hour, minute, meridiem):
    hour, minute = int(hour or 0), int(minute or 0)
    meridiem = (meridiem or "").upper()
    if meridiem in {"PM", "CH"} and hour < 12:
        hour += 12
    elif meridiem in {"AM", "SA"} and hour == 12:
        hour = 0
    return hour, minute


def _local(year, month, day, hour, minute, now_local):
    """Ngày không có năm: dùng năm hiện tại, nếu thành tương lai thì lùi một năm."""
    explicit = year is not None
    year = int(year) if explicit else now_local.year
    try:
        value = datetime(year, int(month), int(day), hour, minute, tzinfo=now_local.tzinfo)
    except ValueError:
        return None
    if not explicit and value > now_local + timedelta(days=1):
        value = value.replace(year=year - 1)
    return value


def _relative(amount, unit, now):
    amount = 1 if amount.lower() in {"một", "an", "a"} else int(amount)
    kind = UNITS[unit.lower()]
    if kind == "months":
        return now - timedelta(days=30 * amount)
    if kind == "years":
        return now - timedelta(days=365 * amount)
    return now - timedelta(**{kind: amount})


def parse_relative(raw, now=None):
    """Chỉ thời gian tương đối ("2 giờ", "Vừa xong"); dùng khi cần phân biệt với ngày tuyệt đối."""
    text = (raw or "").strip()
    now = now or datetime.now(timezone.utc)
    if JUST_NOW_RE.match(text):
        return now.isoformat()
    match = RELATIVE_RE.match(text)
    if not match:
        return None
    return _relative(match.group(1), match.group(2), now).isoformat()


def parse_post_time(raw, now=None):
    """Trả về ISO UTC hoặc None nếu không nhận ra mốc thời gian."""
    text = " ".join((raw or "").replace("͏", "").split())
    if not text or len(text) > 80:
        return None
    now = now or datetime.now(timezone.utc)
    relative = parse_relative(text, now)
    if relative:
        return relative
    now_local = now.astimezone()
    match = YESTERDAY_RE.match(text)
    if match:
        hour, minute = _hour(*match.groups()) if match.group(1) else (now_local.hour, now_local.minute)
        day = (now_local - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return day.astimezone(timezone.utc).isoformat()
    for pattern, order in ((VI_DATE_RE, "dmy"), (VI_NUMERIC_RE, "dmy"), (EN_DATE_RE, "mdy"), (EN_DAY_FIRST_RE, "dmy")):
        match = pattern.search(text)
        if not match:
            continue
        first, second, year, hour, minute, meridiem = match.groups()
        if order == "mdy":
            month, day = MONTHS[first.lower()], second
        else:
            day, month = first, MONTHS.get(second.lower(), second) if not second.isdigit() else second
        value = _local(year, month, day, *_hour(hour, minute, meridiem), now_local)
        if value:
            return value.astimezone(timezone.utc).isoformat()
    return None
