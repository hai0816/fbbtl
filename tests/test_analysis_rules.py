"""Kiểm tra tiền xử lý, rule engine và chấm điểm (không cần database)."""
import unittest

from fb_scraper.analysis.default_rules import DEFAULT_SETTINGS, RULES
from fb_scraper.analysis.features import extract
from fb_scraper.analysis.rules import build_segments, compile_rule
from fb_scraper.analysis.scoring import assess
from fb_scraper.analysis.similarity import analyze
from fb_scraper.analysis.text import accent_ratio, fold, lower_form

COMPILED = [compile_rule(rule) for rule in RULES]


def score(text, ocr=(), settings=None):
    return assess(build_segments(text, ocr), extract("\n".join([text, *ocr])), COMPILED, settings or DEFAULT_SETTINGS)


class TextTests(unittest.TestCase):
    def test_fold_handles_accents_abbreviations_and_obfuscation(self):
        self.assertEqual(fold("Tuyển CTV, N.Ạ.P T.I.Ề.N, k cần kn"), "tuyen cong tac vien, nap tien, khong can kinh nghiem")
        self.assertIn("500k/ngay", fold("lương 500k/ngày"))  # "500k" không bị đổi thành "không"

    def test_lower_and_folded_forms_are_aligned(self):
        text = "Tuyển CTV online, Đăng ký www.Vi-Du.vn/Đăng nhận 1tr5"
        self.assertEqual(len(lower_form(text)), len(fold(text)))
        self.assertGreater(accent_ratio(lower_form(text), fold(text)), 0.08)
        self.assertEqual(accent_ratio("tuyen ctv", "tuyen ctv"), 0)

    def test_features_normalize_income_and_returns(self):
        features = extract("Lương 500k/ngày, 150.000đ/giờ, 1tr5 mỗi tuần. Lãi 3%/ngày. Giảm giá 50%. Zalo 0900.000.001")
        self.assertEqual(features["max_daily_income"], 1200000)
        self.assertEqual(features["max_monthly_return_pct"], 90.0)
        self.assertEqual([item["text"] for item in features["return_rates"]], ["3%/ngay"])  # "giảm giá 50%" bị loại
        self.assertEqual(features["phones"], ["0900000001"])
        self.assertIn("zalo", features["contact_channels"])


class RuleEngineTests(unittest.TestCase):
    def test_job_scam_post_scores_high_with_evidence(self):
        result = score("Tuyển CTV online, việc nhẹ lương cao 500k/ngày. Nạp tiền để kích hoạt nhiệm vụ nhận hoa hồng.")
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["category"], "JOB_SCAM_INDICATORS")
        self.assertTrue({"J04", "J06", "X01"} <= set(result["matched_rules"]))
        j04 = next(item for item in result["matches"] if item["rule_id"] == "J04")
        self.assertIn("Nạp tiền", j04["evidence"][0]["text"])
        self.assertTrue(j04["evidence"][0]["terms"])
        self.assertIn("không phải xác suất", result["explanation"])

    def test_warning_post_is_not_flagged_for_mentioning_deposit(self):
        result = score("Không nên nạp tiền trước khi nhận việc! Đây là thủ đoạn lừa đảo, mọi người hãy tỉnh táo.")
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["category"], "WARNING_OR_AWARENESS")
        self.assertNotIn("J03", result["matched_rules"])

    def test_negation_in_legitimate_job_post(self):
        result = score("Tuyển nhân viên bán hàng, không thu phí hồ sơ, không đặt cọc. Lương 8 triệu/tháng.")
        self.assertEqual(result["risk_score"], 0)

    def test_negation_does_not_cross_clause_boundary(self):
        result = score("Việc không khó, chỉ cần nạp 200k để kích hoạt nhiệm vụ nhận hoa hồng")
        self.assertIn("J04", result["matched_rules"])

    def test_self_claimed_trust_is_not_treated_as_warning(self):
        result = score("Uy tín 100%, cam kết không lừa đảo. Chuyển khoản 200k để nhận việc trong vòng 30 phút.")
        self.assertNotEqual(result["context"]["type"], "WARNING")
        self.assertIn("C01", result["matched_rules"])
        self.assertIn("J03", result["matched_rules"])

    def test_accent_sensitive_terms_avoid_false_matches(self):
        result = score("Cửa hàng bán sản phẩm, lại có quà. Nhà mặt tiền quận 1. Dùng điện thoại đặt hàng.")
        self.assertEqual(result["matched_rules"], [])
        unaccented = score("tuyen ctv lam nhiem vu, nap tien de nhan hoa hong, lai moi ngay")
        self.assertIn("I06", unaccented["matched_rules"])  # bài cố tình viết không dấu vẫn bị bắt

    def test_equivalent_rules_are_not_double_counted(self):
        result = score("Đóng phí hồ sơ 300k để nhận việc. Đặt cọc 500k để giữ chỗ vị trí cộng tác viên.")
        counted = [item for item in result["matches"] if item["group"] == "job_upfront_payment" and item["counted"]]
        self.assertEqual(len(counted), 1)
        self.assertTrue(result["suppressed"])
        self.assertIn("Không cộng trùng", result["explanation"])

    def test_investment_and_job_to_investment_categories(self):
        invest = score("Đầu tư sinh lời 3%/ngày, cam kết không rủi ro. Muốn rút tiền cần đóng phí 10%.")
        self.assertEqual(invest["category"], "INVESTMENT_SCAM_INDICATORS")
        self.assertTrue({"I01B", "I02", "I04"} <= set(invest["matched_rules"]))
        pivot = score("Tuyển cộng tác viên online. Sau khi làm nhiệm vụ được mời vào sàn USDT lợi nhuận cố định, "
                      "nạp vốn càng nhiều lãi càng cao.")
        self.assertEqual(pivot["category"], "JOB_TO_INVESTMENT_INDICATORS")

    def test_ocr_text_is_scored_and_labelled(self):
        result = score("Việc làm online tại nhà.", ["NẠP TIỀN KÍCH HOẠT NHIỆM VỤ - HOA HỒNG 30%"])
        evidence = next(item for item in result["matches"] if item["rule_id"] == "J04")["evidence"][0]
        self.assertEqual(evidence["source"], "ocr")

    def test_thresholds_are_configurable_and_score_is_bounded(self):
        text = "Nạp tiền để kích hoạt nhiệm vụ."
        low = score(text, settings={**DEFAULT_SETTINGS, "risk_thresholds": {"medium": 50, "high": 90}})
        default = score(text)
        self.assertEqual(low["risk_score"], default["risk_score"])
        self.assertEqual((low["risk_level"], default["risk_level"]), ("LOW", "MEDIUM"))
        many = score("Việc nhẹ lương cao 900k/ngày không cần kinh nghiệm. Nạp tiền làm nhiệm vụ nhận hoa hồng. "
                     "Đóng phí hồ sơ để nhận việc, gấp trong 10 phút. Rút tiền phải nộp thuế. Cam kết không rủi ro.")
        self.assertEqual(many["risk_score"], 100)

    def test_compile_rejects_invalid_clause(self):
        with self.assertRaises(ValueError):
            compile_rule({**RULES[0], "conditions": [{"unknown": []}]})


class SimilarityTests(unittest.TestCase):
    def test_paraphrased_posts_cluster_together(self):
        documents = {1: fold("Tuyển CTV online, làm nhiệm vụ nhận hoa hồng."),
                     2: fold("Tuyển cộng tác viên làm online, hoàn thành nhiệm vụ để nhận hoa hồng."),
                     3: fold("Bán nhà mặt tiền quận 3 giá tốt")}
        result = analyze(documents, {1: {"phones": ["0900000001"]}, 2: {"phones": ["0900000001"]}}, 0.5)
        self.assertEqual(result["clusters"][0]["post_ids"], [1, 2])
        self.assertEqual(result["clusters"][0]["shared_phones"], ["0900000001"])
        self.assertGreaterEqual(result["pairs"][0]["score"], 0.5)
        self.assertEqual(len(result["clusters"]), 1)


if __name__ == "__main__":
    unittest.main()
