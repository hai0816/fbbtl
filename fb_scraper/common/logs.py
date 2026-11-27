"""Ghi traceback đầy đủ vào data/logs/scraper.log.

Terminal và giao diện vẫn hiện thông báo ngắn bằng print(); file log giữ chi tiết lỗi
để tìm nguyên nhân khi Facebook đổi giao diện hoặc một bước bị bỏ qua.
"""
import logging
from logging.handlers import RotatingFileHandler

from fb_scraper.common.paths import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "scraper.log"
ROOT_LOGGER = "fb_scraper"


def get_logger(name):
    """Logger con của fb_scraper, ví dụ get_logger(__name__)."""
    return logging.getLogger(name)


def setup_logging():
    """Gọi một lần khi khởi động (main.py, worker). Lỗi tạo file log không dừng chương trình."""
    logger = logging.getLogger(ROOT_LOGGER)
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    except OSError as error:
        print(f"Chưa tạo được file log ({type(error).__name__}); tiếp tục chạy.", flush=True)
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
