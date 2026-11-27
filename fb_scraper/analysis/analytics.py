"""Thống kê & phân tích trên TẬP DỮ LIỆU ĐÃ THU THẬP.

Mọi con số mô tả mẫu đã thu thập (phụ thuộc từ khóa, nguồn, thời điểm và giới hạn truy cập),
KHÔNG đại diện cho xu hướng của toàn bộ Facebook. Nhãn "gắn cờ" = risk_level MEDIUM hoặc HIGH.
"""
from collections import Counter, defaultdict
import json

from fb_scraper.analysis import similarity
from fb_scraper.analysis.expansion import build_lexicon, candidates
from fb_scraper.analysis.scoring import CATEGORY_LABELS

DISCLAIMER = ("Số liệu chỉ mô tả tập dữ liệu đã thu thập/nhập (phụ thuộc từ khóa, nguồn và thời điểm), "
              "không đại diện cho xu hướng của toàn bộ Facebook. Điểm rủi ro là mức ưu tiên kiểm tra, "
              "không phải kết luận một bài viết là lừa đảo.")
FLAGGED = ("MEDIUM", "HIGH")


def _one(db, query, params=()):
    return db.execute(query, params).fetchone()[0]


def overview(db):
    levels = dict(db.execute("SELECT risk_level, COUNT(*) FROM risk_assessments GROUP BY risk_level").fetchall())
    reviews = dict(db.execute("SELECT review_status, COUNT(*) FROM risk_assessments GROUP BY review_status").fetchall())
    methods = dict(db.execute("SELECT coalesce(json_extract(metadata,'$.collection_method'),'crawler'), COUNT(*) "
                              "FROM posts GROUP BY 1").fetchall())
    return {
        "posts": _one(db, "SELECT COUNT(*) FROM posts"),
        "comments": _one(db, "SELECT COUNT(*) FROM comments"),
        "posts_with_ocr": _one(db, "SELECT COUNT(DISTINCT post_id) FROM media WHERE post_id IS NOT NULL "
                                   "AND ocr_text IS NOT NULL AND trim(ocr_text)!=''"),
        "images": _one(db, "SELECT COUNT(*) FROM media WHERE media_type IN ('image','gif')"),
        "analyzed": _one(db, "SELECT COUNT(*) FROM risk_assessments"),
        "flagged": sum(levels.get(level, 0) for level in FLAGGED),
        "levels": {level: levels.get(level, 0) for level in ("LOW", "MEDIUM", "HIGH")},
        "reviews": reviews, "collection_methods": methods,
        "warning_posts": _one(db, "SELECT COUNT(*) FROM risk_assessments WHERE category='WARNING_OR_AWARENESS'"),
        "exact_duplicates": _one(db, "SELECT COUNT(*) FROM post_analysis WHERE duplicate_of IS NOT NULL"),
        "keywords": _one(db, "SELECT COUNT(*) FROM keywords WHERE enabled=1"),
        "sources": _one(db, "SELECT COUNT(*) FROM sources"),
        "flagged_pending": _one(db, "SELECT COUNT(*) FROM risk_assessments WHERE review_status='PENDING' "
                                    "AND risk_level IN ('MEDIUM','HIGH')"),
        "last_assessed": _one(db, "SELECT max(assessed_at) FROM risk_assessments"),
    }


def _assessments(db):
    cursor = db.execute("SELECT r.post_id, r.category, r.risk_score, r.risk_level, r.evidence, "
                        "substr(coalesce(p.collected_at,''),1,10), substr(coalesce(p.posted_at,''),1,10) "
                        "FROM risk_assessments r JOIN posts p ON p.id=r.post_id")
    for post_id, category, score, level, evidence, collected, posted in cursor:
        yield {"post_id": post_id, "category": category, "score": score, "level": level,
               "matches": json.loads(evidence), "collected_day": collected or None, "posted_day": posted or None}


def signals(db, rules):
    names = {rule["rule_id"]: rule for rule in rules}
    counts, by_category, flagged_counts = Counter(), Counter(), Counter()
    for item in _assessments(db):
        by_category[item["category"]] += 1
        for match in item["matches"]:
            counts[match["rule_id"]] += 1
            if item["level"] in FLAGGED:
                flagged_counts[match["rule_id"]] += 1
    top = [{"rule_id": rule_id, "name": names.get(rule_id, {}).get("name", rule_id),
            "category": names.get(rule_id, {}).get("category", "?"), "posts": count,
            "flagged_posts": flagged_counts[rule_id]} for rule_id, count in counts.most_common()]
    return {"rules": top,
            "job_signals": [item for item in top if item["category"] == "JOB"],
            "investment_signals": [item for item in top if item["category"] == "INVESTMENT"],
            "categories": [{"category": key, "label": CATEGORY_LABELS.get(key, key), "posts": value}
                           for key, value in by_category.most_common()]}


def frequent_phrases(db, limit=25):
    """Cụm từ xuất hiện ở nhiều BÀI ĐÁNG NGỜ (đếm theo số bài, mỗi bài tính một lần)."""
    lexicon = build_lexicon()
    counts, shown = Counter(), {}
    rows = db.execute("SELECT a.combined_text FROM post_analysis a JOIN risk_assessments r ON r.post_id=a.post_id "
                      "WHERE r.risk_level IN ('MEDIUM','HIGH')").fetchall()
    for (text,) in rows:
        for norm, (display, _) in candidates(text, lexicon).items():
            counts[norm] += 1
            shown.setdefault(norm, display)
    return [{"phrase": shown[norm], "posts": count} for norm, count in counts.most_common(limit) if count > 1]


def timeline(db):
    """Số bài theo ngày (ngày thu thập và ngày đăng nếu có), số bài gắn cờ và theo nhóm dấu hiệu."""
    days = defaultdict(lambda: {"collected": 0, "flagged": 0, "posted": 0, "JOB": 0, "INVESTMENT": 0, "OTHER": 0})
    group_trend = defaultdict(Counter)
    for item in _assessments(db):
        if item["collected_day"]:
            days[item["collected_day"]]["collected"] += 1
        day = item["posted_day"] or item["collected_day"]
        if not day:
            continue
        if item["posted_day"]:
            days[day]["posted"] += 1
        if item["level"] in FLAGGED:
            days[day]["flagged"] += 1
            kind = ("JOB" if item["category"].startswith("JOB_SCAM") else
                    "INVESTMENT" if item["category"].startswith(("INVESTMENT", "JOB_TO")) else "OTHER")
            days[day][kind] += 1
            for match in item["matches"]:
                if match["counted"]:
                    group_trend[day][match["group"]] += 1
    ordered = sorted(days)
    return {"days": [{"day": day, **days[day]} for day in ordered],
            "signal_groups": [{"day": day, **group_trend[day]} for day in ordered if group_trend[day]],
            "note": "Bài có ngày đăng được xếp theo ngày đăng; bài thiếu ngày đăng dùng ngày thu thập."}


def keyword_performance(db):
    """Hiệu quả từng từ khóa tìm kiếm: bài thấy/lưu/bị lọc (từ keywords.stats) và chất lượng bài đã lưu.

    yield = số bài gắn cờ / số bài đã lưu. Từ khóa có yield thấp, nhiều bài cảnh báo hoặc độ liên quan thấp
    nên được thay bằng cụm từ "tác nghiệp" (xem Mở rộng từ khóa).
    """
    from fb_scraper.analysis.text import keyword_norm
    posts = defaultdict(lambda: {"collected": 0, "flagged": 0, "warning": 0, "sponsored": 0, "relevance": []})
    rows = db.execute("SELECT json_extract(p.metadata,'$.source'), r.risk_level, r.category, a.features "
                      "FROM posts p LEFT JOIN risk_assessments r ON r.post_id=p.id "
                      "LEFT JOIN post_analysis a ON a.post_id=p.id "
                      "WHERE json_extract(p.metadata,'$.source') LIKE 'kw:%'")
    for source, level, category, features in rows:
        item = posts[keyword_norm(source[3:])]
        data = json.loads(features or "{}")
        item["collected"] += 1
        item["flagged"] += level in FLAGGED
        item["warning"] += category == "WARNING_OR_AWARENESS"
        item["sponsored"] += bool(data.get("is_sponsored"))
        if data.get("keyword_relevance") is not None:
            item["relevance"].append(data["keyword_relevance"])
    result = []
    for identity, keyword, category, stats in db.execute("SELECT id, keyword, category, stats FROM keywords"):
        item = posts.pop(keyword_norm(keyword), None) or {"collected": 0, "flagged": 0, "warning": 0,
                                                          "sponsored": 0, "relevance": []}
        totals = json.loads(stats or "{}")
        relevance = item.pop("relevance")
        result.append({"id": identity, "keyword": keyword, "category": category, **item,
                       "runs": totals.get("runs", 0), "seen": totals.get("seen", 0),
                       "filtered": totals.get("filtered", 0),
                       "yield": round(item["flagged"] / item["collected"], 3) if item["collected"] else None,
                       "mean_relevance": round(sum(relevance) / len(relevance), 2) if relevance else None})
    return sorted(result, key=lambda entry: (-(entry["yield"] or -1), -entry["collected"]))


def reused_artifacts(db, min_posts=2):
    """Liên kết, tên miền, số điện thoại, kênh liên hệ xuất hiện ở ≥ min_posts bài khác nhau."""
    owners = defaultdict(lambda: defaultdict(set))
    for post_id, features in db.execute("SELECT post_id, features FROM post_analysis"):
        data = json.loads(features)
        for field in ("urls", "domains", "phones"):
            for value in data.get(field, []):
                owners[field][value].add(post_id)
    return {field: sorted(({"value": value, "posts": len(ids), "post_ids": sorted(ids)[:20]}
                           for value, ids in values.items() if len(ids) >= min_posts),
                          key=lambda item: -item["posts"])[:30]
            for field, values in owners.items()}


def similar_content(db, threshold, flagged_only=False):
    query = "SELECT a.post_id, a.folded_text, a.features FROM post_analysis a"
    if flagged_only:
        query += " JOIN risk_assessments r ON r.post_id=a.post_id WHERE r.risk_level IN ('MEDIUM','HIGH')"
    documents, features = {}, {}
    for post_id, folded, data in db.execute(query):
        documents[post_id] = folded
        features[post_id] = json.loads(data)
    result = similarity.analyze(documents, features, threshold)
    previews = dict(db.execute("SELECT id, substr(coalesce(content,''),1,160) FROM posts").fetchall())
    levels = dict(db.execute("SELECT post_id, risk_level FROM risk_assessments").fetchall())
    for cluster in result["clusters"]:
        cluster["posts"] = [{"post_id": post_id, "preview": previews.get(post_id, ""),
                             "risk_level": levels.get(post_id)} for post_id in cluster["post_ids"]]
        cluster["flagged"] = sum(1 for post_id in cluster["post_ids"] if levels.get(post_id) in FLAGGED)
    result["pairs"] = result["pairs"][:100]
    result["note"] = ("Cụm chỉ thể hiện nội dung giống nhau; không đủ để kết luận các bài thuộc cùng "
                      "một người/tổ chức nếu không có bằng chứng độc lập.")
    return result


def report(db, rules, settings):
    return {"disclaimer": DISCLAIMER, "overview": overview(db), "signals": signals(db, rules),
            "phrases": frequent_phrases(db), "timeline": timeline(db), "reused": reused_artifacts(db),
            "similarity": similar_content(db, settings["similarity_threshold"])}
