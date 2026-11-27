"""Mã nguồn thu thập Facebook, chia theo chức năng."""
import logging

# Traceback chỉ ghi vào file log (common/logs.py), terminal giữ thông báo ngắn bằng print().
# propagate=False: browser-use gắn handler vào root logger khi import, không để log lọt ra terminal.
# Khi chưa gọi setup_logging() (ví dụ trong kiểm thử), NullHandler bỏ qua log.
_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())
_logger.propagate = False
