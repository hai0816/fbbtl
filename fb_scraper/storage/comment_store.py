"""Lưu reply bằng ID nội bộ; nối cha sau khi đã lưu đủ các bình luận trong batch."""
import hashlib

from fb_scraper.common.comments import normalize_comments
from fb_scraper.common.content import fuller_text
from fb_scraper.storage.schema import as_dict, json_text, now

COMMENT_COLUMNS = ("post_id", "comment_key", "content", "created_at", "reaction_count", "collected_at",
                   "author", "author_url", "url", "created_raw", "parent_comment_key", "root_comment_key", "is_reply")
# Một câu upsert dùng chung; RETURNING id trả ID nội bộ mà không cần SELECT lại.
UPSERT_COMMENT = (
    f"INSERT INTO comments({','.join(COMMENT_COLUMNS)}) VALUES({','.join('?' for _ in COMMENT_COLUMNS)}) "
    "ON CONFLICT(comment_key) DO UPDATE SET "
    + ",".join(f"{column}=excluded.{column}" for column in COMMENT_COLUMNS if column not in {"comment_key", "post_id"})
    + " RETURNING id"
)


def local_key(post_key, comment):
    if comment.get("comment_id"):
        return comment["comment_id"]
    fingerprint = [post_key, comment.get("author"), comment["text"],
                   comment.get("parent_comment_id"), comment.get("root_comment_id")]
    return "local:" + hashlib.sha256(json_text(fingerprint).encode("utf-8")).hexdigest()[:32]


def comment_rows(db, post_id):
    cursor = db.execute("SELECT * FROM comments WHERE post_id=? ORDER BY id", (post_id,))
    return [as_dict(cursor, row) for row in cursor]


def delete_comments(db, post_id):
    """Xóa bình luận và media của bình luận thuộc một bài; trả số bình luận đã xóa."""
    db.execute("DELETE FROM media WHERE comment_id IN (SELECT id FROM comments WHERE post_id=?)", (post_id,))
    db.execute("UPDATE comments SET parent_comment_id=NULL WHERE post_id=?", (post_id,))
    return db.execute("DELETE FROM comments WHERE post_id=?", (post_id,)).rowcount


def resolve_parents(db, post_id, rows=None):
    rows = comment_rows(db, post_id) if rows is None else rows
    by_key = {row["comment_key"]: row for row in rows}
    for row in rows:
        parent_key = row["parent_comment_key"]
        visited = {row["comment_key"]}
        current = parent_key
        while current in by_key:
            if current in visited:
                raise ValueError("Quan hệ bình luận tạo thành chu kỳ.")
            visited.add(current)
            current = by_key[current]["parent_comment_key"]
        parent = by_key.get(parent_key)
        if parent_key and not parent:
            existing = db.execute("SELECT post_id FROM comments WHERE comment_key=?", (parent_key,)).fetchone()
            if existing and existing[0] != post_id:
                raise ValueError("Bình luận cha thuộc một bài viết khác.")
        if parent and row["parent_comment_id"] != parent["id"]:
            db.execute("UPDATE comments SET parent_comment_id=? WHERE id=?", (parent["id"], row["id"]))
            row["parent_comment_id"] = parent["id"]


def write_comments(db, post_id, post_key, comments):
    """Thêm/cập nhật bình luận; không xóa bình luận cũ vắng mặt trong batch (DOM có thể chỉ tải một phần)."""
    from fb_scraper.storage.media_store import write_media
    # Đọc một lần các bình luận đã có của bài thay vì SELECT riêng cho từng bình luận.
    existing = {row["comment_key"]: row for row in comment_rows(db, post_id)}
    for comment in normalize_comments(comments):
        key = local_key(post_key, comment)
        previous = existing.get(key)
        if previous is None:
            cursor = db.execute("SELECT * FROM comments WHERE comment_key=?", (key,))
            row = cursor.fetchone()
            if row is not None:
                raise ValueError("comment_key đã thuộc một bài viết khác.")
            previous = {}
        values = {"post_id": post_id, "comment_key": key,
                  "content": fuller_text(previous.get("content"), comment["text"]),
                  "created_at": comment.get("created_at") or previous.get("created_at"),
                  "reaction_count": comment["reaction_count"] if comment.get("reaction_count") is not None else previous.get("reaction_count"),
                  "collected_at": previous.get("collected_at") or now(),
                  "author": comment.get("author") or previous.get("author"),
                  "author_url": comment.get("author_url") or previous.get("author_url"),
                  "url": comment.get("url") or previous.get("url"),
                  "created_raw": comment.get("posted_raw") or previous.get("created_raw"),
                  "parent_comment_key": comment.get("parent_comment_id") or previous.get("parent_comment_key"),
                  "root_comment_key": comment.get("root_comment_id") or previous.get("root_comment_key"),
                  "is_reply": comment["is_reply"] if comment["is_reply"] is not None else previous.get("is_reply")}
        # fetchall() đọc hết kết quả RETURNING để câu lệnh kết thúc trước lệnh kế tiếp.
        identity = db.execute(UPSERT_COMMENT, tuple(values[column] for column in COMMENT_COLUMNS)).fetchall()[0][0]
        existing[key] = {**previous, **values, "id": identity,
                         "parent_comment_id": previous.get("parent_comment_id")}
        if comment.get("media"):
            write_media(db, comment["media"], comment_id=identity)
    resolve_parents(db, post_id, list(existing.values()))


def comment_view(row, media=()):
    return {"comment_id": None if row["comment_key"].startswith("local:") else row["comment_key"],
            "parent_comment_id": row["parent_comment_key"], "root_comment_id": row["root_comment_key"],
            "is_reply": None if row["is_reply"] is None else bool(row["is_reply"]),
            "author": row["author"], "author_url": row["author_url"], "text": row["content"] or "",
            "url": row["url"], "posted_raw": row["created_raw"], "created_at": row["created_at"],
            "reaction_count": row["reaction_count"], "media": list(media)}
