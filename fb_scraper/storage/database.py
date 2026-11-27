"""SQLite chuẩn hóa: sources → posts → comments/media (+ post_versions, search_hits).

SQLite là nơi lưu chính, commit sau từng bài; JSONL chỉ là snapshot xuất khi kết thúc lượt.
"""
import json
import sqlite3

from fb_scraper.common.models import Post
from fb_scraper.common.paths import PROJECT_ROOT
from fb_scraper.storage.comment_store import delete_comments
from fb_scraper.storage.files import atomic_write, export_rows
from fb_scraper.storage.migration import upgrade
from fb_scraper.storage.post_store import write_post
from fb_scraper.storage.records import load_post, post_record  # noqa: F401  (load_post: API cho collector)
from fb_scraper.storage.schema import TABLES
# save_entity được dùng qua storage.save_entity bởi entities/collector.py.
from fb_scraper.storage.source_store import save_entity, write_search_result  # noqa: F401

DATA = PROJECT_ROOT / "data"
IMG_DIR = DATA / "images"
DB_PATH = DATA / "posts.db"
JSONL_PATH = DATA / "posts.jsonl"


def configure(db):
    """Thiết lập cho mọi kết nối ghi.

    WAL: giao diện web đọc được trong lúc worker đang ghi, không bị khóa/503.
    synchronous=NORMAL: an toàn với WAL (không hỏng DB khi mất điện, chỉ có thể mất
    giao dịch cuối) và commit sau từng bài nhanh hơn nhiều so với FULL.
    """
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")


def init_db():
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    try:
        configure(db)
        upgrade(db, DB_PATH, DATA)
    except BaseException:
        db.close()
        raise
    return db


def save_post(db, post):
    """Thêm/cập nhật bài; trường bị bỏ qua giữ nguyên, danh sách rỗng không xóa dữ liệu."""
    with db:
        return write_post(db, post)


def clear_comments(db, post_key):
    """Xóa toàn bộ bình luận (và media của bình luận) của một bài. Chỉ gọi khi thật sự muốn xóa."""
    with db:
        row = db.execute("SELECT id FROM posts WHERE post_key=?", (post_key,)).fetchone()
        if row is None:
            return 0
        return delete_comments(db, row[0])


def mark_comments_scanned(db, post_key, version):
    """Ghi phiên bản cách đọc bình luận đã dùng cho bài, để lần sau biết có cần quét lại không."""
    with db:
        db.execute("UPDATE posts SET metadata=json_set(metadata, '$.comment_scan_version', ?) WHERE post_key=?",
                   (version, post_key))


def known_ids(db):
    return {row[0] for row in db.execute("SELECT post_key FROM posts")}


def save_search_result(db, result):
    with db:
        return write_search_result(db, result)


# Các bảng được xuất thành data/<bảng>.jsonl bởi export_jsonl().
EXPORTED_TABLES = ("posts", "sources", "comments", "media", "post_versions", "search_hits", "risk_assessments")


def count_rows(db, table):
    if table not in TABLES:
        raise ValueError("Bảng không thuộc schema hiện tại.")
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def export_sources(db):
    return export_rows(db, DATA / "sources.jsonl", "SELECT * FROM sources ORDER BY id", ("metadata",))


def export_jsonl(db):
    """Xuất mỗi bảng một file JSONL; posts.jsonl kèm bình luận/media/lịch sử để đọc một dòng là đủ."""
    statuses = [export_rows(db, JSONL_PATH, "SELECT * FROM posts ORDER BY id", ("metadata",),
                            transform=lambda row: post_record(db, row["post_key"]))]
    statuses.append(export_sources(db))
    statuses.append(export_rows(db, DATA / "comments.jsonl", "SELECT * FROM comments ORDER BY id"))
    statuses.append(export_rows(db, DATA / "media.jsonl", "SELECT * FROM media ORDER BY id", ("metadata",)))
    statuses.append(export_rows(db, DATA / "post_versions.jsonl", "SELECT * FROM post_versions ORDER BY id"))
    statuses.append(export_rows(db, DATA / "search_hits.jsonl", "SELECT * FROM search_hits ORDER BY id"))
    # v5: kết quả chấm điểm rủi ro (bằng chứng, rule kích hoạt) để báo cáo/kiểm chứng ngoài ứng dụng.
    statuses.append(export_rows(db, DATA / "risk_assessments.jsonl", "SELECT * FROM risk_assessments ORDER BY post_id",
                                ("matched_rules", "evidence", "context")))
    return all(statuses)


def export_agent_output(posts, db=None):
    own_connection = db is None
    if own_connection:
        db = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        payload = {"posts": [post_record(db, Post.model_validate(post).post_id) for post in posts]}
        if any(record is None for record in payload["posts"]):
            raise ValueError("Agent có bài chưa được lưu vào database.")
        return atomic_write(DATA / "data_agent.json", lambda output: json.dump(payload, output, ensure_ascii=False, indent=2))
    finally:
        if own_connection:
            db.close()


def print_storage_paths():
    print("Thư mục dữ liệu:", DATA, flush=True)
    print("JSONL:", JSONL_PATH, flush=True)
    print("SQLite (sources/posts/comments/media/post_versions/search_hits):", DB_PATH, flush=True)
