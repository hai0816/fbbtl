"""Chuyển schema cũ lên phiên bản hiện tại trong transaction; giữ bản sao SQLite trước khi đổi."""
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from fb_scraper.storage.migration_v5 import upgrade_v5
from fb_scraper.storage.schema import SCHEMA_VERSION, as_dict, columns, create_tables


def read_rows(db, table):
    cursor = db.execute(f'SELECT * FROM "{table}"')
    return [as_dict(cursor, row) for row in cursor]


def decode_legacy(record, fields):
    for field in fields:
        value = record.get(field)
        if isinstance(value, str):
            record[field] = json.loads(value)
    return record


def hydrate_parent_keys(db):
    """Database tạo từ SQL gốc đã có FK số nhưng chưa có khóa Facebook của cha/gốc."""
    rows = read_rows(db, "comments")
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        parent = by_id.get(row["parent_comment_id"])
        current = row
        visited = set()
        while current.get("parent_comment_id") is not None:
            if current["id"] in visited:
                raise ValueError("Quan hệ bình luận cũ tạo thành chu kỳ.")
            visited.add(current["id"])
            ancestor = by_id.get(current["parent_comment_id"])
            if ancestor is None or ancestor["post_id"] != row["post_id"]:
                raise ValueError("Bình luận cha không thuộc cùng bài viết.")
            current = ancestor
        db.execute("UPDATE comments SET parent_comment_key=coalesce(parent_comment_key,?), "
                   "root_comment_key=coalesce(root_comment_key,?), is_reply=coalesce(is_reply,?) WHERE id=?",
                   (parent["comment_key"] if parent else None, current["comment_key"], int(parent is not None), row["id"]))


def upgrade_v4(db, data_path):
    """v3 → v4: lịch sử tìm kiếm sang bảng, đường dẫn media tương đối, phiên bản đầu của bài."""
    from fb_scraper.storage.media_store import stored_path
    from fb_scraper.storage.post_store import content_hash
    from fb_scraper.storage.source_store import insert_search_hit

    for source in read_rows(db, "sources"):
        metadata = json.loads(source["metadata"] or "{}")
        history = metadata.pop("search_history", None)
        if history is None:
            continue
        for result in history:
            if result.get("keyword") and result.get("entity_type") in {"profile", "group"} and result.get("found_at"):
                insert_search_hit(db, source["id"], result)
        db.execute("UPDATE sources SET metadata=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), source["id"]))

    for media_id, path in db.execute("SELECT id, file_path FROM media WHERE file_path IS NOT NULL").fetchall():
        relative = stored_path(path, data_path)
        if relative != path:
            db.execute("UPDATE media SET file_path=? WHERE id=?", (relative, media_id))

    # Bài đã có trước v4 nhận một phiên bản gốc để lần sửa nội dung sau còn có mốc so sánh.
    rows = db.execute("SELECT id, content, author, reaction_count, comment_count, share_count, "
                      "coalesce(last_seen_at, collected_at) FROM posts p WHERE NOT EXISTS "
                      "(SELECT 1 FROM post_versions v WHERE v.post_id=p.id)").fetchall()
    for post_id, content, author, reactions, comments, shares, seen_at in rows:
        db.execute("INSERT INTO post_versions(post_id,content,content_sha256,author,reaction_count,comment_count,"
                   "share_count,seen_at) VALUES(?,?,?,?,?,?,?,?)",
                   (post_id, content, content_hash(content), author, reactions, comments, shares,
                    seen_at or "1970-01-01T00:00:00+00:00"))


def upgrade(db, database_path, data_path):
    from fb_scraper.storage.source_store import write_source, write_search_result
    from fb_scraper.storage.post_store import write_post

    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError("Database thuộc phiên bản mới hơn; không tự hạ cấp.")
    legacy = "posts" in names and "post_id" in columns(db, "posts")
    if "posts" in names and not legacy and "post_key" not in columns(db, "posts"):
        raise RuntimeError("Schema posts không được hỗ trợ; giữ nguyên database.")
    if version == SCHEMA_VERSION and not legacy:
        return
    history = []
    legacy_history = data_path / "search_results.jsonl"
    # Từ v3 nhật ký cũ đã được nhập; nhập lại khi lên v4 sẽ tạo bản ghi trùng.
    if version < 3 and "search_results" not in names and legacy_history.exists():
        try:
            history = [json.loads(line) for line in legacy_history.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        except (ValueError, OSError):
            raise RuntimeError("Không nhập được search_results.jsonl cũ; giữ nguyên file để kiểm tra.") from None
    if names - {"sqlite_sequence"}:
        backup = database_path.with_name(f"{database_path.stem}.before-v{SCHEMA_VERSION}-{uuid4().hex[:8]}.db")
        with closing(sqlite3.connect(backup)) as target:
            db.backup(target)
        print(f"Đã sao lưu database trước khi chuyển schema: {backup}", flush=True)
    # Tắt khóa ngoại trong lúc chuyển schema (bắt buộc khi dựng lại bảng, xem migration_v5);
    # foreign_key_check bên dưới vẫn chặn commit nếu dữ liệu sau migration sai khóa ngoại.
    foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        old_tables = []
        if legacy:
            for table in ("posts", "profiles", "groups", "search_results"):
                if table in names:
                    db.execute(f'ALTER TABLE "{table}" RENAME TO "legacy_{table}"')
                    old_tables.append(table)
        create_tables(db)
        if not legacy:
            hydrate_parent_keys(db)
        if legacy:
            for table, kind in (("profiles", "profile"), ("groups", "group")):
                if table not in old_tables:
                    continue
                for entity in read_rows(db, "legacy_" + table):
                    decode_legacy(entity, ("work", "education", "rules"))
                    source_type = "page" if entity.get("account_type") == "page" else kind
                    entity["entity_type"] = kind
                    reference = entity.get("url") or f"legacy:{kind}:{entity['entity_id']}"
                    write_source(db, source_type, entity.get("name"), reference, {"entity": entity})
            for record in read_rows(db, "legacy_posts"):
                decode_legacy(record, ("links", "images", "image_files", "ocr", "top_comments"))
                for field in ("url", "source", "author", "text"):
                    record[field] = record.get(field) or ""
                for field in ("links", "images", "image_files", "ocr", "top_comments"):
                    record[field] = record.get(field) or []
                identity = write_post(db, record)
                db.execute("UPDATE posts SET last_seen_at=? WHERE id=?", (record.get("collected_at"), identity))
                if record.get("collected_at"):
                    db.execute("UPDATE post_versions SET seen_at=? WHERE post_id=?", (record["collected_at"], identity))
            if "search_results" in old_tables:
                history = read_rows(db, "legacy_search_results")
        for result in history:
            write_search_result(db, result)
        if legacy:
            # Chuyển schema không phải là một lần truy cập Facebook mới.
            for source in read_rows(db, "sources"):
                metadata = json.loads(source["metadata"])
                seen = db.execute("SELECT MAX(last_seen_at) FROM posts WHERE source_id=?", (source["id"],)).fetchone()[0]
                found = db.execute("SELECT MAX(found_at) FROM search_hits WHERE source_id=?", (source["id"],)).fetchone()[0]
                times = [seen, found, metadata.get("entity", {}).get("collected_at")]
                valid = [value for value in times if value]
                db.execute("UPDATE sources SET last_checked_at=? WHERE id=?", (max(valid) if valid else None, source["id"]))
        for table in old_tables:
            db.execute(f'DROP TABLE "legacy_{table}"')
        upgrade_v4(db, data_path)
        upgrade_v5(db)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Migration tạo khóa ngoại không hợp lệ; đã rollback.")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
