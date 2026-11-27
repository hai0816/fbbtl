"""Thống kê tác giả và nơi đăng (nhóm/trang/hồ sơ) theo kết quả chấm điểm rủi ro.

Một tác giả/nơi đăng có nhiều bài bị gắn cờ chỉ là tín hiệu để ưu tiên kiểm tra, không phải kết luận
rằng tài khoản đó lừa đảo. Số bài "đã xác nhận" lấy từ kết quả duyệt thủ công.
"""
from collections import Counter
import csv
import io
import json
from urllib.parse import parse_qs, urlsplit

FB = "https://www.facebook.com/"
# Đoạn đầu đường dẫn không phải tên người dùng/trang.
RESERVED = {"groups", "people", "profile.php", "permalink.php", "story.php", "photo", "photo.php", "photos",
            "watch", "share", "hashtag", "events", "pages", "search", "reel", "marketplace", "login"}
ANONYMOUS = {"người tham gia ẩn danh", "anonymous participant", "anonymous member"}
FLAGGED = {"MEDIUM", "HIGH"}
TYPE_LABELS = {"group": "Nhóm", "page": "Trang", "profile": "Hồ sơ/Trang", "dataset": "Dataset"}


def author_key(url, name=""):
    """Định danh ổn định của tác giả. Bỏ tham số theo dõi (__cft__, __tn__) vốn đổi theo mỗi lần tải trang,
    và quy link thành viên nhóm (/groups/X/user/Y/) về hồ sơ Y, để một người không bị tính thành nhiều."""
    name = (name or "").strip()
    if name.casefold() in ANONYMOUS:
        return None
    parts = urlsplit(url or "")
    if parts.netloc.endswith("facebook.com"):
        segments = [segment for segment in parts.path.split("/") if segment]
        query = parse_qs(parts.query)
        if segments[:1] in (["profile.php"], ["permalink.php"]) and query.get("id"):
            return f"{FB}profile.php?id={query['id'][0]}"
        if len(segments) >= 4 and segments[0] == "groups" and segments[2] == "user":
            return f"{FB}profile.php?id={segments[3]}"
        if len(segments) >= 3 and segments[0] == "people":
            return f"{FB}profile.php?id={segments[2]}"
        if segments and segments[0] not in RESERVED:
            return FB + segments[0]
    return f"name:{name.casefold()}" if name else None


def _entry():
    return {"names": Counter(), "post_ids": [], "posts": 0, "flagged": 0, "high": 0, "confirmed": 0,
            "false_positive": 0, "score_sum": 0, "max_score": 0, "keywords": Counter(), "phones": Counter(),
            "authors": set()}


def _add(entry, row, name, author):
    entry["names"][name] += 1
    entry["post_ids"].append(row["post_id"])
    entry["posts"] += 1
    entry["flagged"] += row["risk_level"] in FLAGGED
    entry["high"] += row["risk_level"] == "HIGH"
    entry["confirmed"] += row["review_status"] == "CONFIRMED_SCAM"
    entry["false_positive"] += row["review_status"] == "FALSE_POSITIVE"
    entry["score_sum"] += row["risk_score"]
    entry["max_score"] = max(entry["max_score"], row["risk_score"])
    if str(row["origin"] or "").startswith("kw:"):
        entry["keywords"][row["origin"][3:]] += 1
    for phone in json.loads(row["features"] or "{}").get("phones", []):
        entry["phones"][phone] += 1
    if author:
        entry["authors"].add(author)


def _finish(key, entry, kind, url):
    posts = entry["posts"]
    return {"key": key, "label": entry["names"].most_common(1)[0][0] if entry["names"] else key,
            "type": kind, "type_label": TYPE_LABELS.get(kind, "Tác giả"), "url": url,
            "posts": posts, "flagged": entry["flagged"], "high": entry["high"], "confirmed": entry["confirmed"],
            "false_positive": entry["false_positive"], "flag_rate": round(entry["flagged"] / posts, 3),
            "avg_score": round(entry["score_sum"] / posts, 1), "max_score": entry["max_score"],
            "keywords": [word for word, _ in entry["keywords"].most_common(3)],
            "phones": [phone for phone, _ in entry["phones"].most_common(3)],
            "authors": len(entry["authors"]), "post_ids": entry["post_ids"]}


def _rank(rows, min_posts, limit):
    rows = [row for row in rows if row["posts"] >= min_posts]
    rows.sort(key=lambda row: (-row["confirmed"], -row["flagged"], -row["high"], -row["max_score"], -row["posts"]))
    return rows[:limit]


def stats(db, *, min_posts=1, limit=200):
    """Trả về {"authors", "places", "summary"}; mỗi dòng có post_ids để mở danh sách bài tương ứng."""
    cursor = db.execute(
        "SELECT r.post_id, p.author, p.author_url, s.id AS source_id, s.source_type, s.source_label, s.source_ref, "
        "json_extract(p.metadata,'$.source') AS origin, r.risk_score, r.risk_level, r.review_status, a.features "
        "FROM risk_assessments r JOIN posts p ON p.id=r.post_id JOIN sources s ON s.id=p.source_id "
        "LEFT JOIN post_analysis a ON a.post_id=p.id ORDER BY r.post_id")
    columns = [column[0] for column in cursor.description]
    authors, places, place_meta = {}, {}, {}
    for values in cursor:
        row = dict(zip(columns, values))
        name = (row["author"] or "").strip()
        author = author_key(row["author_url"], name)
        if author:
            _add(authors.setdefault(author, _entry()), row, name, None)
        place = row["source_ref"] or f"source:{row['source_id']}"
        kind = row["source_type"]
        if kind == "group":
            place_name = "Nhóm " + place.rstrip("/").rsplit("/", 1)[-1]
        elif author == place and name:
            place_name = name  # bài đăng trên chính trang/hồ sơ của tác giả
        else:
            place_name = row["source_label"]
        place_meta[place] = (kind, row["source_ref"])
        _add(places.setdefault(place, _entry()), row, place_name, author)
    author_rows = [_finish(key, entry, "author", None if key.startswith("name:") else key)
                   for key, entry in authors.items()]
    place_rows = [_finish(key, entry, place_meta[key][0], place_meta[key][1]) for key, entry in places.items()]
    summary = {"authors": len(author_rows), "places": len(place_rows),
               "repeat_authors": sum(1 for row in author_rows if row["flagged"] >= 2),
               "repeat_places": sum(1 for row in place_rows if row["flagged"] >= 2)}
    return {"authors": _rank(author_rows, min_posts, limit), "places": _rank(place_rows, min_posts, limit),
            "summary": summary}


def export_csv(db, kind, min_posts=1):
    """CSV cho báo cáo: kind = 'authors' hoặc 'places'."""
    rows = stats(db, min_posts=min_posts, limit=100000)["places" if kind == "places" else "authors"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["label", "type", "url", "posts", "flagged", "high", "confirmed_scam", "false_positive",
                     "flag_rate", "avg_score", "max_score", "distinct_authors", "keywords", "phones", "post_ids"])
    for row in rows:
        writer.writerow([row["label"], row["type_label"], row["url"] or "", row["posts"], row["flagged"], row["high"],
                         row["confirmed"], row["false_positive"], row["flag_rate"], row["avg_score"],
                         row["max_score"], row["authors"], "; ".join(row["keywords"]), "; ".join(row["phones"]),
                         " ".join(map(str, row["post_ids"]))])
    return ("﻿" + output.getvalue()).encode("utf-8")
