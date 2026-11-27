"""API cho trang Phân tích (/analysis). GET dùng kết nối chỉ đọc; POST mở kết nối ghi ngắn."""
import base64
import binascii
from contextlib import contextmanager
import csv
import io
import json
from pathlib import Path
import re
import sqlite3

from fb_scraper.analysis import actors, analytics, evaluation, expansion, importer, monitor, pipeline, store
from fb_scraper.analysis.scoring import CATEGORY_LABELS
from fb_scraper.storage import database as storage
from fb_scraper.web import data

POST_COLUMNS = ("r.post_id, p.post_key, p.url, p.author, s.source_label, s.source_type, p.posted_at, p.collected_at, "
                "r.category, r.risk_score, r.risk_level, r.matched_rules, r.review_status, r.explanation, "
                "substr(coalesce(p.content,''),1,280) AS preview")
MAX_UPLOAD = 20 * 1024 * 1024


@contextmanager
def writable():
    db = sqlite3.connect(storage.DB_PATH, timeout=30)
    try:
        storage.configure(db)
        store.ensure_defaults(db)
        yield db
    finally:
        db.close()


def _rows(db, query, params=()):
    cursor = db.execute(query, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def _first(params, key, default=""):
    return params.get(key, [default])[0]


SORTS = {"score": "r.risk_score DESC, p.id DESC", "recent": "coalesce(p.posted_at, p.collected_at) DESC, p.id DESC",
         "oldest": "coalesce(p.posted_at, p.collected_at) ASC, p.id ASC"}


def _filters(params):
    """Mệnh đề WHERE dùng chung cho danh sách bài và xuất CSV theo bộ lọc hiện tại."""
    clauses, values = [], []
    for key, column in (("level", "r.risk_level"), ("category", "r.category"), ("review", "r.review_status")):
        value = _first(params, key)
        if value == "FLAGGED" and key == "level":
            clauses.append("r.risk_level IN ('MEDIUM','HIGH')")
        elif value:
            clauses.append(f"{column}=?")
            values.append(value)
    ids = [int(item) for item in _first(params, "ids").split(",")[:1000] if item.strip().isdigit()]
    if _first(params, "ids"):
        clauses.append(f"r.post_id IN ({','.join('?' * len(ids)) or 'NULL'})")
        values.extend(ids)
    query = _first(params, "q")[:200].casefold()
    if query:
        clauses.append("instr(casefold(coalesce(p.content,'') || ' ' || coalesce(p.author,'') || ' ' || r.matched_rules), ?) > 0")
        values.append(query)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return "FROM risk_assessments r JOIN posts p ON p.id=r.post_id JOIN sources s ON s.id=p.source_id" + where, values


def list_posts(db, params):
    base, values = _filters(params)
    offset = max(0, int(_first(params, "offset", "0")))
    limit = max(1, min(int(_first(params, "limit", "20")), 100))
    order = SORTS.get(_first(params, "sort", "score"), SORTS["score"])
    total = db.execute("SELECT COUNT(*) " + base, values).fetchone()[0]
    items = _rows(db, f"SELECT {POST_COLUMNS} {base} ORDER BY {order} LIMIT ? OFFSET ?", (*values, limit, offset))
    for item in items:
        item["matched_rules"] = json.loads(item["matched_rules"])
        item["category_label"] = CATEGORY_LABELS.get(item["category"], item["category"])
    return {"items": items, "total": total}


def post_detail(db, post_id):
    rows = _rows(db, f"SELECT {POST_COLUMNS}, p.content, r.evidence, r.context, r.reviewer_note, r.assessed_at, "
                     "a.features, a.comment_context, a.ocr_text, a.duplicate_of, a.sentence_count, a.token_count "
                     "FROM risk_assessments r JOIN posts p ON p.id=r.post_id JOIN sources s ON s.id=p.source_id "
                     "JOIN post_analysis a ON a.post_id=r.post_id WHERE r.post_id=?", (post_id,))
    if not rows:
        return None
    record = pipeline.assessment_view(rows[0])
    record["category_label"] = CATEGORY_LABELS.get(record["category"], record["category"])
    return record


def export_csv(db, params=None):
    base, values = _filters(params or {})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["post_id", "post_key", "url", "author", "posted_at", "category", "risk_score", "risk_level",
                     "matched_rules", "review_status", "reviewer_note", "explanation"])
    for row in db.execute("SELECT r.post_id, p.post_key, p.url, p.author, p.posted_at, r.category, r.risk_score, "
                          f"r.risk_level, r.matched_rules, r.review_status, r.reviewer_note, r.explanation {base} "
                          "ORDER BY r.risk_score DESC", values):
        writer.writerow([*row[:8], ",".join(json.loads(row[8])), *row[9:]])
    return ("﻿" + output.getvalue()).encode("utf-8")


def handle_get(path, params):
    """Trả về (payload, status) hoặc (bytes, content_type) cho /api/analysis/export."""
    with data.database() as db:
        if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='risk_assessments'").fetchone():
            return {"error": "Chưa có dữ liệu phân tích. Hãy bấm Phân tích lại."}, 404
        db.create_function("casefold", 1, lambda value: (value or "").casefold())
        settings = store.get_settings(db)
        rules = store.load_rules(db, enabled_only=False)
        if path == "/api/analysis/overview":
            return {"disclaimer": analytics.DISCLAIMER, "overview": analytics.overview(db),
                    "signals": analytics.signals(db, rules), "phrases": analytics.frequent_phrases(db),
                    "timeline": analytics.timeline(db), "reused": analytics.reused_artifacts(db),
                    "settings": settings, "categories": CATEGORY_LABELS}, 200
        if path == "/api/analysis/posts":
            return list_posts(db, params), 200
        if path.startswith("/api/analysis/posts/"):
            identity = path.rsplit("/", 1)[-1]
            record = post_detail(db, int(identity)) if identity.isdigit() else None
            return (record, 200) if record else ({"error": "Không tìm thấy bài đã phân tích."}, 404)
        if path == "/api/analysis/similar":
            threshold = float(_first(params, "threshold", str(settings["similarity_threshold"])))
            if not 0 < threshold <= 1:
                raise ValueError("Ngưỡng tương đồng phải trong (0, 1].")
            return analytics.similar_content(db, threshold, _first(params, "flagged") == "1"), 200
        if path == "/api/analysis/rules":
            return {"rules": rules, "settings": settings}, 200
        if path == "/api/analysis/keywords":
            return {"keywords": store.list_keywords(db), "suggestions": store.list_suggestions(db),
                    "performance": analytics.keyword_performance(db)}, 200
        if path == "/api/analysis/sources":
            return {"sources": monitor.list_sources(db)}, 200
        if path == "/api/analysis/evaluate":
            return evaluation.evaluate(db, settings), 200
        if path == "/api/analysis/imports":
            return {"batches": _rows(db, "SELECT * FROM import_batches ORDER BY id DESC LIMIT 20")}, 200
        if path == "/api/analysis/actors":
            return actors.stats(db, min_posts=max(1, int(_first(params, "min_posts", "1")))), 200
        if path == "/api/analysis/actors/export":
            return actors.export_csv(db, _first(params, "kind", "authors"),
                                     max(1, int(_first(params, "min_posts", "1")))), "text/csv"
        if path == "/api/analysis/export":
            return export_csv(db, params), "text/csv"
    return {"error": "Không tìm thấy đường dẫn."}, 404


def _save_upload(payload):
    name = Path(str(payload.get("file_name", "dataset"))).name
    name = re.sub(r"[^\w.\-]+", "_", name)[:100] or "dataset"
    try:
        content = base64.b64decode(payload.get("data_base64", ""), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Tệp tải lên không hợp lệ.") from None
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("Tệp rỗng hoặc lớn hơn 20 MB.")
    folder = storage.DATA / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_bytes(content)  # giữ bản gốc của dataset để truy vết nguồn dữ liệu
    return target


def handle_post(path, payload, manager):
    if path == "/api/analysis/collect":
        with writable() as db:
            plan = (monitor.keyword_plan(db, payload.get("category") or None) if payload.get("target") == "keywords"
                    else monitor.due_plan(db, float(payload.get("interval_hours", 0.0001))))
        if not plan:
            raise ValueError("Không có từ khóa đang bật hoặc nguồn theo dõi nào để thu thập.")
        options = {"max_posts": max(1, min(int(payload.get("max_posts", 20)), 500)),
                   "max_comments": max(0, min(int(payload.get("max_comments", 10)), 5000)),
                   "ocr": bool(payload.get("ocr", False)),
                   "recent_first": bool(payload.get("recent_first", True)),
                   "scam_filter": bool(payload.get("scam_filter", False))}
        return {"job_id": manager.start({"plan": plan, "options": options}), "sources": len(plan)}, 202
    with writable() as db:
        if path == "/api/analysis/run":
            return pipeline.run_analysis(db, force=bool(payload.get("force"))), 200
        if path == "/api/analysis/check":
            text = (payload.get("text") or "").strip()
            if not text:
                raise ValueError("Dán nội dung bài viết cần kiểm tra.")
            result = pipeline.check_text(db, text[:20000])
            return {**result, "category_label": CATEGORY_LABELS.get(result["category"], result["category"])}, 200
        if path == "/api/analysis/review":
            pipeline.set_review(db, int(payload["post_id"]), payload["status"], (payload.get("note") or "")[:1000] or None)
            return {"ok": True}, 200
        if path == "/api/analysis/rules":
            store.update_rule(db, str(payload["rule_id"]), enabled=payload.get("enabled"),
                              weight=payload.get("weight"), priority=payload.get("priority"))
            return {"ok": True, "analysis": pipeline.run_analysis(db)}, 200
        if path == "/api/analysis/settings":
            value = store.set_setting(db, str(payload["key"]), payload["value"])
            return {"value": value, "analysis": pipeline.run_analysis(db)}, 200
        if path == "/api/analysis/keywords":
            action = payload.get("action")
            if action == "add":
                identity, created = store.add_keyword(db, payload.get("keyword", ""), payload.get("category", "OTHER"))
                return {"id": identity, "created": created}, 200
            if action == "remove":
                return {"removed": store.remove_keyword(db, payload["id"])}, 200
            if action == "toggle":
                return {"updated": store.set_keyword_enabled(db, int(payload["id"]), payload.get("enabled"))}, 200
            raise ValueError("Thao tác từ khóa không hợp lệ.")
        if path == "/api/analysis/expand":
            pipeline.run_analysis(db)
            text = (payload.get("text") or "").strip()[:5000] or None
            return {"suggestions": expansion.suggest(db, min_level=payload.get("level", "MEDIUM"), text=text)}, 200
        if path == "/api/analysis/suggestions":
            keyword_id = store.decide_suggestion(db, int(payload["id"]), bool(payload.get("accept")),
                                                 payload.get("category") or None)
            return {"keyword_id": keyword_id}, 200
        if path == "/api/analysis/sources":
            if payload.get("action") == "add":
                return {"id": monitor.add_source(db, payload.get("url", ""), payload.get("kind", "auto"))}, 200
            monitor.set_monitored(db, int(payload["id"]), bool(payload.get("enabled")))
            return {"ok": True}, 200
        if path == "/api/analysis/import":
            target = _save_upload(payload)
            result = importer.import_file(db, target, file_format=payload.get("format") or None,
                                          table=payload.get("table") or None, dataset=payload.get("dataset") or None)
            result["analysis"] = pipeline.run_analysis(db)
            storage.export_jsonl(db)
            return result, 200
    return {"error": "Không tìm thấy thao tác."}, 404
