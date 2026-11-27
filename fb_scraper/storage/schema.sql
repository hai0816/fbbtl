-- Bốn bảng chính; các cột bổ sung giữ tên, URL và metadata đã thu thập.
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- v5: 'dataset' cho dữ liệu nhập từ CSV/JSON/SQLite (phương án dự phòng khi không thu thập trực tiếp).
    source_type TEXT NOT NULL CHECK(source_type IN ('profile', 'page', 'group', 'dataset')),
    source_label TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    -- v5: quản lý nguồn nghiên cứu được phép theo dõi định kỳ.
    monitor_enabled INTEGER NOT NULL DEFAULT 0 CHECK(monitor_enabled IN (0, 1)),
    collection_status TEXT,
    last_error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS sources_ref_unique ON sources(source_ref) WHERE source_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    post_key TEXT NOT NULL UNIQUE,
    content TEXT,
    posted_at TEXT,
    is_shared INTEGER NOT NULL DEFAULT 0 CHECK(is_shared IN (0, 1)),
    original_post_key TEXT,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT,
    url TEXT,
    author TEXT,
    author_url TEXT,
    posted_raw TEXT,
    reaction_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (source_id) REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS posts_source_idx ON posts(source_id);
-- Danh sách kết quả trên giao diện sắp theo lần thấy gần nhất.
CREATE INDEX IF NOT EXISTS posts_last_seen_idx ON posts(last_seen_at DESC, id DESC);
-- source_for_post() tìm nguồn theo entity_id trong metadata mà không quét/parse cả bảng.
CREATE INDEX IF NOT EXISTS sources_entity_idx ON sources(json_extract(metadata, '$.entity.entity_id'));

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    parent_comment_id INTEGER,
    comment_key TEXT NOT NULL UNIQUE,
    content TEXT,
    created_at TEXT,
    reaction_count INTEGER DEFAULT 0,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    author TEXT,
    author_url TEXT,
    url TEXT,
    created_raw TEXT,
    parent_comment_key TEXT,
    root_comment_key TEXT,
    is_reply INTEGER CHECK(is_reply IN (0, 1) OR is_reply IS NULL),
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (parent_comment_id) REFERENCES comments(id)
);
CREATE INDEX IF NOT EXISTS comments_post_idx ON comments(post_id);
CREATE INDEX IF NOT EXISTS comments_parent_idx ON comments(parent_comment_id);
CREATE TRIGGER IF NOT EXISTS comments_parent_insert BEFORE INSERT ON comments
WHEN NEW.parent_comment_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'Comment parent must belong to the same post')
    WHERE NOT EXISTS (SELECT 1 FROM comments WHERE id=NEW.parent_comment_id AND post_id=NEW.post_id);
END;
CREATE TRIGGER IF NOT EXISTS comments_parent_update BEFORE UPDATE OF parent_comment_id, post_id ON comments
WHEN NEW.parent_comment_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'Comment parent must belong to the same post')
    WHERE NOT EXISTS (SELECT 1 FROM comments WHERE id=NEW.parent_comment_id AND post_id=NEW.post_id);
    SELECT RAISE(ABORT, 'Comment cycle') WHERE NEW.parent_comment_id=NEW.id OR EXISTS (
        WITH RECURSIVE ancestors(id) AS (
            SELECT NEW.parent_comment_id UNION
            SELECT c.parent_comment_id FROM comments c JOIN ancestors a ON c.id=a.id
            WHERE c.parent_comment_id IS NOT NULL
        ) SELECT 1 FROM ancestors WHERE id=NEW.id
    );
END;
CREATE TRIGGER IF NOT EXISTS comments_post_update BEFORE UPDATE OF post_id ON comments
WHEN NEW.post_id != OLD.post_id BEGIN
    SELECT RAISE(ABORT, 'Comment children must belong to the same post')
    WHERE EXISTS (SELECT 1 FROM comments WHERE parent_comment_id=OLD.id AND post_id != NEW.post_id);
END;

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER,
    comment_id INTEGER,
    media_type TEXT NOT NULL CHECK(media_type IN ('image', 'video', 'gif')),
    file_path TEXT,
    file_hash TEXT,
    ocr_text TEXT,
    position INTEGER DEFAULT 0,
    media_url TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (comment_id) REFERENCES comments(id),
    CHECK ((post_id IS NOT NULL AND comment_id IS NULL) OR (post_id IS NULL AND comment_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS media_post_idx ON media(post_id);
CREATE INDEX IF NOT EXISTS media_comment_idx ON media(comment_id);
CREATE UNIQUE INDEX IF NOT EXISTS media_post_url_unique ON media(post_id,media_url) WHERE post_id IS NOT NULL AND media_url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS media_comment_url_unique ON media(comment_id,media_url) WHERE comment_id IS NOT NULL AND media_url IS NOT NULL;

-- Lịch sử bài viết: một dòng mỗi khi nội dung/tác giả/số liệu đổi so với lần ghi trước.
-- posts giữ trạng thái mới nhất; bảng này giữ bằng chứng "bài từng ghi X, sau đó sửa thành Y".
CREATE TABLE IF NOT EXISTS post_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    content TEXT,
    content_sha256 TEXT NOT NULL,
    author TEXT,
    reaction_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    seen_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);
CREATE INDEX IF NOT EXISTS post_versions_post_idx ON post_versions(post_id, id);

-- Nhật ký tìm kiếm: mỗi lần phát hiện một nguồn là một dòng, kể cả URL lặp ở từ khóa khác.
CREATE TABLE IF NOT EXISTS search_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('profile', 'group')),
    name TEXT,
    search_url TEXT,
    found_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS search_hits_source_idx ON search_hits(source_id);
CREATE INDEX IF NOT EXISTS search_hits_keyword_idx ON search_hits(keyword);

-- ===== v5: trinh sát & phát hiện dấu hiệu lừa đảo (fb_scraper/analysis) =====
-- Bộ từ khóa trinh sát; origin cho biết từ khóa mặc định, người dùng thêm hay được duyệt từ đề xuất.
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    keyword_norm TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK(category IN ('JOB', 'INVESTMENT', 'OTHER')),
    origin TEXT NOT NULL DEFAULT 'user' CHECK(origin IN ('default', 'user', 'expansion')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    -- v6: cộng dồn qua các lượt: runs, seen (bài thấy trên trang tìm kiếm), saved, existing, filtered.
    stats TEXT NOT NULL DEFAULT '{}'
);

-- Đề xuất mở rộng truy vấn: không tự thêm vào keywords khi chưa được người dùng duyệt.
CREATE TABLE IF NOT EXISTS keyword_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL,
    phrase_norm TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'OTHER',
    score REAL NOT NULL DEFAULT 0,
    support INTEGER NOT NULL DEFAULT 0,
    example_post_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'ACCEPTED', 'REJECTED')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (example_post_id) REFERENCES posts(id)
);

-- Bộ rule; definition là JSON điều kiện so khớp. Người dùng bật/tắt, đổi trọng số tại đây.
CREATE TABLE IF NOT EXISTS detection_rules (
    rule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('JOB', 'INVESTMENT', 'COMMON', 'COMBO')),
    description TEXT NOT NULL,
    definition TEXT NOT NULL,
    weight INTEGER NOT NULL CHECK(weight BETWEEN 0 AND 100),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    priority INTEGER NOT NULL DEFAULT 2,
    dedup_group TEXT NOT NULL,
    customized INTEGER NOT NULL DEFAULT 0 CHECK(customized IN (0, 1)),
    updated_at TEXT NOT NULL
);

-- Cấu hình phân tích (ngưỡng mức rủi ro, ngưỡng tương đồng...), giá trị là JSON.
CREATE TABLE IF NOT EXISTS analysis_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Kết quả tiền xử lý: dữ liệu gốc vẫn ở posts.content và media.ocr_text, không bị ghi đè.
CREATE TABLE IF NOT EXISTS post_analysis (
    post_id INTEGER PRIMARY KEY,
    normalized_text TEXT NOT NULL,
    ocr_text TEXT NOT NULL DEFAULT '',
    combined_text TEXT NOT NULL,
    folded_text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    duplicate_of INTEGER,
    features TEXT NOT NULL DEFAULT '{}',
    comment_context TEXT NOT NULL DEFAULT '{}',
    sentence_count INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    pipeline_version INTEGER NOT NULL,
    analyzed_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (duplicate_of) REFERENCES posts(id)
);
CREATE INDEX IF NOT EXISTS post_analysis_hash_idx ON post_analysis(text_hash);

-- Điểm rủi ro: chỉ là mức ưu tiên kiểm tra, KHÔNG phải xác suất bài viết là lừa đảo.
CREATE TABLE IF NOT EXISTS risk_assessments (
    post_id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 0 AND 100),
    risk_level TEXT NOT NULL CHECK(risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
    matched_rules TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '[]',
    explanation TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(review_status IN ('PENDING', 'CONFIRMED_SCAM', 'FALSE_POSITIVE', 'NEEDS_INFO')),
    reviewer_note TEXT,
    reviewed_at TEXT,
    ruleset_hash TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);
CREATE INDEX IF NOT EXISTS risk_level_idx ON risk_assessments(risk_level, risk_score DESC);

-- Nhật ký mỗi lần nhập dataset: số dòng thêm/cập nhật/trùng/lỗi.
CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    file_format TEXT NOT NULL,
    total_rows INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    imported_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);
