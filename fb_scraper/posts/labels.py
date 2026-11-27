"""Nhãn aria-label của bình luận, dùng chung cho Python và JavaScript."""
import re

from fb_scraper.common.content import COMMENT_LABEL_SOURCE, REPLY_LABEL_SOURCE

COMMENT_LABEL_RE = re.compile(r"^\s*" + COMMENT_LABEL_SOURCE + r"\b", re.I)
REPLY_LABEL_RE = re.compile(r"^\s*" + REPLY_LABEL_SOURCE + r"\b", re.I)

# Biểu thức chính quy dạng JavaScript, chèn vào các đoạn script chạy trong Chrome.
COMMENT_LABEL_JS = "/^" + COMMENT_LABEL_SOURCE + "/i"
REPLY_LABEL_JS = r"/^\s*" + REPLY_LABEL_SOURCE + "/i"
