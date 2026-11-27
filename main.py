"""Điểm chạy chung: python main.py <lệnh> [tùy chọn]."""
import argparse

from fb_scraper.common.logs import setup_logging
from fb_scraper.common.runtime import load_env, setup_console


def main():
    # Thiết lập một lần tại đây; các module con không tự đổi encoding, nạp .env hay mở file log.
    setup_console()
    load_env()
    setup_logging()

    parser = argparse.ArgumentParser(description="Thu thập dữ liệu Facebook.")
    parser.add_argument(
        "command",
        choices=("posts", "entity", "search", "agent", "browser", "storage", "web", "analyze", "monitor"),
        help="posts: bài viết; entity: cá nhân/nhóm; search: tìm kiếm; "
             "agent: LLM; browser: mở Chrome; storage: xuất dữ liệu; web: giao diện; "
             "analyze: phát hiện dấu hiệu lừa đảo; monitor: thu thập định kỳ nguồn theo dõi",
    )
    # REMAINDER giữ nguyên các tùy chọn phía sau lệnh để CLI tương ứng đọc.
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="tùy chọn của lệnh")
    args = parser.parse_args()

    # Import trễ: lệnh storage/analyze không cần nạp Playwright/browser-use.
    if args.command == "posts":
        from fb_scraper.posts.collector import run_cli
    elif args.command == "entity":
        from fb_scraper.entities.collector import run_cli
    elif args.command == "search":
        from fb_scraper.search.collector import run_cli
    elif args.command == "agent":
        from fb_scraper.agent.mode import run_cli
    elif args.command == "browser":
        from fb_scraper.browser.connection import run_cli
    elif args.command == "web":
        from fb_scraper.web.server import run_cli
    elif args.command == "analyze":
        from fb_scraper.analysis.cli import run_cli
    elif args.command == "monitor":
        from fb_scraper.analysis.monitor import run_cli
    else:
        from fb_scraper.storage.cli import run_cli

    run_cli(args.arguments)


if __name__ == "__main__":
    main()
