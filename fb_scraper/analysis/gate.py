"""Bộ lọc khi thu thập: chấm điểm bài bằng chính bộ rule TRƯỚC khi lưu.

Facebook search trả nhiều bài chỉ NHẮC tới lừa đảo (báo chí, cảnh báo, review phim) hoặc tin tuyển dụng
bình thường. Khi bật scam_filter, bài chỉ được lưu nếu:
  - không bị nhận diện là bài cảnh báo (category != WARNING_OR_AWARENESS),
  - điểm rủi ro ≥ filter_min_score (cấu hình, mặc định 10), và
  - có ít nhất 2 nhóm dấu hiệu độc lập HOẶC một dấu hiệu cốt lõi (priority 1: yêu cầu chuyển tiền…).
Bài có ảnh mà phần chữ chưa đạt ngưỡng sẽ được OCR rồi xét lại (lừa đảo hay đặt chữ trong ảnh).
Lưu ý: bộ lọc giảm nhiễu nhưng làm mất mẫu âm; khi xây tập dữ liệu đánh giá nên TẮT bộ lọc.
"""
from fb_scraper.analysis import store
from fb_scraper.analysis.features import extract
from fb_scraper.analysis.rules import build_segments, compile_rule
from fb_scraper.analysis.scoring import assess


class ScamGate:
    def __init__(self, db):
        store.ensure_defaults(db)
        self.settings = store.get_settings(db)
        self.rules = [compile_rule(rule) for rule in store.load_rules(db)]
        self.min_score = self.settings.get("filter_min_score", 10)

    def check(self, text, ocr_texts=()):
        segments = build_segments(text or "", list(ocr_texts))
        result = assess(segments, extract("\n".join([text or "", *ocr_texts])), self.rules, self.settings)
        counted = [item for item in result["matches"] if item["counted"] and item["category"] != "COMBO"]
        groups = {item["group"] for item in counted}
        core = any(rule["priority"] == 1 for rule in self.rules
                   if rule["rule_id"] in {item["rule_id"] for item in counted})
        if result["category"] == "WARNING_OR_AWARENESS":
            keep, reason = False, "bài cảnh báo/nâng cao nhận thức"
        elif result["risk_score"] < self.min_score:
            keep, reason = False, f"điểm {result['risk_score']} < ngưỡng lọc {self.min_score}"
        elif len(groups) < 2 and not core:
            # Một cụm từ đơn lẻ ("việc nhẹ lương cao") cũng xuất hiện trong báo chí, review, tin tuyển dụng thật.
            keep, reason = False, f"chỉ có một dấu hiệu yếu ({', '.join(item['rule_id'] for item in counted)})"
        else:
            keep, reason = True, f"điểm {result['risk_score']} ({', '.join(result['matched_rules'])})"
        return {"keep": keep, "reason": reason, "score": result["risk_score"], "category": result["category"]}
