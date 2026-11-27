"""Sửa dữ liệu đã thu thập bằng bộ đọc mới, không cần mở Facebook. Không xóa bản ghi nào.

- comments.created_at / posts.posted_at còn trống: đọc lại từ chuỗi gốc (created_raw / posted_raw).
  Thời gian tương đối ("2 giờ") được tính theo thời điểm thu thập (collected_at), không theo hiện tại.
- posts.metadata.is_sponsored: suy ra từ link chiến dịch trả phí đã lưu (utm_medium=paid…).
- Báo cáo bài nghi trùng: bài không có permalink cùng tác giả + cùng đoạn đầu nội dung.
"""
from collections import defaultdict
from datetime import datetime, timezone
import json

from fb_scraper.common.times import parse_post_time
from fb_scraper.common.urls import content_fingerprint
from fb_scraper.posts.parser import AD_LINK_RE


def _base_time(value):
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except ValueError:
        parsed = None
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def repair(db):
    stats = {"comment_times": 0, "post_times": 0, "sponsored": 0, "duplicate_groups": []}
    with db:
        rows = db.execute("SELECT id, created_raw, collected_at FROM comments "
                          "WHERE created_at IS NULL AND coalesce(created_raw,'')!=''").fetchall()
        for identity, raw, collected in rows:
            value = parse_post_time(raw, _base_time(collected))
            if value:
                db.execute("UPDATE comments SET created_at=? WHERE id=?", (value, identity))
                stats["comment_times"] += 1
        rows = db.execute("SELECT id, posted_raw, collected_at FROM posts "
                          "WHERE posted_at IS NULL AND coalesce(posted_raw,'')!=''").fetchall()
        for identity, raw, collected in rows:
            value = parse_post_time(raw, _base_time(collected))
            if value:
                db.execute("UPDATE posts SET posted_at=? WHERE id=?", (value, identity))
                stats["post_times"] += 1
        for identity, metadata in db.execute("SELECT id, metadata FROM posts").fetchall():
            data = json.loads(metadata or "{}")
            if data.get("is_sponsored") is None and any(AD_LINK_RE.search(link) for link in data.get("links", [])):
                db.execute("UPDATE posts SET metadata=json_set(metadata,'$.is_sponsored',json('true')) WHERE id=?",
                           (identity,))
                stats["sponsored"] += 1
    groups = defaultdict(list)
    for identity, key, author, content in db.execute(
            "SELECT id, post_key, author, content FROM posts WHERE coalesce(url,'')=''"):
        groups[content_fingerprint(content, author)].append({"id": identity, "post_key": key,
                                                             "length": len(content or "")})
    for fingerprint, posts in groups.items():
        if len(posts) > 1 and fingerprint.split("|", 1)[1]:
            stats["duplicate_groups"].append(posts)
    empty = db.execute("SELECT id, post_key, author FROM posts WHERE trim(coalesce(content,''))=''").fetchall()
    stats["empty_posts"] = [{"id": row[0], "post_key": row[1], "author": row[2]} for row in empty]
    return stats
