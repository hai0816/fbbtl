"""Nhập dữ liệu từ CSV, JSON/JSONL hoặc SQLite — phương án dự phòng khi không thu thập trực tiếp.

- Ánh xạ tên cột linh hoạt (FIELD_ALIASES), thiếu post_id thì tạo ID ổn định từ nội dung.
- Trùng lặp: dòng trùng trong cùng tệp bị bỏ qua; post_id đã có trong database được CẬP NHẬT qua
  storage.save_post (không xóa dữ liệu cũ); nội dung y hệt bài khác được đánh dấu duplicate_of ở bước phân tích.
- Thời gian: epoch giây/mili-giây, ISO 8601, dd/mm/yyyy [hh:mm[:ss]], yyyy-mm-dd... Giờ không có múi giờ được
  hiểu là giờ Việt Nam (UTC+7) rồi lưu dạng ISO UTC như dữ liệu crawler.
- Mỗi bài ghi nhận nguồn (sources.source_type='dataset') và metadata collection_method='import'.
"""
import csv
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from fb_scraper.analysis.text import normalize
from fb_scraper.common.numbers import parse_count
from fb_scraper.storage import database as storage
from fb_scraper.storage.schema import json_text, now
from fb_scraper.storage.source_store import write_source

VN_TZ = timezone(timedelta(hours=7))
FIELD_ALIASES = {
    "post_id": ("post_id", "post_key", "id", "postid", "post id", "ma_bai", "fb_id"),
    "content": ("content", "text", "message", "post_text", "noi_dung", "nội dung", "body", "caption"),
    "url": ("url", "link", "permalink", "post_url", "permalink_url"),
    "author": ("author", "user", "username", "page_name", "page", "tac_gia", "tác giả", "from", "name"),
    "author_url": ("author_url", "profile_url", "user_url"),
    "posted_at": ("posted_at", "created_time", "created_at", "time", "date", "timestamp", "ngay_dang", "ngày đăng"),
    "likes": ("likes", "reactions", "reaction_count", "like_count"),
    "comments": ("comments_count", "comment_count", "num_comments"),
    "shares": ("shares", "share_count", "num_shares"),
    "ocr_text": ("ocr_text", "ocr", "image_text"),
    "images": ("images", "image_urls", "image_url"),
    "label": ("label", "is_scam", "nhan", "nhãn", "ground_truth"),
    "top_comments": ("top_comments", "comments", "comment_list"),
}
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%d/%m/%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%m/%d/%Y %I:%M %p", "%Y/%m/%d %H:%M", "%Y/%m/%d")
POSITIVE_LABELS = {"1", "true", "yes", "scam", "lua dao", "lừa đảo", "fraud", "positive", "co"}
NEGATIVE_LABELS = {"0", "false", "no", "legit", "binh thuong", "bình thường", "negative", "khong", "không"}


def normalize_time(value):
    """Trả về (ISO UTC hoặc None, lỗi hoặc None)."""
    if value in (None, ""):
        return None, None
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            number = float(value)
            number = number / 1000 if number > 1e11 else number
            return datetime.fromtimestamp(number, timezone.utc).isoformat(), None
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
            for fmt in DATE_FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None, f"không đọc được thời gian '{value}'"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VN_TZ)
        return parsed.astimezone(timezone.utc).isoformat(), None
    except (ValueError, OverflowError, OSError):
        return None, f"thời gian không hợp lệ '{value}'"


def parse_label(value):
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in POSITIVE_LABELS:
        return 1
    if text in NEGATIVE_LABELS:
        return 0
    return None


def _pick(row, field):
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in FIELD_ALIASES[field]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return lowered[alias]
    return None


def _count(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.replace(".", "", 1).isdigit() and text.endswith((".0", ".00")):
        return int(float(text))  # số kiểu "12.0" do Excel/pandas xuất
    return parse_count(text)


def _list(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return [item.strip() for item in text.replace("\n", "|").split("|") if item.strip()]


def read_rows(path, file_format=None, table=None, data=None):
    """Đọc tệp thành danh sách dict. data: nội dung bytes (khi tải lên qua giao diện)."""
    file_format = (file_format or Path(path).suffix.lstrip(".")).lower()
    if file_format in {"db", "sqlite", "sqlite3"}:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as source:
            source.row_factory = sqlite3.Row
            names = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            table = table or ("posts" if "posts" in names else None)
            if table not in names:
                raise ValueError(f"Không tìm thấy bảng '{table}'. Có các bảng: {', '.join(sorted(names))}")
            return [dict(row) for row in source.execute(f'SELECT * FROM "{table}"')], "sqlite"
    raw = data if data is not None else Path(path).read_bytes()
    text = raw.decode("utf-8-sig")
    if file_format == "csv":
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
        return list(csv.DictReader(io.StringIO(text), dialect=dialect)), "csv"
    if file_format == "jsonl" or (file_format == "json" and not text.lstrip().startswith(("[", "{"))):
        return [json.loads(line) for line in text.splitlines() if line.strip()], "jsonl"
    if file_format == "json":
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("posts") or payload.get("data") or payload.get("items") or [payload]
        return list(payload), "json"
    raise ValueError("Định dạng hỗ trợ: csv, json, jsonl, db/sqlite.")


def to_post(row, dataset, source_id):
    """Chuẩn hóa một dòng; trả về (post dict, nhãn, cảnh báo) hoặc ném ValueError nếu không dùng được."""
    content = normalize(_pick(row, "content") or "")
    ocr_text = normalize(_pick(row, "ocr_text") or "")
    if not content and not ocr_text:
        raise ValueError("thiếu nội dung (content/text/message) và OCR")
    warnings = []
    posted_at, error = normalize_time(_pick(row, "posted_at"))
    if error:
        warnings.append(error)
    post_id = _pick(row, "post_id")
    if post_id in (None, ""):
        seed = "|".join([dataset, content, str(_pick(row, "author") or ""), str(_pick(row, "posted_at") or "")])
        post_id = "import:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    post = {"post_id": str(post_id), "content": content, "url": str(_pick(row, "url") or ""),
            "author": str(_pick(row, "author") or ""), "author_url": str(_pick(row, "author_url") or ""),
            "posted_at": posted_at, "posted_raw": str(_pick(row, "posted_at") or "") or None,
            "likes": _count(_pick(row, "likes")), "comments": _count(_pick(row, "comments")),
            "shares": _count(_pick(row, "shares")), "source_id": source_id, "source": f"dataset:{dataset}",
            "images": [str(item) for item in _list(_pick(row, "images")) if str(item).startswith("http")]}
    if ocr_text:
        post["ocr"] = [{"text": ocr_text}]
    comments = [item for item in _list(_pick(row, "top_comments")) if item]
    if comments:
        post["top_comments"] = [item if isinstance(item, dict) else {"text": str(item)} for item in comments]
    return post, parse_label(_pick(row, "label")), warnings


def import_file(db, path, *, file_format=None, table=None, dataset=None, data=None):
    """Nhập một tệp vào SQLite; trả về thống kê và lưu nhật ký vào import_batches."""
    rows, detected = read_rows(path, file_format, table, data)
    dataset = dataset or Path(path).stem
    with db:
        source_id = write_source(db, "dataset", f"Dataset: {dataset}", f"dataset:{dataset}",
                                 {"collection_method": "import", "file_name": Path(path).name})
    known = storage.known_ids(db)
    seen, errors = set(), []
    stats = {"total_rows": len(rows), "inserted": 0, "updated": 0, "duplicates": 0}
    for number, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValueError("dòng không phải object/bản ghi")
            post, label, warnings = to_post(row, dataset, source_id)
            if post["post_id"] in seen:
                stats["duplicates"] += 1
                continue
            seen.add(post["post_id"])
            existed = post["post_id"] in known
            storage.save_post(db, post)
            with db:
                db.execute("UPDATE posts SET metadata=json_set(metadata,'$.collection_method','import',"
                           "'$.dataset',?,'$.label',?,'$.imported_at',?) WHERE post_key=?",
                           (dataset, label, now(), post["post_id"]))
            stats["updated" if existed else "inserted"] += 1
            known.add(post["post_id"])
            errors.extend({"row": number, "warning": warning} for warning in warnings)
        except (ValueError, TypeError) as error:
            errors.append({"row": number, "error": str(error)[:300]})
    with db:
        db.execute("INSERT INTO import_batches(source_id,file_name,file_format,total_rows,inserted,updated,duplicates,"
                   "errors,imported_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (source_id, Path(path).name, detected, stats["total_rows"], stats["inserted"], stats["updated"],
                    stats["duplicates"], json_text(errors[:500]), now()))
        db.execute("UPDATE sources SET last_checked_at=?, collection_status='ok' WHERE id=?", (now(), source_id))
    return {**stats, "format": detected, "dataset": dataset, "source_id": source_id,
            "errors": [item for item in errors if "error" in item][:50],
            "warnings": [item for item in errors if "warning" in item][:50]}
