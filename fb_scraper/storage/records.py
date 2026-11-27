"""Bản ghi chuẩn hóa để xuất; projection riêng cho collector và giao diện hiện có."""
import json

from fb_scraper.storage.schema import as_dict
from fb_scraper.storage.source_store import get_source
from fb_scraper.storage.comment_store import comment_rows, comment_view
from fb_scraper.storage.media_store import media_rows


def version_rows(db, post_id):
    cursor = db.execute("SELECT * FROM post_versions WHERE post_id=? ORDER BY id", (post_id,))
    return [as_dict(cursor, row) for row in cursor]


def post_record(db, key, *, comments=True, media=True, versions=True):
    """Đọc một bài kèm bản ghi liên quan.

    Đặt comments/media/versions=False khi chỉ cần trường của bài (ví dụ trước khi cập nhật),
    tránh đọc hàng trăm bình luận chỉ để đổi một con số.
    """
    cursor = db.execute("SELECT * FROM posts WHERE post_key=?", (key,))
    row = cursor.fetchone()
    if row is None:
        return None
    record = as_dict(cursor, row)
    record["metadata"] = json.loads(record["metadata"] or "{}")
    record["source_record"] = get_source(db, record["source_id"])
    record["comments"] = comment_rows(db, record["id"]) if comments else []
    if media:
        media_by_comment = {}
        if record["comments"]:
            cursor = db.execute("SELECT m.* FROM media m JOIN comments c ON c.id=m.comment_id "
                                "WHERE c.post_id=? ORDER BY m.position, m.id", (record["id"],))
            for item in (as_dict(cursor, media_row) for media_row in cursor):
                item["metadata"] = json.loads(item["metadata"] or "{}")
                media_by_comment.setdefault(item["comment_id"], []).append(item)
        for comment in record["comments"]:
            comment["media"] = media_by_comment.get(comment["id"], [])
        record["media"] = media_rows(db, post_id=record["id"])
    else:
        record["media"] = []
    record["versions"] = version_rows(db, record["id"]) if versions else []
    return record


def post_view(record):
    """DTO giữ tên trường của collector; không phải schema SQLite."""
    source, metadata = record["source_record"], record["metadata"]
    entity = source["metadata"].get("entity", {})
    pictures = [item for item in record["media"] if item["media_type"] in {"image", "gif"}]
    image_files = [item["file_path"] for item in pictures if item["file_path"]]
    ocr = [item["metadata"].get("ocr", {"text": item["ocr_text"]}) for item in pictures if item["ocr_text"] is not None]
    return {"id": record["id"], "source_id": record["source_id"], "post_key": record["post_key"],
            "post_id": record["post_key"], "url": record["url"] or "", "source": metadata.get("source", ""),
            "source_type": source["source_type"], "source_label": source["source_label"], "source_ref": source["source_ref"],
            "author": record["author"] or "", "author_url": record["author_url"] or "",
            "text": record["content"] or "", "content": record["content"] or "", "posted_at": record["posted_at"],
            "posted_raw": record["posted_raw"], "likes": record["reaction_count"], "comments": record["comment_count"],
            "shares": record["share_count"], "images": [item["media_url"] for item in pictures if item["media_url"]],
            "image_files": image_files, "ocr": ocr, "links": metadata.get("links", []),
            "top_comments": [comment_view(comment, comment.get("media", ())) for comment in record["comments"]],
            "entity_type": metadata.get("entity_type") or entity.get("entity_type"),
            "entity_id": metadata.get("entity_id") or entity.get("entity_id"),
            "is_shared": bool(record["is_shared"]), "original_post_key": record["original_post_key"],
            "is_sponsored": metadata.get("is_sponsored"),
            "collected_at": record["collected_at"], "last_seen_at": record["last_seen_at"],
            "source_record": source, "comment_records": record["comments"], "media": record["media"],
            "versions": record.get("versions", []),
            "comment_scan_version": metadata.get("comment_scan_version", 0)}


def load_post(db, key):
    record = post_record(db, key)
    return post_view(record) if record else None
