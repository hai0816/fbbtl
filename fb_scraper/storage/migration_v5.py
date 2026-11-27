"""v4 → v5: cho phép nguồn 'dataset' và thêm cột theo dõi nguồn, giữ nguyên mọi dòng và ID.

SQLite không sửa được CHECK của bảng có sẵn, nên bảng sources được dựng lại theo quy trình
chuẩn của SQLite (https://sqlite.org/lang_altertable.html#otheralter):
1. migration.upgrade() tắt foreign_keys TRƯỚC khi mở transaction (trong transaction PRAGMA này
   không có tác dụng), nhờ vậy DROP bảng cũ không xóa ngầm/kiểm tra bài viết đang tham chiếu.
2. Tạo sources_v5, chép toàn bộ dòng (giữ nguyên id), xóa bảng cũ, đổi tên sources_v5 → sources.
   Khóa ngoại của posts/search_hits/import_batches vẫn trỏ tới tên "sources" nên không phải sửa.
3. Tạo lại index. Bên gọi chạy PRAGMA foreign_key_check trước khi commit.
Toàn bộ chạy trong transaction của migration.upgrade(); lỗi sẽ rollback, file backup vẫn còn.
"""
from fb_scraper.storage.schema import columns

SOURCES_V5 = """CREATE TABLE sources_v5 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL CHECK(source_type IN ('profile', 'page', 'group', 'dataset')),
    source_label TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    monitor_enabled INTEGER NOT NULL DEFAULT 0 CHECK(monitor_enabled IN (0, 1)),
    collection_status TEXT,
    last_error TEXT
)"""
SOURCE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS sources_ref_unique ON sources(source_ref) WHERE source_ref IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS sources_entity_idx ON sources(json_extract(metadata, '$.entity.entity_id'))",
)


def sources_allow_dataset(db):
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'").fetchone()
    return sql is not None and "'dataset'" in sql[0]


def upgrade_v5(db):
    if sources_allow_dataset(db):
        return False
    keep = [name for name in ("id", "source_type", "source_label", "source_ref", "created_at",
                              "last_checked_at", "metadata", "monitor_enabled", "collection_status", "last_error")
            if name in columns(db, "sources")]
    before = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if db.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Phải tắt foreign_keys trước transaction khi dựng lại bảng sources.")
    db.execute(SOURCES_V5)
    names = ",".join(keep)
    db.execute(f"INSERT INTO sources_v5({names}) SELECT {names} FROM sources")
    db.execute("DROP TABLE sources")
    db.execute("ALTER TABLE sources_v5 RENAME TO sources")
    for statement in SOURCE_INDEXES:
        db.execute(statement)
    after = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if before != after:
        raise RuntimeError("Dựng lại bảng sources làm thay đổi số dòng; đã hủy migration.")
    return True
