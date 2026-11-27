"""Tạo schema mà không implicit commit; mọi kết nối ghi đều bật khóa ngoại."""
from pathlib import Path
import json
import sqlite3
from datetime import datetime, timezone

TABLES = ("sources", "posts", "comments", "media", "post_versions", "search_hits",
          "keywords", "keyword_suggestions", "detection_rules", "analysis_settings",
          "post_analysis", "risk_assessments", "import_batches")
# v4: thêm post_versions, search_hits; đường dẫn media tương đối so với data/.
# v5: nguồn 'dataset', theo dõi nguồn, bảng từ khóa/rule/tiền xử lý/điểm rủi ro/nhập dữ liệu.
# v6: keywords.stats — thống kê hiệu quả từng từ khóa (đã thấy/đã lưu/bị bộ lọc loại).
SCHEMA_VERSION = 6
EXTRA_COLUMNS = {
    "sources": {"metadata": "TEXT NOT NULL DEFAULT '{}'",
                "monitor_enabled": "INTEGER NOT NULL DEFAULT 0 CHECK(monitor_enabled IN (0,1))",
                "collection_status": "TEXT", "last_error": "TEXT"},
    "posts": {"url": "TEXT", "author": "TEXT", "author_url": "TEXT", "posted_raw": "TEXT",
              "reaction_count": "INTEGER", "comment_count": "INTEGER", "share_count": "INTEGER",
              "metadata": "TEXT NOT NULL DEFAULT '{}'"},
    "comments": {"author": "TEXT", "author_url": "TEXT", "url": "TEXT", "created_raw": "TEXT",
                 "parent_comment_key": "TEXT", "root_comment_key": "TEXT",
                 "is_reply": "INTEGER CHECK(is_reply IN (0,1) OR is_reply IS NULL)"},
    "media": {"media_url": "TEXT", "metadata": "TEXT NOT NULL DEFAULT '{}'"},
    "keywords": {"stats": "TEXT NOT NULL DEFAULT '{}'"},
}


def now():
    return datetime.now(timezone.utc).isoformat()


def json_text(value):
    return json.dumps(value, ensure_ascii=False)


def create_tables(db):
    for table, extra in EXTRA_COLUMNS.items():
        existing = columns(db, table)
        if existing and (table != "posts" or "post_key" in existing):
            for name, definition in extra.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
    pending = ""
    for line in Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").splitlines(True):
        pending += line
        if sqlite3.complete_statement(pending):
            db.execute(pending)
            pending = ""
    if pending.strip():
        raise RuntimeError("Schema SQL chưa hoàn chỉnh.")


def columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def as_dict(cursor, row):
    return dict(zip((column[0] for column in cursor.description), row))
