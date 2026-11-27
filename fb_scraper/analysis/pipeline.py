"""Pipeline phân tích: tiền xử lý → chấm điểm → lưu post_analysis/risk_assessments.

Chạy tăng dần: chỉ xử lý bài mới, bài vừa được thu thập lại (last_seen_at mới hơn), bài có OCR/bình luận
mới, hoặc khi bộ rule/cấu hình đổi (ruleset_hash khác). Trạng thái duyệt (review_status) được giữ nguyên.
"""
import hashlib
import json
import re

from fb_scraper.analysis import store
from fb_scraper.analysis.features import extract
from fb_scraper.analysis.rules import ACCENTED_MIN_RATIO, TermSet, build_segments, compile_rule
from fb_scraper.analysis.scoring import assess
from fb_scraper.analysis.text import accent_ratio, fold, lower_form, normalize, sentences, tokens
from fb_scraper.storage.schema import json_text, now

# 2: thêm cờ quảng cáo và độ liên quan với từ khóa tìm kiếm vào features.
PIPELINE_VERSION = 2
COMMENT_WARNING = TermSet(["lừa đảo", "lừa", "scam", "bị lừa", "mất tiền", "không rút được", "cảnh báo",
                           "công an", "tố cáo", "đừng tin", "chiếm đoạt"])
COMMENT_SHILL = TermSet(["đã rút được", "rút được tiền", "đã nhận được tiền", "uy tín thật", "uy tín lắm",
                         "đã nhận lương", "mình làm rồi", "đã nhận hoa hồng", "rút về rồi"])
COMMENT_SOLICIT = TermSet(["check inbox", "check ib", "inbox mình", "ib mình", "kết bạn zalo", "đã inbox"])
MIN_DUPLICATE_LENGTH = 30


def _ocr_texts(db, post_id):
    return [row[0] for row in db.execute(
        "SELECT ocr_text FROM media WHERE post_id=? AND ocr_text IS NOT NULL AND trim(ocr_text) != '' "
        "ORDER BY position, id", (post_id,))]


def comment_context(db, post_id):
    """Bình luận được phân tích riêng làm NGỮ CẢNH cho người duyệt, không cộng vào điểm rủi ro."""
    summary = {"total": 0, "warning_mentions": 0, "shill_like": 0, "solicitation": 0, "examples": []}
    for (content,) in db.execute("SELECT content FROM comments WHERE post_id=? ORDER BY id", (post_id,)):
        if not content:
            continue
        summary["total"] += 1
        lower, folded = lower_form(content), fold(content)
        accented = accent_ratio(lower, folded) >= ACCENTED_MIN_RATIO
        for key, terms in (("warning_mentions", COMMENT_WARNING), ("shill_like", COMMENT_SHILL),
                           ("solicitation", COMMENT_SOLICIT)):
            if terms.search(lower, folded, accented) is not None:
                summary[key] += 1
                if len(summary["examples"]) < 5:
                    summary["examples"].append({"type": key, "text": normalize(content)[:200]})
    return summary


def text_hash(folded):
    """Hash nội dung sau chuẩn hóa: bỏ dấu, ký tự đặc biệt, khoảng trắng → bài đăng lại y hệt có cùng hash."""
    compact = re.sub(r"[^a-z0-9]+", "", folded)
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def search_context(db, post_id, folded):
    """Bài thu từ tìm kiếm từ khóa: tỷ lệ âm tiết của từ khóa có trong bài (Facebook search trả cả bài
    không liên quan). Kèm cờ quảng cáo do collector ghi nhận. Chỉ là thông tin, không cộng điểm."""
    row = db.execute("SELECT json_extract(metadata,'$.source'), json_extract(metadata,'$.is_sponsored') "
                     "FROM posts WHERE id=?", (post_id,)).fetchone()
    source, sponsored = row if row else (None, None)
    result = {"is_sponsored": None if sponsored is None else bool(sponsored)}
    if source and str(source).startswith("kw:"):
        keyword = str(source)[3:]
        words = set(tokens(keyword))
        present = set(tokens(folded, folded=True))
        result.update(search_keyword=keyword,
                      keyword_relevance=round(len(words & present) / len(words), 2) if words else None)
    return result


def preprocess(db, post):
    content = normalize(post["content"])
    ocr = [normalize(text) for text in _ocr_texts(db, post["id"])]
    combined = "\n".join([content] + [f"[OCR] {text}" for text in ocr]).strip()
    folded = fold("\n".join([content] + ocr))
    digest = text_hash(folded)
    duplicate = None
    if len(re.sub(r"[^a-z0-9]", "", folded)) >= MIN_DUPLICATE_LENGTH:
        row = db.execute("SELECT min(post_id) FROM post_analysis WHERE text_hash=? AND post_id<?",
                         (digest, post["id"])).fetchone()
        duplicate = row[0] if row else None
    return {"post_id": post["id"], "normalized_text": content, "ocr_text": "\n".join(ocr),
            "combined_text": combined, "folded_text": folded, "text_hash": digest, "duplicate_of": duplicate,
            "features": {**extract("\n".join([content] + ocr), folded), **search_context(db, post["id"], folded)},
            "comment_context": comment_context(db, post["id"]),
            "sentence_count": len(sentences(content)) + sum(len(sentences(text)) for text in ocr),
            "token_count": len(tokens(folded, folded=True)), "_ocr": ocr}


def save_analysis(db, record, assessment, digest):
    db.execute(
        "INSERT INTO post_analysis(post_id,normalized_text,ocr_text,combined_text,folded_text,text_hash,duplicate_of,"
        "features,comment_context,sentence_count,token_count,pipeline_version,analyzed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET normalized_text=excluded.normalized_text,"
        "ocr_text=excluded.ocr_text, combined_text=excluded.combined_text, folded_text=excluded.folded_text,"
        "text_hash=excluded.text_hash, duplicate_of=excluded.duplicate_of, features=excluded.features,"
        "comment_context=excluded.comment_context, sentence_count=excluded.sentence_count,"
        "token_count=excluded.token_count, pipeline_version=excluded.pipeline_version, analyzed_at=excluded.analyzed_at",
        (record["post_id"], record["normalized_text"], record["ocr_text"], record["combined_text"],
         record["folded_text"], record["text_hash"], record["duplicate_of"], json_text(record["features"]),
         json_text(record["comment_context"]), record["sentence_count"], record["token_count"],
         PIPELINE_VERSION, now()))
    context = {**assessment["context"], "suppressed": assessment["suppressed"]}
    db.execute(
        "INSERT INTO risk_assessments(post_id,category,risk_score,risk_level,matched_rules,evidence,explanation,"
        "context,ruleset_hash,assessed_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET "
        "category=excluded.category, risk_score=excluded.risk_score, risk_level=excluded.risk_level,"
        "matched_rules=excluded.matched_rules, evidence=excluded.evidence, explanation=excluded.explanation,"
        "context=excluded.context, ruleset_hash=excluded.ruleset_hash, assessed_at=excluded.assessed_at",
        (record["post_id"], assessment["category"], assessment["risk_score"], assessment["risk_level"],
         json_text(assessment["matched_rules"]), json_text(assessment["matches"]), assessment["explanation"],
         json_text(context), digest, now()))


def pending_posts(db, digest, force=False):
    """Bài cần (phân tích) lại. So sánh thời điểm theo chuỗi ISO UTC mà storage ghi."""
    if force:
        return db.execute("SELECT id, content FROM posts ORDER BY id").fetchall()
    return db.execute(
        "SELECT p.id, p.content FROM posts p LEFT JOIN post_analysis a ON a.post_id=p.id "
        "LEFT JOIN risk_assessments r ON r.post_id=p.id WHERE a.post_id IS NULL OR r.post_id IS NULL "
        "OR a.pipeline_version != ? OR r.ruleset_hash != ? OR coalesce(p.last_seen_at, p.collected_at) > a.analyzed_at "
        "OR EXISTS (SELECT 1 FROM comments c WHERE c.post_id=p.id AND c.collected_at > a.analyzed_at) "
        "ORDER BY p.id", (PIPELINE_VERSION, digest)).fetchall()


def run_analysis(db, *, force=False, post_ids=None):
    """Phân tích các bài cần cập nhật; trả về thống kê ngắn để in/log."""
    store.ensure_defaults(db)
    rules_raw = store.load_rules(db)
    settings = store.get_settings(db)
    digest = store.ruleset_hash(rules_raw, settings)
    rules = [compile_rule(rule) for rule in rules_raw]
    rows = pending_posts(db, digest, force)
    if post_ids is not None:
        wanted = set(post_ids)
        rows = [row for row in rows if row[0] in wanted]
    levels = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for post_id, content in rows:
        record = preprocess(db, {"id": post_id, "content": content or ""})
        segments = build_segments(record["normalized_text"], record.pop("_ocr"))
        assessment = assess(segments, record["features"], rules, settings)
        with db:
            save_analysis(db, record, assessment, digest)
        levels[assessment["risk_level"]] += 1
    return {"analyzed": len(rows), "levels": levels, "ruleset_hash": digest}


def check_text(db, text):
    """Chấm thử đoạn văn bản dán vào, KHÔNG lưu: cùng rule, trọng số và ngưỡng như bài đã thu thập."""
    store.ensure_defaults(db)
    settings = store.get_settings(db)
    rules = [compile_rule(rule) for rule in store.load_rules(db)]
    record = preprocess(db, {"id": None, "content": text})
    assessment = assess(build_segments(record["normalized_text"], []), record["features"], rules, settings)
    row = db.execute("SELECT min(post_id) FROM post_analysis WHERE text_hash=?", (record["text_hash"],)).fetchone()
    result = {key: assessment[key] for key in ("risk_score", "risk_level", "category", "matches", "explanation", "context")}
    return {**result, "features": record["features"], "thresholds": settings["risk_thresholds"],
            "same_as": row[0] if row else None}


def set_review(db, post_id, status, note=None):
    """Kết quả kiểm tra thủ công; dùng để đánh giá precision/recall và hiệu chỉnh ngưỡng."""
    if status not in {"PENDING", "CONFIRMED_SCAM", "FALSE_POSITIVE", "NEEDS_INFO"}:
        raise ValueError("Trạng thái duyệt không hợp lệ.")
    with db:
        changed = db.execute("UPDATE risk_assessments SET review_status=?, reviewer_note=?, reviewed_at=? "
                             "WHERE post_id=?", (status, note, now(), post_id)).rowcount
    if not changed:
        raise ValueError("Bài chưa được phân tích. Chạy phân tích trước khi duyệt.")


def assessment_view(row):
    record = dict(row)
    for field in ("matched_rules", "evidence", "context", "features", "comment_context"):
        if isinstance(record.get(field), str):
            record[field] = json.loads(record[field])
    return record
