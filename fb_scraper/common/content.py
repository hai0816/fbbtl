"""Chọn nội dung đầy đủ và đọc tên từ nhãn Facebook khi DOM không có link tên."""
import re

# Nguồn duy nhất cho nhãn aria-label của bình luận/reply; posts/labels.py và các đoạn
# JavaScript dùng lại chuỗi này để Python và Chrome không nhận diện lệch nhau.
# Cú pháp hợp lệ ở cả Python và JavaScript: (?:...) là nhóm không ghi nhớ.
#
# Nhãn reply hiện tại của Facebook (đã đối chiếu trên trang thật, 11/2026):
#   "Phản hồi bình luận của <người viết bình luận cha> dưới tên <người reply> vào 1 tuần trước"
# Reply không còn lồng trong DOM của bình luận cha và permalink không có reply_comment_id,
# nên tên người viết bình luận cha trong nhãn là dấu hiệu cấu trúc để nối cha-con.
REPLY_LABEL_SOURCE = (
    r"(?:Phản hồi bình luận của .+? dưới tên|Reply to .+?'s comment by|Reply by|"
    r"Trả lời (?:bình luận của .+? dưới tên|của|dưới tên|bởi)|Phản hồi (?:của|dưới tên|bởi))"
)
COMMENT_LABEL_SOURCE = r"(?:Comment (?:by|from)|Bình luận (?:của|dưới tên|bởi)|" + REPLY_LABEL_SOURCE + ")"
COMMENT_AUTHOR_SOURCE = (
    r"^\s*" + COMMENT_LABEL_SOURCE + r"\s+(.+?)(?:\s+(?:vào|lúc|at|on|cách đây)\s+.+)?$"
)
COMMENT_AUTHOR_RE = re.compile(COMMENT_AUTHOR_SOURCE, re.I)
# Nhóm 1: tên người viết bình luận cha trong nhãn reply.
REPLY_PARENT_SOURCE = r"^\s*(?:(?:Phản hồi|Trả lời) bình luận của (.+?) dưới tên|Reply to (.+?)'s comment by)\s"
REPLY_PARENT_RE = re.compile(REPLY_PARENT_SOURCE, re.I)
EXPAND_LABEL_SOURCE = r"^\s*(?:Xem thêm|See more)\s*[.…]*\s*$"
EXPAND_LABEL_RE = re.compile(EXPAND_LABEL_SOURCE, re.I)
COLLAPSED_TEXT_RE = re.compile(r"(?:Xem thêm|See more)\s*[.…]*\s*$", re.I)


def comment_author(label):
    match = COMMENT_AUTHOR_RE.match(label or "")
    return match.group(1).strip() if match else None


def reply_parent_author(label):
    """'Phản hồi bình luận của A dưới tên B…' -> 'A'; nhãn không nêu bình luận cha -> None."""
    match = REPLY_PARENT_RE.match(label or "")
    if not match:
        return None
    return (match.group(1) or match.group(2)).strip()


def is_collapsed_text(text):
    return bool(COLLAPSED_TEXT_RE.search(text or ""))


def fuller_text(previous, incoming):
    """Không để bản rút gọn ghi đè bản đầy đủ; chấp nhận bản đã mở dù ngắn hơn nhãn UI."""
    previous, incoming = previous or "", incoming or ""
    if not incoming:
        return previous
    if not previous.strip():
        return incoming  # bản rút gọn vẫn tốt hơn nội dung rỗng (trước đây bài mới bị lưu rỗng)
    if is_collapsed_text(previous) != is_collapsed_text(incoming):
        return incoming if is_collapsed_text(previous) else previous
    old, new = previous.rstrip(), incoming.rstrip()
    if len(new) < len(old) and (old.startswith(new) or new.endswith(("…", "..."))):
        return previous
    return incoming
