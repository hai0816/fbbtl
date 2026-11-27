"""Risk Scoring Engine.

Công thức (mọi số hạng đều hiển thị trong phần giải thích):
    1. Mỗi rule kích hoạt có điểm hiệu lực  e_r = w_r × m_r
         m_r = hệ số ngữ cảnh cảnh báo (mặc định 0,3) nếu bài được phân loại là bài CẢNH BÁO,
               hoặc nếu mọi bằng chứng của rule nằm trong câu cảnh báo; ngược lại m_r = 1.
    2. Chống cộng trùng: các rule cùng nhóm tương đương (dedup_group) chỉ lấy  max(e_r).
    3. Rule kết hợp (COMBO) được xét trên tập nhóm đã kích hoạt rồi cộng như một nhóm riêng.
    4. risk_score = min(100, làm tròn(Σ_nhóm max e_r)).
    5. Mức: LOW < medium ≤ MEDIUM < high ≤ HIGH (ngưỡng cấu hình trong analysis_settings).
Reaction/share/số bình luận KHÔNG tham gia công thức. Điểm là mức ưu tiên kiểm tra,
không phải xác suất bài viết là lừa đảo.
"""
from fb_scraper.analysis.default_rules import MENTION_GROUPS
from fb_scraper.analysis.rules import document_context, match_rule, mention_groups

CATEGORY_LABELS = {
    "JOB_SCAM_INDICATORS": "Dấu hiệu lừa đảo tuyển dụng",
    "INVESTMENT_SCAM_INDICATORS": "Dấu hiệu lừa đảo đầu tư",
    "JOB_TO_INVESTMENT_INDICATORS": "Tuyển dụng chuyển hướng sang đầu tư",
    "MIXED_INDICATORS": "Dấu hiệu tuyển dụng và đầu tư",
    "RECOVERY_SCAM_INDICATORS": "Dịch vụ 'lấy lại tiền bị lừa'",
    "OTHER_INDICATORS": "Dấu hiệu chung",
    "WARNING_OR_AWARENESS": "Bài cảnh báo/nâng cao nhận thức",
    "NO_INDICATOR": "Chưa phát hiện dấu hiệu",
}
LEVEL_LABELS = {"LOW": "Thấp", "MEDIUM": "Trung bình", "HIGH": "Cao"}


def risk_level(score, thresholds):
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    return "LOW"


def _category(groups, context, combos):
    if context["type"] == "WARNING":
        return "WARNING_OR_AWARENESS"
    if not groups:
        return "NO_INDICATOR"
    if "I07" in combos:
        return "JOB_TO_INVESTMENT_INDICATORS"
    totals = {"JOB": 0.0, "INVESTMENT": 0.0, "COMMON": 0.0}
    shared = []
    for item in groups.values():
        # Nhóm tương đương chứa rule của cả hai loại (J06/I03 "nạp tiền để nhận lại") được quy
        # cho loại đang chiếm ưu thế ở các nhóm còn lại, tránh bài đầu tư bị gắn nhãn tuyển dụng.
        if {"JOB", "INVESTMENT"} <= item.get("categories", set()):
            shared.append(item)
            continue
        totals[item["category"]] = totals.get(item["category"], 0) + item["effective"]
    for item in shared:
        totals["JOB" if totals["JOB"] >= totals["INVESTMENT"] else "INVESTMENT"] += item["effective"]
    if "recovery_scam" in groups and totals["JOB"] < 15 and totals["INVESTMENT"] < 15:
        return "RECOVERY_SCAM_INDICATORS"
    if totals["JOB"] >= 15 and totals["INVESTMENT"] >= 15:
        return "MIXED_INDICATORS"
    if totals["JOB"] == 0 and totals["INVESTMENT"] == 0:
        return "OTHER_INDICATORS"
    return "JOB_SCAM_INDICATORS" if totals["JOB"] >= totals["INVESTMENT"] else "INVESTMENT_SCAM_INDICATORS"


def assess(segments, features, rules, settings):
    """Chấm điểm một bài từ các câu đã tách và đặc trưng. rules: danh sách rule đã compile & đang bật."""
    context = document_context(segments, features)
    factor = settings["warning_context_factor"]
    max_evidence = settings.get("max_evidence_per_rule", 3)
    triggered = []
    for rule in rules:
        if rule["category"] == "COMBO":
            continue
        evidence = match_rule(rule, segments, features, settings, max_evidence)
        if not evidence:
            continue
        multiplier = 1.0
        if not rule.get("ignore_warning_context"):
            if context["type"] == "WARNING" or all(item["warning"] for item in evidence):
                multiplier = factor
        triggered.append({"rule": rule, "evidence": evidence, "multiplier": multiplier,
                          "effective": rule["weight"] * multiplier})

    groups, suppressed = {}, []
    for item in sorted(triggered, key=lambda entry: (-entry["effective"], entry["rule"]["priority"])):
        rule = item["rule"]
        if rule["group"] in groups:
            suppressed.append({"rule_id": rule["rule_id"], "kept": groups[rule["group"]]["rule_id"],
                               "group": rule["group"]})
            groups[rule["group"]]["categories"].add(rule["category"])
            continue
        groups[rule["group"]] = {"rule_id": rule["rule_id"], "category": rule["category"],
                                 "categories": {rule["category"]}, "effective": item["effective"]}

    present = set(groups) | mention_groups(segments, MENTION_GROUPS)
    combos = {}
    for rule in rules:
        if rule["category"] != "COMBO" or rule["group"] in groups:
            continue
        if all(clause["groups"] & present for clause in rule["_clauses"] if clause["kind"] == "groups"):
            contributors = sorted(rule_id for group, entry in groups.items()
                                  for rule_id in [entry["rule_id"]]
                                  if any(group in clause["groups"] for clause in rule["_clauses"]))
            multiplier = factor if context["type"] == "WARNING" else 1.0
            combos[rule["rule_id"]] = True
            groups[rule["group"]] = {"rule_id": rule["rule_id"], "category": "COMBO",
                                     "effective": rule["weight"] * multiplier}
            triggered.append({"rule": rule, "multiplier": multiplier, "effective": rule["weight"] * multiplier,
                              "evidence": [{"source": "rules", "text": "Kết hợp: " + ", ".join(contributors),
                                            "terms": contributors, "warning": False}]})

    score = min(100, round(sum(item["effective"] for item in groups.values())))
    level = risk_level(score, settings["risk_thresholds"])
    category = _category({key: value for key, value in groups.items() if value["category"] != "COMBO"},
                         context, combos)
    counted = {entry["rule_id"] for entry in groups.values()}
    matches = [{"rule_id": item["rule"]["rule_id"], "name": item["rule"]["name"],
                "category": item["rule"]["category"], "group": item["rule"]["group"],
                "weight": item["rule"]["weight"], "multiplier": item["multiplier"],
                "points": round(item["effective"], 1) if item["rule"]["rule_id"] in counted else 0,
                "counted": item["rule"]["rule_id"] in counted,
                "reason": item["rule"]["description"], "evidence": item["evidence"]}
               for item in sorted(triggered, key=lambda entry: entry["rule"]["rule_id"])]
    return {"risk_score": score, "risk_level": level, "category": category,
            "matched_rules": [item["rule_id"] for item in matches],
            "matches": matches, "suppressed": suppressed, "context": context,
            "explanation": explain(score, level, category, matches, suppressed, context, settings)}


def explain(score, level, category, matches, suppressed, context, settings):
    if not matches:
        text = "Không có rule nào kích hoạt."
    else:
        parts = [f"{item['rule_id']} {item['name']} (+{item['points']:g}"
                 + (f", ×{item['multiplier']:g} do ngữ cảnh cảnh báo" if item["multiplier"] != 1 else "") + ")"
                 for item in matches if item["counted"]]
        text = f"Điểm {score} = " + " + ".join(parts)
        if score == 100:
            text += " (giới hạn tối đa 100)"
        text += "."
    if suppressed:
        text += " Không cộng trùng: " + "; ".join(
            f"{item['rule_id']} (cùng nhóm '{item['group']}' với {item['kept']})" for item in suppressed) + "."
    if context["type"] == "WARNING":
        text += (f" Bài được nhận diện là nội dung CẢNH BÁO (từ cảnh báo: {', '.join(context['warning_terms'][:5])}); "
                 f"điểm các rule bị nhân {settings['warning_context_factor']:g}.")
    thresholds = settings["risk_thresholds"]
    text += (f" Mức {LEVEL_LABELS[level]} theo ngưỡng {thresholds['medium']}/{thresholds['high']}."
             f" Phân loại: {CATEGORY_LABELS[category]}."
             " Điểm là mức ưu tiên kiểm tra thủ công, không phải xác suất bài viết là lừa đảo;"
             " reaction/share/số bình luận không được dùng để tính điểm.")
    return text
