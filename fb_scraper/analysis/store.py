"""Truy cập bảng v5: cấu hình, rule, từ khóa, đề xuất mở rộng. Mọi hàm ghi đều chạy trong `with db`."""
import hashlib
import json

from fb_scraper.analysis.default_rules import DEFAULT_SETTINGS, RULES
from fb_scraper.analysis.rules import ENGINE_VERSION, compile_rule
from fb_scraper.analysis.text import keyword_norm
from fb_scraper.storage.schema import as_dict, json_text, now

DEFAULT_KEYWORDS = {
    "JOB": ["Việc làm online", "Việc nhẹ lương cao", "Tuyển cộng tác viên", "Làm nhiệm vụ nhận hoa hồng",
            "Nạp tiền để nhận việc", "Đóng phí tuyển dụng", "Đặt cọc công việc"],
    "INVESTMENT": ["Đầu tư sinh lời", "Cam kết lợi nhuận", "Đầu tư không rủi ro", "Lợi nhuận mỗi ngày",
                   "Nạp tiền đầu tư", "Nạp tiền để rút tiền", "Đầu tư tài chính online"],
}
# Từ khóa trong đề là TÊN GỌI của kiểu lừa đảo, hay xuất hiện trong báo chí/bài cảnh báo. Bộ dưới đây mô phỏng
# ngôn ngữ "tác nghiệp" của bài mời gọi (viết tắt, kêu gọi inbox). Đây là điểm xuất phát; giữ/bỏ dựa trên
# thống kê hiệu quả từng từ khóa (tỷ lệ bài gắn cờ) trong tab Từ khóa & nguồn.
OPERATIONAL_KEYWORDS = {
    "JOB": ["tuyển ctv online nhận hoa hồng", "tuyển ctv chốt đơn tại nhà", "làm nhiệm vụ nhận hoa hồng ib",
            "tuyển ctv thả tim tiktok", "việc làm online chỉ cần điện thoại", "tuyển ctv đánh giá sản phẩm",
            "kiếm thêm thu nhập cho sinh viên inbox", "nhận hoa hồng 20% mỗi đơn", "tuyển ctv like video nhận tiền",
            "làm nhiệm vụ shopee nhận hoa hồng", "ứng tiền đơn hàng nhận hoa hồng", "việc làm tại nhà 500k/ngày",
            "tuyển nhân viên trực page tại nhà", "tuyển ctv xem quảng cáo nhận tiền"],
    "INVESTMENT": ["kèm 1-1 lợi nhuận mỗi ngày", "nhóm tín hiệu vào lệnh", "x2 tài khoản",
                   "lãi 3% mỗi ngày", "đầu tư usdt lợi nhuận cố định", "lợi nhuận cố định hàng ngày",
                   "nhóm kín tín hiệu usdt", "đầu tư forex cam kết lãi", "chuyên gia kèm lệnh crypto",
                   "rút tiền cần đóng phí xác minh"],
    "OTHER": ["hỗ trợ lấy lại tiền bị lừa", "hacker lấy lại tiền lừa đảo"],
}
CATEGORIES = {"JOB", "INVESTMENT", "OTHER"}
RULE_COLUMNS = ("rule_id", "name", "category", "description", "weight", "priority", "dedup_group")
RULE_FIELDS = {"rule_id", "name", "category", "description", "weight", "priority", "group", "conditions",
               "window", "exclude", "negatable", "ignore_warning_context"}


def _rows(db, query, params=()):
    cursor = db.execute(query, params)
    return [as_dict(cursor, row) for row in cursor]


def ensure_defaults(db):
    """Nạp rule/từ khóa/cấu hình mặc định. Không ghi đè bật/tắt, trọng số người dùng đã chỉnh."""
    with db:
        timestamp = now()
        for rule in RULES:
            write_rule(db, rule, timestamp=timestamp, from_code=True)
        for category in CATEGORIES:
            for keyword in DEFAULT_KEYWORDS.get(category, []) + OPERATIONAL_KEYWORDS.get(category, []):
                db.execute("INSERT OR IGNORE INTO keywords(keyword,keyword_norm,category,origin,enabled,created_at) "
                           "VALUES(?,?,?,'default',1,?)", (keyword, keyword_norm(keyword), category, timestamp))
        for key, value in DEFAULT_SETTINGS.items():
            db.execute("INSERT OR IGNORE INTO analysis_settings(key,value,updated_at) VALUES(?,?,?)",
                       (key, json_text(value), timestamp))


def write_rule(db, rule, *, timestamp=None, from_code=False):
    """Thêm/cập nhật một rule. Rule từ code không đổi weight/priority mà người dùng đã tùy chỉnh."""
    unknown = set(rule) - RULE_FIELDS
    if unknown:
        raise ValueError(f"Trường không hỗ trợ trong rule: {', '.join(sorted(unknown))}")
    if rule["category"] not in {"JOB", "INVESTMENT", "COMMON", "COMBO"}:
        raise ValueError("category phải là JOB, INVESTMENT, COMMON hoặc COMBO.")
    if not 0 <= int(rule["weight"]) <= 100:
        raise ValueError("Trọng số phải trong khoảng 0–100.")
    compile_rule(rule)  # kiểm tra regex/điều kiện trước khi lưu
    definition = {key: rule[key] for key in ("conditions", "window", "exclude", "negatable",
                                             "ignore_warning_context") if key in rule}
    timestamp = timestamp or now()
    values = (rule["rule_id"], rule["name"], rule["category"], rule["description"], json_text(definition),
              int(rule["weight"]), int(rule.get("priority", 2)), rule["group"], timestamp)
    keep = "CASE WHEN detection_rules.customized=1 THEN detection_rules.{0} ELSE excluded.{0} END"
    db.execute("INSERT INTO detection_rules(rule_id,name,category,description,definition,weight,priority,"
               "dedup_group,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(rule_id) DO UPDATE SET "
               "name=excluded.name, category=excluded.category, description=excluded.description, "
               "definition=excluded.definition, dedup_group=excluded.dedup_group, updated_at=excluded.updated_at, "
               f"weight={keep.format('weight') if from_code else 'excluded.weight'}, "
               f"priority={keep.format('priority') if from_code else 'excluded.priority'}"
               + ("" if from_code else ", customized=1"), values)


def load_rules(db, *, enabled_only=True):
    query = "SELECT * FROM detection_rules" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY rule_id"
    rules = []
    for row in _rows(db, query):
        rule = {**json.loads(row["definition"]), "rule_id": row["rule_id"], "name": row["name"],
                "category": row["category"], "description": row["description"], "weight": row["weight"],
                "priority": row["priority"], "group": row["dedup_group"], "enabled": bool(row["enabled"]),
                "customized": bool(row["customized"])}
        rules.append(rule)
    return rules


def update_rule(db, rule_id, *, enabled=None, weight=None, priority=None):
    if weight is not None and not 0 <= int(weight) <= 100:
        raise ValueError("Trọng số phải trong khoảng 0–100.")
    if priority is not None and int(priority) not in {1, 2, 3}:
        raise ValueError("Mức ưu tiên phải là 1, 2 hoặc 3.")
    with db:
        changed = db.execute(
            "UPDATE detection_rules SET enabled=coalesce(?,enabled), weight=coalesce(?,weight), "
            "priority=coalesce(?,priority), customized=CASE WHEN ? OR ? THEN 1 ELSE customized END, "
            "updated_at=? WHERE rule_id=?",
            (None if enabled is None else int(bool(enabled)), weight, priority,
             weight is not None, priority is not None, now(), rule_id)).rowcount
    if not changed:
        raise ValueError(f"Không có rule {rule_id}.")


def get_settings(db):
    settings = {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULT_SETTINGS.items()}
    for row in _rows(db, "SELECT key, value FROM analysis_settings"):
        settings[row["key"]] = json.loads(row["value"])
    return settings


def set_setting(db, key, value):
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"Không có cấu hình {key}.")
    if key == "risk_thresholds":
        medium, high = int(value["medium"]), int(value["high"])
        if not 0 < medium < high <= 100:
            raise ValueError("Ngưỡng phải thỏa 0 < medium < high ≤ 100.")
        value = {"medium": medium, "high": high}
    elif key in {"warning_context_factor", "similarity_threshold"}:
        value = float(value)
        if not 0 <= value <= 1:
            raise ValueError(f"{key} phải trong khoảng 0–1.")
    else:
        value = float(value) if not float(value).is_integer() else int(float(value))
        if value < 0:
            raise ValueError(f"{key} không được âm.")
    with db:
        db.execute("INSERT INTO analysis_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=excluded.value, updated_at=excluded.updated_at", (key, json_text(value), now()))
    return value


def ruleset_hash(rules, settings):
    """Dấu vân tay của bộ rule + cấu hình; đổi rule/ngưỡng thì các bài cần chấm lại."""
    payload = [(rule["rule_id"], rule["weight"], rule["group"], rule["priority"], rule["conditions"],
                rule.get("window"), rule.get("exclude"), rule.get("negatable")) for rule in rules]
    scoring_keys = ("risk_thresholds", "warning_context_factor", "income_per_day_threshold",
                    "return_per_month_threshold", "max_evidence_per_rule")
    raw = json.dumps([ENGINE_VERSION, payload, {key: settings.get(key) for key in scoring_keys}],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------- Từ khóa ----------
def list_keywords(db, *, enabled_only=False, category=None):
    query = "SELECT * FROM keywords WHERE 1=1"
    params = []
    if enabled_only:
        query += " AND enabled=1"
    if category:
        query += " AND category=?"
        params.append(category)
    return _rows(db, query + " ORDER BY category, id", params)


def add_keyword(db, keyword, category="OTHER", origin="user"):
    keyword = " ".join((keyword or "").split())
    if not keyword or len(keyword) > 120:
        raise ValueError("Từ khóa phải có 1–120 ký tự.")
    if category not in CATEGORIES:
        raise ValueError("Nhóm từ khóa phải là JOB, INVESTMENT hoặc OTHER.")
    norm = keyword_norm(keyword)
    if not norm:
        raise ValueError("Từ khóa phải chứa chữ hoặc số.")
    with db:
        existing = db.execute("SELECT id FROM keywords WHERE keyword_norm=?", (norm,)).fetchone()
        if existing:
            db.execute("UPDATE keywords SET enabled=1 WHERE id=?", (existing[0],))
            return existing[0], False
        cursor = db.execute("INSERT INTO keywords(keyword,keyword_norm,category,origin,enabled,created_at) "
                            "VALUES(?,?,?,?,1,?)", (keyword, norm, category, origin, now()))
        return cursor.lastrowid, True


def remove_keyword(db, identity):
    """Xóa từ khóa người dùng/mở rộng. Từ khóa mặc định chỉ bị TẮT (nếu xóa, lần khởi động sau sẽ nạp lại)."""
    where, value = ("id=?", int(identity)) if str(identity).isdigit() else ("keyword_norm=?", keyword_norm(identity))
    with db:
        disabled = db.execute(f"UPDATE keywords SET enabled=0 WHERE origin='default' AND {where}", (value,)).rowcount
        deleted = db.execute(f"DELETE FROM keywords WHERE origin!='default' AND {where}", (value,)).rowcount
    return deleted + disabled


def set_keyword_enabled(db, keyword_id, enabled):
    with db:
        return db.execute("UPDATE keywords SET enabled=? WHERE id=?", (int(bool(enabled)), keyword_id)).rowcount


def mark_keywords_used(db, keywords):
    with db:
        for keyword in keywords:
            db.execute("UPDATE keywords SET last_used_at=? WHERE keyword_norm=?", (now(), keyword_norm(keyword)))


# ---------- Đề xuất mở rộng truy vấn ----------
def save_suggestions(db, suggestions):
    """Lưu đề xuất ở trạng thái PENDING; đề xuất đã bị từ chối/chấp nhận không bị đưa lại."""
    with db:
        for item in suggestions:
            db.execute("INSERT INTO keyword_suggestions(phrase,phrase_norm,category,score,support,example_post_id,"
                       "status,created_at) VALUES(?,?,?,?,?,?,'PENDING',?) ON CONFLICT(phrase_norm) DO UPDATE SET "
                       "score=excluded.score, support=excluded.support, category=excluded.category, "
                       "example_post_id=excluded.example_post_id WHERE keyword_suggestions.status='PENDING'",
                       (item["phrase"], keyword_norm(item["phrase"]), item["category"], item["score"],
                        item["support"], item.get("example_post_id"), now()))


def list_suggestions(db, status="PENDING"):
    return _rows(db, "SELECT * FROM keyword_suggestions WHERE status=? ORDER BY score DESC, id", (status,))


def decide_suggestion(db, suggestion_id, accept, category=None):
    """Bước xác nhận của người dùng: chỉ đề xuất được chấp nhận mới thành từ khóa."""
    rows = _rows(db, "SELECT * FROM keyword_suggestions WHERE id=?", (suggestion_id,))
    if not rows:
        raise ValueError(f"Không có đề xuất {suggestion_id}.")
    suggestion = rows[0]
    keyword_id = None
    if accept:
        keyword_id, _ = add_keyword(db, suggestion["phrase"], category or suggestion["category"], "expansion")
    with db:
        db.execute("UPDATE keyword_suggestions SET status=?, decided_at=? WHERE id=?",
                   ("ACCEPTED" if accept else "REJECTED", now(), suggestion_id))
    return keyword_id
