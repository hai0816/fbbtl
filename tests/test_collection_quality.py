"""Kiểm tra các cải tiến độ chính xác khi thu thập: thời gian, ID ổn định, quảng cáo, ảnh, OCR, sửa dữ liệu."""
import asyncio
from datetime import datetime, timezone
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fb_scraper.common.times import parse_post_time
from fb_scraper.common.urls import make_post_id
from fb_scraper.posts import images
from fb_scraper.posts.parser import parse_article
from fb_scraper.storage import database as storage
from fb_scraper.storage.repair import repair
from tests.persistence_fixtures import PersistenceFixture

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)


def local_iso(*args):
    return datetime(*args).astimezone().astimezone(timezone.utc).isoformat()


class TimeParsingTests(unittest.TestCase):
    def test_relative_and_absolute_formats(self):
        cases = {
            "39 phút trước": "2026-11-19T02:21:00+00:00", "2 giờ · Công khai": "2026-11-19T01:00:00+00:00",
            "3d": "2026-11-16T03:00:00+00:00", "Vừa xong": NOW.isoformat(),
            "about an hour ago": "2026-11-19T02:00:00+00:00",
            "Thứ Sáu, 28 Tháng 8, 2026 lúc 17:35": local_iso(2026, 8, 28, 17, 35),
            "10 Tháng 11 lúc 08:03": local_iso(2026, 11, 10, 8, 3),
            "November 10, 2026 at 8:03 PM": local_iso(2026, 11, 10, 20, 3),
            "10 November 2026": local_iso(2026, 11, 10, 0, 0),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_post_time(raw, NOW), expected)

    def test_date_without_year_never_lands_in_future(self):
        self.assertTrue(parse_post_time("25 Tháng 12", NOW).startswith("2025-12"))

    def test_text_that_is_not_a_timestamp(self):
        for raw in ("3 ngày nữa sẽ có quà", "Xem thêm", "Công khai", "", "Lương 10 triệu/tháng"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_post_time(raw, NOW))


class PostIdentityTests(unittest.TestCase):
    RAW = {"author": "ACFC Careers", "imgs": [], "labels": [], "stats_text": "",
           "links": [{"href": "https://hrform.example/x?utm_medium=paid&utm_source=fb", "text": ""}]}

    def test_collapsed_and_expanded_text_share_one_id_without_permalink(self):
        # Facebook chỉ rút gọn bài dài (vài dòng); phần hiển thị luôn dài hơn 60 chữ/số dùng làm vân tay.
        head = "LEVI'S HÀ NỘI TUYỂN DỤNG - CƠ HỘI GIA NHẬP THƯƠNG HIỆU THỜI TRANG HÀNG ĐẦU. Apply ngay"
        short = parse_article({**self.RAW, "text": head + "… Xem thêm"}, "kw:x")
        full = parse_article({**self.RAW, "text": head + ": Levi's đang tìm kiếm những gương mặt mới."}, "kw:x")
        self.assertEqual(short["post_id"], full["post_id"])
        self.assertNotEqual(make_post_id("", full["text"], "Shop khác"), full["post_id"])
        self.assertEqual(make_post_id("https://www.facebook.com/a/posts/123", "x"), "123")

    def test_sponsored_flag_and_hidden_characters(self):
        post = parse_article({**self.RAW, "text": "Tuyển nhân viên bán hàng toàn thời gian tại Hà Nội"}, "kw:x")
        self.assertTrue(post["is_sponsored"])
        organic = parse_article({**self.RAW, "links": [], "text": "Tuyển nhân viên bán hàng toàn thời gian"}, "kw:x")
        self.assertFalse(organic["is_sponsored"])
        labelled = parse_article({**self.RAW, "links": [], "labels": ["Được tài trợ"], "text": "Nội dung quảng cáo đủ dài."}, "k")
        self.assertTrue(labelled["is_sponsored"])
        self.assertIsNone(parse_article({**self.RAW, "text": "͏" * 40}, "kw:x"))

    def test_image_meta_keeps_photo_page_for_full_size_download(self):
        raw = {**self.RAW, "text": "Bài có ảnh tuyển dụng đủ dài để lưu.", "imgs": [
            {"src": "https://scontent.example/a.jpg", "w": 320, "h": 320, "page": "https://www.facebook.com/photo/?fbid=1"}]}
        post = parse_article(raw, "kw:x")
        self.assertEqual(post["image_meta"][0]["page"], "https://www.facebook.com/photo/?fbid=1")


class FakeResponse:
    def __init__(self, status, body=b"x" * 2000, content_type="image/jpeg"):
        self.status, self.ok, self._body = status, 200 <= status < 300, body
        self.headers = {"content-type": content_type}

    async def body(self):
        return self._body


class ImageDownloadTests(PersistenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        storage.IMG_DIR.mkdir(parents=True)
        self.enterContext(patch.object(images, "RETRY_DELAYS", (0, 0)))

    def context(self, *responses):
        request = types.SimpleNamespace(get=AsyncMock(side_effect=list(responses)))
        return types.SimpleNamespace(request=request, new_page=AsyncMock())

    def test_retries_network_errors_then_saves_with_real_extension(self):
        context = self.context(OSError("getaddrinfo ENOTFOUND"), FakeResponse(503), FakeResponse(200, content_type="image/png"))
        post = {"post_id": "p", "images": ["https://scontent.example/a.png"]}
        files = asyncio.run(images.download_images(context, post))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith("p_0.png"))
        self.assertEqual(context.request.get.await_count, 3)

    def test_expired_link_and_non_image_are_not_retried_or_saved(self):
        context = self.context(FakeResponse(403), FakeResponse(200, b"<html>", "text/html"))
        post = {"post_id": "p", "images": ["https://scontent.example/a.jpg", "https://scontent.example/b.jpg"]}
        self.assertEqual(asyncio.run(images.download_images(context, post)), [])
        self.assertEqual(context.request.get.await_count, 2)

    def test_full_size_image_is_used_when_ocr_needs_it(self):
        context = self.context(FakeResponse(200))
        post = {"post_id": "p", "images": ["https://scontent.example/small.jpg"],
                "image_meta": [{"src": "https://scontent.example/small.jpg", "w": 320,
                                "page": "https://www.facebook.com/photo/?fbid=9"}]}
        with patch.object(images, "resolve_full_image", new=AsyncMock(return_value="https://scontent.example/big.jpg")):
            asyncio.run(images.download_images(context, post, full_size=True))
        self.assertEqual(context.request.get.await_args.args[0], "https://scontent.example/big.jpg")
        self.assertTrue(post["media"][0]["metadata"]["full_size"])
        self.assertEqual(post["media"][0]["media_url"], "https://scontent.example/small.jpg")


class OcrOrderingTests(unittest.TestCase):
    def test_fragments_are_joined_in_reading_order(self):
        sys.modules.setdefault("easyocr", types.ModuleType("easyocr"))
        from fb_scraper.common.ocr import order_lines
        box = lambda x, y: [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]
        detections = [(box(200, 102), "HOA HỒNG", 0.9), (box(0, 100), "NHẬN", 0.95),
                      (box(0, 10), "NẠP TIỀN", 0.8), (box(0, 200), "rác", 0.1)]
        lines, confidence = order_lines(detections, 0.35)
        self.assertEqual(lines, ["NẠP TIỀN", "NHẬN HOA HỒNG"])
        self.assertAlmostEqual(confidence, (0.9 + 0.95 + 0.8) / 3)


class RepairTests(PersistenceFixture, unittest.TestCase):
    def test_repair_fills_times_and_sponsored_without_deleting(self):
        db = storage.init_db()
        self.addCleanup(db.close)
        storage.save_post(db, {"post_id": "1", "text": "Bài có bình luận cần sửa thời gian",
                               "source_ref": "https://www.facebook.com/a",
                               "links": ["https://x.example/?utm_medium=paid&utm_source=fb"],
                               "top_comments": [{"comment_id": "c", "text": "Hay", "posted_raw": "Thứ Sáu, 28 Tháng 8, 2026 lúc 17:35"}]})
        db.execute("UPDATE comments SET created_at=NULL")
        db.commit()
        stats = repair(db)
        self.assertEqual((stats["comment_times"], stats["sponsored"]), (1, 1))
        self.assertEqual(db.execute("SELECT created_at FROM comments").fetchone()[0], local_iso(2026, 8, 28, 17, 35))
        self.assertTrue(json.loads(db.execute("SELECT metadata FROM posts").fetchone()[0])["is_sponsored"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
