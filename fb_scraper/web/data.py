"""Đọc 4 bảng chuẩn hóa; API dùng projection cho giao diện và xuất bản ghi theo schema."""
from contextlib import contextmanager
import json
import sqlite3

from fb_scraper.storage import database as storage
from fb_scraper.storage.records import post_record, post_view


@contextmanager
def database():
    if not storage.DB_PATH.exists():
        yield None
        return
    db = sqlite3.connect(storage.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.create_function("casefold", 1, lambda value: (value or "").casefold())
    try:
        yield db
    finally:
        db.close()


def has_posts(db):
    return db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE name='posts'").fetchone()


def summary():
    with database() as db:
        if not has_posts(db):
            return {"posts": 0, "comments": 0, "images": 0}
        return {"posts": db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
                "comments": db.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
                "images": db.execute("SELECT COUNT(*) FROM media WHERE media_type IN ('image','gif')").fetchone()[0]}


def list_posts(query="", offset=0, limit=12):
    with database() as db:
        if not has_posts(db):
            return {"items": [], "total": 0}
        where = "WHERE instr(casefold(coalesce(p.content,'') || ' ' || coalesce(p.author,'') || ' ' || s.source_label || ' ' || p.metadata), ?) > 0"
        params = (query.casefold(),)
        base = f"FROM posts p JOIN sources s ON s.id=p.source_id {where}"
        total = db.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
        keys = db.execute("SELECT p.post_key " + base + " ORDER BY p.last_seen_at DESC,p.id DESC LIMIT ? OFFSET ?",
                          (*params, limit, offset)).fetchall()
        items = [post_view(post_record(db, key[0])) for key in keys]
        _attach_risk(db, items)
        return {"items": items, "total": total}


def _attach_risk(db, items):
    """Gắn điểm rủi ro (nếu bài đã được phân tích) để bảng bài viết hiện mức rủi ro ngay."""
    if not items or not db.execute("SELECT 1 FROM sqlite_master WHERE name='risk_assessments'").fetchone():
        return
    marks = ",".join("?" * len(items))
    risks = {row[0]: row[1:] for row in db.execute(
        f"SELECT post_id, risk_score, risk_level FROM risk_assessments WHERE post_id IN ({marks})",
        [item["id"] for item in items])}
    for item in items:
        if item["id"] in risks:
            item["risk"] = {"score": risks[item["id"]][0], "level": risks[item["id"]][1]}


def get_post(identity):
    with database() as db:
        return storage.load_post(db, identity) if has_posts(db) else None


def export_lines():
    with database() as db:
        if has_posts(db):
            for (key,) in db.execute("SELECT post_key FROM posts ORDER BY id"):
                yield json.dumps(post_record(db, key), ensure_ascii=False) + "\n"
