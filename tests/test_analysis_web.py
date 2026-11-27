"""Kiểm tra API trang Phân tích qua HTTP thật (server cục bộ, jobs giả)."""
import base64
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from fb_scraper.storage import database as storage
from fb_scraper.web.server import AppServer
from tests.persistence_fixtures import PersistenceFixture
from tests.test_web import FakeJobs

SAMPLE = Path(__file__).parent / "fixtures" / "sample_posts.csv"


class AnalysisWebTests(PersistenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.jobs = FakeJobs()
        self.enterContext(patch.object(AppServer, "browser_ready", return_value=False))
        storage.init_db().close()
        self.server = AppServer(("127.0.0.1", 0), self.jobs)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        self.addCleanup(connection.close)
        request_headers = {"Content-Type": "application/json", "X-Scraper-Token": self.server.token}
        request_headers.update(headers or {})
        connection.request("POST" if payload is not None else "GET", path,
                           json.dumps(payload) if payload is not None else None, request_headers)
        response = connection.getresponse()
        body = response.read()
        if response.getheader("Content-Type", "").startswith("application/json"):
            return response.status, json.loads(body)
        return response.status, body

    def upload(self):
        payload = {"file_name": "../sample posts.csv", "data_base64": base64.b64encode(SAMPLE.read_bytes()).decode()}
        return self.request("/api/analysis/import", payload)

    def test_static_page_and_import_then_overview(self):
        for path in ("/analysis", "/analysis.js", "/analysis.css"):
            self.assertEqual(self.request(path)[0], 200)
        status, result = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(result["inserted"], 20)
        self.assertTrue((storage.DATA / "imports" / "sample_posts.csv").exists())  # tên tệp đã được làm sạch
        status, overview = self.request("/api/analysis/overview")
        self.assertEqual(status, 200)
        self.assertEqual(overview["overview"]["posts"], 20)
        self.assertGreater(overview["overview"]["levels"]["HIGH"], 0)
        status, posts = self.request("/api/analysis/posts?level=HIGH&limit=5")
        self.assertEqual(status, 200)
        self.assertTrue(all(item["risk_level"] == "HIGH" for item in posts["items"]))
        detail = self.request(f"/api/analysis/posts/{posts['items'][0]['post_id']}")[1]
        self.assertTrue(detail["evidence"])
        self.assertIn("explanation", detail)
        status, csv_body = self.request("/api/analysis/export")
        self.assertEqual(status, 200)
        self.assertIn("risk_score", csv_body.decode("utf-8-sig").splitlines()[0])
        flagged = self.request("/api/analysis/posts?level=FLAGGED&sort=recent&limit=100")[1]
        self.assertEqual(flagged["total"], overview["overview"]["flagged"])
        self.assertTrue(all(item["risk_level"] in ("MEDIUM", "HIGH") for item in flagged["items"]))
        self.assertEqual(overview["overview"]["flagged_pending"], overview["overview"]["flagged"])
        high_csv = self.request("/api/analysis/export?level=HIGH")[1].decode("utf-8-sig").splitlines()
        self.assertEqual(len(high_csv) - 1, overview["overview"]["levels"]["HIGH"])
        listed = self.request("/api/posts?limit=50")[1]["items"]
        self.assertTrue(listed and all(item["risk"]["level"] in ("LOW", "MEDIUM", "HIGH") for item in listed))

    def test_actor_statistics_and_post_scope(self):
        self.upload()
        status, result = self.request("/api/analysis/actors")
        self.assertEqual(status, 200)
        self.assertEqual(sum(row["posts"] for row in result["places"]), 20)
        top = result["authors"][0]
        self.assertGreater(top["flagged"], 0)
        self.assertEqual(len(top["post_ids"]), top["posts"])
        scoped = self.request("/api/analysis/posts?ids=" + ",".join(map(str, top["post_ids"])))[1]
        self.assertEqual(scoped["total"], top["posts"])
        repeat = self.request("/api/analysis/actors?min_posts=2")[1]["authors"]
        self.assertTrue(all(row["posts"] >= 2 for row in repeat))
        csv_body = self.request("/api/analysis/actors/export?kind=places")[1].decode("utf-8-sig").splitlines()
        self.assertTrue(csv_body[0].startswith("label,type,url,posts,flagged"))
        self.assertEqual(len(csv_body) - 1, len(result["places"]))

    def test_quick_check_scores_pasted_text_without_saving(self):
        text = ("Tuyển CTV làm việc tại nhà, việc nhẹ lương cao 500k-1tr/ngày, không cần kinh nghiệm. "
                "Đóng trước 200k phí giữ chỗ, hoàn lại sau. Inbox Zalo 0912345678 để nhận việc ngay!")
        status, result = self.request("/api/analysis/check", {"text": text})
        self.assertEqual(status, 200)
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertTrue(any(match["counted"] for match in result["matches"]))
        self.assertIn("0912345678", result["features"]["phones"][0].replace(" ", ""))
        self.assertEqual(self.request("/api/analysis/check", {"text": "   "})[0], 400)
        self.assertEqual(self.request("/api/analysis/overview")[1]["overview"]["posts"], 0)  # không lưu bài

    def test_review_rules_settings_similarity_and_keywords(self):
        self.upload()
        post_id = self.request("/api/analysis/posts?limit=1")[1]["items"][0]["post_id"]
        self.assertEqual(self.request("/api/analysis/review", {"post_id": post_id, "status": "FALSE_POSITIVE"})[0], 200)
        self.assertEqual(self.request("/api/analysis/review", {"post_id": post_id, "status": "BAD"})[0], 400)
        self.assertEqual(self.request("/api/analysis/rules", {"rule_id": "J04", "enabled": False})[0], 200)
        rules = {rule["rule_id"]: rule for rule in self.request("/api/analysis/rules")[1]["rules"]}
        self.assertFalse(rules["J04"]["enabled"])
        bad = self.request("/api/analysis/settings", {"key": "risk_thresholds", "value": {"medium": 70, "high": 50}})
        self.assertEqual(bad[0], 400)
        self.assertEqual(self.request("/api/analysis/settings", {"key": "similarity_threshold", "value": 0.4})[0], 200)
        similar = self.request("/api/analysis/similar?threshold=0.4")[1]
        self.assertTrue(similar["clusters"])
        self.assertIn("không đủ để kết luận", similar["note"])
        status, expanded = self.request("/api/analysis/expand", {"level": "MEDIUM"})
        self.assertEqual(status, 200)
        suggestion = self.request("/api/analysis/keywords")[1]["suggestions"][0]
        self.assertEqual(self.request("/api/analysis/suggestions", {"id": suggestion["id"], "accept": True})[0], 200)
        keywords = [row["keyword"] for row in self.request("/api/analysis/keywords")[1]["keywords"]]
        self.assertIn(suggestion["phrase"], keywords)
        self.assertEqual(self.request("/api/analysis/evaluate")[0], 200)

    def test_collect_uses_existing_job_manager_and_requires_token(self):
        payload = {"target": "keywords", "category": "JOB", "max_posts": 5}
        self.assertEqual(self.request("/api/analysis/collect", payload, {"X-Scraper-Token": "wrong"})[0], 403)
        status, result = self.request("/api/analysis/collect", payload)
        self.assertEqual(status, 202)
        plan = self.jobs.configs[0]["plan"]
        self.assertEqual(result["sources"], len(plan))
        self.assertTrue(plan and all(item["kind"] == "keyword" for item in plan))
        self.assertEqual(self.jobs.configs[0]["options"]["max_posts"], 5)
        self.assertEqual(self.request("/api/analysis/sources", {"action": "add", "url": "https://example.org"})[0], 400)
        self.assertEqual(self.request("/api/analysis/sources", {"action": "add",
                                                                 "url": "https://www.facebook.com/groups/42"})[0], 200)
        sources = self.request("/api/analysis/sources")[1]["sources"]
        self.assertEqual(sources[0]["monitor_enabled"], 1)

    def test_upload_validation(self):
        status, body = self.request("/api/analysis/import", {"file_name": "x.csv", "data_base64": "@@@"})
        self.assertEqual(status, 400)
        status, body = self.request("/api/analysis/import", {"file_name": "x.txt",
                                                             "data_base64": base64.b64encode(b"abc").decode()})
        self.assertEqual(status, 400)
        self.assertIn("Định dạng", body["error"])


if __name__ == "__main__":
    unittest.main()
