"""Lệnh storage: xuất lại JSONL từ SQLite, không chạy trình duyệt."""
import argparse

from fb_scraper.storage import database as storage


def export_all(db):
    """Xuất sources/posts/comments/media, dùng ID khóa ngoại giống SQLite."""
    exported = storage.export_jsonl(db)
    for table in storage.EXPORTED_TABLES:
        print(f"{table}: {storage.count_rows(db, table)} bản ghi -> {storage.DATA / (table + '.jsonl')}")
    return exported


def run_repair():
    import json
    from fb_scraper.storage.repair import repair
    db = storage.init_db()
    try:
        stats = repair(db)
        print(f"Đã điền thời gian cho {stats['comment_times']} bình luận và {stats['post_times']} bài; "
              f"đánh dấu {stats['sponsored']} bài quảng cáo.")
        for group in stats["duplicate_groups"]:
            print("Bài nghi trùng (không có permalink):", json.dumps(group, ensure_ascii=False))
        for post in stats["empty_posts"]:
            print("Bài không có nội dung:", json.dumps(post, ensure_ascii=False))
        storage.export_jsonl(db)
    finally:
        db.close()


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description="Xuất lại JSONL từ SQLite, không chạy trình duyệt.")
    parser.add_argument("--export-jsonl", action="store_true", help="xuất mọi bảng (sources, posts, comments, media, post_versions, search_hits) từ SQLite")
    parser.add_argument("--repair", action="store_true",
                        help="điền thời gian bình luận/bài còn trống, đánh dấu quảng cáo, báo bài nghi trùng (không xóa)")
    args = parser.parse_args(argv)
    if args.repair:
        run_repair()
        if not args.export_jsonl:
            return
    if not args.export_jsonl:
        parser.print_help()
        return

    db = storage.init_db()
    try:
        exported = export_all(db)
    finally:
        db.close()
    raise SystemExit(0 if exported else 1)
