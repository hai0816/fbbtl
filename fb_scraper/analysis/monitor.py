"""Quản lý nguồn nghiên cứu được phép truy cập và thu thập định kỳ.

Tái sử dụng nguyên luồng thu thập của giao diện (web.worker.collect): cùng Chrome/hồ sơ đăng nhập,
cùng giới hạn nội dung công khai, nhóm chỉ thu thập khi xác định là Công khai. Không vượt CAPTCHA,
đăng nhập hay giới hạn truy cập: nguồn lỗi được ghi collection_status='error' kèm thông báo.

Chạy định kỳ: python main.py monitor --once (đặt trong Windows Task Scheduler), hoặc --loop.
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
import time

from fb_scraper.storage.schema import as_dict, now

SOURCE_FIELDS = ("id", "source_type", "source_label", "source_ref", "last_checked_at", "monitor_enabled",
                 "collection_status", "last_error", "created_at")


def list_sources(db, *, monitored_only=False):
    query = (f"SELECT {','.join('s.' + name for name in SOURCE_FIELDS)}, "
             "(SELECT COUNT(*) FROM posts p WHERE p.source_id=s.id) AS post_count, "
             "(SELECT COUNT(*) FROM posts p JOIN risk_assessments r ON r.post_id=p.id "
             " WHERE p.source_id=s.id AND r.risk_level IN ('MEDIUM','HIGH')) AS flagged_count "
             "FROM sources s" + (" WHERE s.monitor_enabled=1" if monitored_only else "") +
             " ORDER BY s.monitor_enabled DESC, s.last_checked_at DESC, s.id")
    cursor = db.execute(query)
    return [as_dict(cursor, row) for row in cursor]


def set_monitored(db, source_id, enabled):
    with db:
        row = db.execute("SELECT source_type, source_ref FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise ValueError(f"Không có nguồn {source_id}.")
        if enabled and (row[0] == "dataset" or not str(row[1] or "").startswith("https://")):
            raise ValueError("Chỉ theo dõi được nguồn Facebook có URL (không áp dụng cho dataset).")
        db.execute("UPDATE sources SET monitor_enabled=? WHERE id=?", (int(bool(enabled)), source_id))


def add_source(db, url, kind="auto", label=None, monitor=True):
    """Thêm nguồn theo URL (chuẩn hóa như giao diện); chưa thu thập cho tới lượt kế tiếp."""
    from fb_scraper.storage.source_store import write_source
    from fb_scraper.web.sources import RunRequest, build_plan
    plan = build_plan(RunRequest(sources=url, mode=kind))[0]
    if plan["kind"] == "keyword":
        raise ValueError("Nguồn phải là URL Facebook của cá nhân, Trang hoặc nhóm.")
    source_type = "page" if plan["mode"] == "page" else plan["kind"]
    with db:
        source_id = write_source(db, source_type, label or plan["value"], plan["value"])
        db.execute("UPDATE sources SET monitor_enabled=?, collection_status=coalesce(collection_status,'pending') "
                   "WHERE id=?", (int(bool(monitor)), source_id))
    return source_id


def record_status(db, plan_item, ok, error=None, stats=None):
    """Gọi sau mỗi nguồn trong worker: cập nhật trạng thái nguồn URL hoặc thống kê từ khóa."""
    with db:
        if plan_item["kind"] == "keyword":
            from fb_scraper.analysis.text import keyword_norm
            norm = keyword_norm(plan_item["value"])
            row = db.execute("SELECT stats FROM keywords WHERE keyword_norm=?", (norm,)).fetchone()
            totals = json.loads(row[0] or "{}") if row else {}
            totals["runs"] = totals.get("runs", 0) + 1
            if not ok:
                totals["errors"] = totals.get("errors", 0) + 1
            for key in ("seen", "saved", "existing", "filtered"):
                totals[key] = totals.get(key, 0) + int((stats or {}).get(key, 0))
            totals["last"] = {**(stats or {}), "ok": ok, "at": now()}
            db.execute("UPDATE keywords SET last_used_at=?, stats=? WHERE keyword_norm=?",
                       (now(), json.dumps(totals, ensure_ascii=False), norm))
            return
        db.execute("UPDATE sources SET collection_status=?, last_error=?, last_checked_at=? WHERE source_ref=?",
                   ("ok" if ok else "error", None if ok else str(error)[:500], now(), plan_item["value"]))


def due_plan(db, interval_hours, include_keywords=False):
    """Nguồn theo dõi chưa được thu thập trong interval_hours giờ (+ từ khóa đang bật nếu yêu cầu)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=interval_hours)).isoformat()
    plan = []
    for source in list_sources(db, monitored_only=True):
        if source["last_checked_at"] and source["last_checked_at"] > cutoff and source["collection_status"] == "ok":
            continue
        kind = "group" if source["source_type"] == "group" else "profile"
        plan.append({"kind": kind, "mode": "page" if source["source_type"] == "page" else kind,
                     "value": source["source_ref"]})
    if include_keywords:
        for row in db.execute("SELECT keyword FROM keywords WHERE enabled=1 AND (last_used_at IS NULL OR last_used_at < ?) "
                              "ORDER BY category, id", (cutoff,)):
            plan.append({"kind": "keyword", "mode": "keyword", "value": row[0]})
    return plan[:50]


def keyword_plan(db, category=None):
    from fb_scraper.analysis.store import list_keywords
    return [{"kind": "keyword", "mode": "keyword", "value": row["keyword"]}
            for row in list_keywords(db, enabled_only=True, category=category)][:50]


def run_round(args):
    from fb_scraper.storage import database as storage
    from fb_scraper.web.worker import collect
    db = storage.init_db()
    try:
        plan = due_plan(db, args.interval_hours, args.keywords)
    finally:
        db.close()
    if not plan:
        print("Không có nguồn nào đến hạn thu thập.", flush=True)
        return 0
    print(f"Thu thập {len(plan)} nguồn đến hạn.", flush=True)
    options = {"max_posts": args.max_posts, "max_comments": args.comments, "ocr": args.ocr}
    return asyncio.run(collect({"plan": plan, "options": options}))


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description="Thu thập định kỳ các nguồn được đánh dấu theo dõi.")
    parser.add_argument("--interval-hours", type=float, default=24, help="bỏ qua nguồn đã thu thập trong N giờ")
    parser.add_argument("--keywords", action="store_true", help="thu thập cả các từ khóa đang bật")
    parser.add_argument("--max-posts", type=int, default=20)
    parser.add_argument("--comments", type=int, default=10)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--loop", action="store_true", help="chạy lặp lại sau mỗi interval (giữ terminal mở)")
    args = parser.parse_args(argv)
    if args.interval_hours <= 0:
        parser.error("--interval-hours phải lớn hơn 0.")
    while True:
        code = run_round(args)
        if not args.loop:
            raise SystemExit(code)
        print(f"Chờ {args.interval_hours:g} giờ tới lượt kế tiếp (Ctrl+C để dừng)…", flush=True)
        try:
            time.sleep(args.interval_hours * 3600)
        except KeyboardInterrupt:
            raise SystemExit(0)
