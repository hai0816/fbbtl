"""Ghi toàn bộ bài/nguồn/bình luận/media/lịch sử trong một transaction của bên gọi."""
import hashlib

from fb_scraper.common.models import Post
from fb_scraper.common.content import fuller_text
from fb_scraper.storage.comment_store import write_comments
from fb_scraper.storage.media_store import write_media, post_media_input
from fb_scraper.storage.records import post_record, post_view
from fb_scraper.storage.source_store import source_for_post
from fb_scraper.storage.schema import json_text, now

MEDIA_FIELDS = ("media", "images", "image_files", "ocr")
VERSION_FIELDS = ("content_sha256", "author", "reaction_count", "comment_count", "share_count")


def content_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def record_version(db, post_id, values, seen_at):
    """Thêm một dòng lịch sử khi nội dung/tác giả/số liệu khác lần ghi gần nhất."""
    current = {"content_sha256": content_hash(values["content"]), "author": values["author"],
               "reaction_count": values["reaction_count"], "comment_count": values["comment_count"],
               "share_count": values["share_count"]}
    last = db.execute(f"SELECT {','.join(VERSION_FIELDS)} FROM post_versions WHERE post_id=? "
                      "ORDER BY id DESC LIMIT 1", (post_id,)).fetchone()
    if last is not None and tuple(last) == tuple(current[field] for field in VERSION_FIELDS):
        return False
    db.execute("INSERT INTO post_versions(post_id,content,content_sha256,author,reaction_count,comment_count,"
               "share_count,seen_at) VALUES(?,?,?,?,?,?,?,?)",
               (post_id, values["content"], current["content_sha256"], current["author"],
                current["reaction_count"], current["comment_count"], current["share_count"], seen_at))
    return True


def write_post(db, post):
    """Thêm/cập nhật một bài.

    - Trường bị bỏ qua giữ giá trị cũ; danh sách rỗng (top_comments=[], images=[]) cũng không
      xóa dữ liệu đã có. Muốn xóa bình luận phải gọi database.clear_comments() tường minh.
    - Chỉ đọc lại media cũ khi bên gọi gửi trường media; không bao giờ đọc lại toàn bộ bình luận.
    """
    incoming = Post.model_validate(post).model_dump(exclude_unset=True)
    key = incoming.get("post_id")
    if not key:
        raise ValueError("Bài viết phải có post_id/post_key trước khi lưu.")
    touches_media = any(incoming.get(field) for field in MEDIA_FIELDS)
    previous_record = post_record(db, key, comments=False, media=touches_media, versions=False)
    previous = post_view(previous_record) if previous_record else {}
    merged = {**{field: value for field, value in previous.items() if value is not None}, **incoming}
    if "source_id" not in incoming and any(
        field in incoming and incoming[field] != previous.get(field)
        for field in ("source_ref", "source_type", "entity_type", "entity_id")
    ):
        merged["source_id"] = None
    record = Post.model_validate(merged).model_dump()
    for field in ("author", "author_url", "url", "posted_raw", "posted_at", "likes", "comments", "shares"):
        if record[field] in (None, "") and previous.get(field) not in (None, ""):
            record[field] = previous[field]
    # Danh sách rỗng không được xóa link đã lưu.
    if not record["links"] and previous.get("links"):
        record["links"] = previous["links"]
    source_id = source_for_post(db, record)
    # Giữ các khóa metadata khác đã lưu (ví dụ comment_scan_version) khi cập nhật bài.
    metadata = {**(previous_record["metadata"] if previous_record else {}),
                "source": record["source"], "links": record["links"],
                "entity_type": record["entity_type"], "entity_id": record["entity_id"]}
    if record.get("is_sponsored") is not None:
        metadata["is_sponsored"] = record["is_sponsored"]
    timestamp = now()
    # source_id đã lưu chỉ đổi khi caller thực sự cung cấp nguồn mới.
    values = {"source_id": source_id, "post_key": key,
              "content": fuller_text(previous.get("text"), record["text"]),
              "posted_at": record["posted_at"], "is_shared": int(record["is_shared"]),
              "original_post_key": record["original_post_key"],
              "collected_at": previous.get("collected_at") or record.get("collected_at") or timestamp,
              "last_seen_at": timestamp, "url": record["url"], "author": record["author"],
              "author_url": record["author_url"], "posted_raw": record["posted_raw"],
              "reaction_count": record["likes"], "comment_count": record["comments"],
              "share_count": record["shares"], "metadata": json_text(metadata)}
    columns = tuple(values)
    updates = ','.join(f'{field}=excluded.{field}' for field in columns if field not in {"post_key", "collected_at"})
    identity = db.execute(f"INSERT INTO posts({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                          f"ON CONFLICT(post_key) DO UPDATE SET {updates} RETURNING id",
                          tuple(values.values())).fetchall()[0][0]
    record_version(db, identity, values, timestamp)
    if incoming.get("top_comments"):
        write_comments(db, identity, key, incoming["top_comments"])
    if touches_media:
        write_media(db, post_media_input(record), post_id=identity)
    return identity
