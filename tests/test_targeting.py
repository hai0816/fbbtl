"""Kiểm tra thu thập đúng mục tiêu: bài mới nhất, bộ lọc dấu hiệu, thống kê hiệu quả từ khóa."""
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fb_scraper.analysis import analytics, monitor, pipeline as analysis_pipeline, store
from fb_scraper.analysis.gate import ScamGate
from fb_scraper.common.models import CollectionOptions
from fb_scraper.posts import collector, pipeline
from fb_scraper.storage import database as storage
from tests.persistence_fixtures import PersistenceFixture

SCAM = "Tuyển CTV online làm tại nhà, nạp tiền để kích hoạt nhiệm vụ, hoàn thành nhận hoa hồng 20%. Ib zalo."
REVIEW = "VIỆC NHẸ LƯƠNG CAO CÓ THẬT SỰ TỒN TẠI: một bộ phim sinh tồn mới đang gây sốt phòng vé."
WARNING = "Cảnh báo: không nên nạp tiền trước khi nhận việc, đây là thủ đoạn lừa đảo. Hãy tỉnh táo."
PITCH = ("Giờ việc nhẹ lương cao không ai tin, sợ lừa đảo. Chỗ chị đảm bảo uy tín cho các em sinh viên "
         "yên tâm kiếm thêm thu nhập trang trải cuộc sống. Inbox zalo chị nhé")


class SearchUrlTests(unittest.TestCase):
    def test_recent_filter_is_added_only_when_requested(self):
        plain = collector.build_sources(["việc làm"])[0]["url"]
        recent = collector.build_sources(["việc làm"], recent_first=True)[0]["url"]
        self.assertNotIn("filters=", plain)
        encoded = parse_qs(urlparse(recent).query)["filters"][0]
        self.assertIn("recent_posts", base64.b64decode(encoded).decode())
        self.assertEqual(parse_qs(urlparse(recent).query)["q"], ["việc làm"])


class GateTests(PersistenceFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.db = storage.init_db()
        self.addCleanup(self.db.close)

    def test_gate_keeps_scam_and_pitch_but_drops_review_warning_and_single_weak_signal(self):
        gate = ScamGate(self.db)
        self.assertTrue(gate.check(SCAM)["keep"])
        self.assertTrue(gate.check(PITCH)["keep"])  # J09 + C05 + C01… nhiều nhóm dấu hiệu độc lập
        self.assertFalse(gate.check(REVIEW)["keep"])
        self.assertIn("một dấu hiệu yếu", gate.check(REVIEW)["reason"])
        self.assertFalse(gate.check(WARNING)["keep"])
        self.assertTrue(gate.check("Việc làm tại nhà, đơn giản.", ["NẠP TIỀN KÍCH HOẠT NHIỆM VỤ NHẬN HOA HỒNG"])["keep"])

    async def test_pipeline_skips_filtered_posts_and_reports_counts(self):
        posts = [{"post_id": "a", "url": "https://www.facebook.com/x/posts/1", "text": SCAM, "author": "A"},
                 {"post_id": "b", "url": "https://www.facebook.com/x/posts/2", "text": REVIEW, "author": "B"},
                 {"post_id": "c", "url": "https://www.facebook.com/x/posts/3", "text": WARNING, "author": "C"}]

        async def source(*args):
            for post in posts:
                yield dict(post)

        options = CollectionOptions(max_posts=3, max_comments=0, scam_filter=True)
        with patch.object(pipeline, "iter_source_posts", new=source), \
             patch.object(pipeline, "scrape_top_comments", new=AsyncMock(return_value=[])), \
             patch.object(pipeline, "download_images", new=AsyncMock(return_value=[])):
            stats = await pipeline.collect_source_posts(None, None, self.db, {"name": "kw:x"}, options)
        self.assertEqual(storage.known_ids(self.db), {"a"})
        self.assertEqual((stats["seen"], stats["saved"], stats["filtered"]), (3, 1, 2))

    async def test_filter_off_keeps_everything(self):
        async def source(*args):
            yield {"post_id": "b", "url": "https://www.facebook.com/x/posts/2", "text": REVIEW, "author": "B"}

        with patch.object(pipeline, "iter_source_posts", new=source), \
             patch.object(pipeline, "scrape_top_comments", new=AsyncMock(return_value=[])), \
             patch.object(pipeline, "download_images", new=AsyncMock(return_value=[])):
            stats = await pipeline.collect_source_posts(None, None, self.db, {"name": "kw:x"},
                                                        CollectionOptions(max_posts=1))
        self.assertEqual((stats["saved"], stats["filtered"], stats["filter_enabled"]), (1, 0, False))


class KeywordPerformanceTests(PersistenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.db = storage.init_db()
        self.addCleanup(self.db.close)
        store.ensure_defaults(self.db)

    def test_stats_accumulate_and_yield_is_computed(self):
        keyword = "tuyển ctv chốt đơn tại nhà"
        plan = {"kind": "keyword", "mode": "keyword", "value": keyword}
        monitor.record_status(self.db, plan, True, stats={"seen": 10, "saved": 2, "filtered": 8})
        monitor.record_status(self.db, plan, True, stats={"seen": 5, "saved": 1, "filtered": 4})
        for number, text in enumerate((SCAM, PITCH, REVIEW)):
            storage.save_post(self.db, {"post_id": f"k{number}", "text": text, "source": f"kw:{keyword}",
                                        "source_ref": "https://www.facebook.com/p"})
        analysis_pipeline.run_analysis(self.db)
        row = next(item for item in analytics.keyword_performance(self.db) if item["keyword"] == keyword)
        self.assertEqual((row["runs"], row["seen"], row["filtered"], row["collected"]), (2, 15, 12, 3))
        self.assertEqual(row["yield"], round(row["flagged"] / 3, 3))
        self.assertGreaterEqual(row["flagged"], 1)
        stats = json.loads(self.db.execute("SELECT stats FROM keywords WHERE keyword=?", (keyword,)).fetchone()[0])
        self.assertTrue(stats["last"]["ok"])

    def test_default_keyword_is_disabled_not_deleted(self):
        identity = self.db.execute("SELECT id FROM keywords WHERE origin='default' LIMIT 1").fetchone()[0]
        self.assertEqual(store.remove_keyword(self.db, identity), 1)
        store.ensure_defaults(self.db)
        enabled = self.db.execute("SELECT enabled FROM keywords WHERE id=?", (identity,)).fetchone()[0]
        self.assertEqual(enabled, 0)

    def test_operational_keywords_are_seeded(self):
        keywords = {row["keyword"] for row in store.list_keywords(self.db)}
        self.assertIn("tuyển ctv chốt đơn tại nhà", keywords)
        self.assertIn("Việc nhẹ lương cao", keywords)


if __name__ == "__main__":
    unittest.main()
