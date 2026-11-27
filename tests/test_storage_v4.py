"""Schema v4: lịch sử phiên bản, nhật ký tìm kiếm, đường dẫn tương đối, WAL và xóa tường minh."""
import hashlib
import json
import sqlite3
import unittest
from contextlib import closing

from fb_scraper.storage import database as storage
from fb_scraper.storage.migration import upgrade
from fb_scraper.storage.schema import create_tables
from fb_scraper.web import data as web_data
from tests.persistence_fixtures import PersistenceFixture
from fb_scraper.storage.schema import SCHEMA_VERSION


class StorageV4Tests(PersistenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.db = storage.init_db()
        self.addCleanup(self.db.close)

    def versions(self, key="p"):
        return storage.load_post(self.db, key)["versions"]

    def test_edited_post_keeps_previous_content_as_evidence(self):
        storage.save_post(self.db, {"post_id": "p", "text": "Bản gốc của bài viết", "author": "A", "likes": 1})
        storage.save_post(self.db, {"post_id": "p", "text": "Bản gốc của bài viết", "likes": 1})
        self.assertEqual(len(self.versions()), 1, "Lưu lại y hệt không tạo phiên bản mới")
        storage.save_post(self.db, {"post_id": "p", "text": "Bài đã bị sửa nội dung", "likes": 3})
        first, second = self.versions()
        self.assertEqual(first["content"], "Bản gốc của bài viết")
        self.assertEqual(second["content"], "Bài đã bị sửa nội dung")
        self.assertEqual(second["content_sha256"], hashlib.sha256("Bài đã bị sửa nội dung".encode()).hexdigest())
        self.assertEqual((first["reaction_count"], second["reaction_count"]), (1, 3))
        self.assertEqual(storage.load_post(self.db, "p")["text"], "Bài đã bị sửa nội dung")

    def test_empty_lists_never_delete_and_clear_is_explicit(self):
        storage.save_post(self.db, {"post_id": "p", "links": ["https://example.org"], "images": ["https://img/1.jpg"],
                                    "top_comments": [{"comment_id": "1", "text": "Gốc", "is_reply": False}]})
        storage.save_post(self.db, {"post_id": "p", "top_comments": [], "links": [], "images": []})
        post = storage.load_post(self.db, "p")
        self.assertEqual(len(post["top_comments"]), 1)
        self.assertEqual(post["links"], ["https://example.org"])
        self.assertEqual(post["images"], ["https://img/1.jpg"])
        self.assertEqual(storage.clear_comments(self.db, "p"), 1)
        self.assertEqual(storage.load_post(self.db, "p")["top_comments"], [])
        self.assertEqual(storage.clear_comments(self.db, "missing"), 0)

    def test_downloaded_file_path_is_relative_to_data_folder(self):
        image = self.data / "images" / "p_0.jpg"
        image.write_bytes(b"image-bytes")
        storage.save_post(self.db, {"post_id": "p", "images": ["https://img/p.jpg"], "image_files": [str(image)]})
        media, = storage.load_post(self.db, "p")["media"]
        self.assertEqual(media["file_path"], "images/p_0.jpg")
        self.assertEqual(media["file_hash"], hashlib.sha256(b"image-bytes").hexdigest())

    def test_search_hits_are_rows_not_growing_json(self):
        result = {"keyword": "k", "entity_type": "profile", "name": "N", "url": "https://www.facebook.com/n",
                  "search_url": "https://www.facebook.com/search/people/?q=k", "found_at": "2026-01-01T00:00:00+00:00"}
        for _ in range(5):
            storage.save_search_result(self.db, result)
        self.assertEqual(storage.count_rows(self.db, "search_hits"), 5)
        self.assertEqual(storage.count_rows(self.db, "sources"), 1)
        metadata, = self.db.execute("SELECT metadata FROM sources").fetchone()
        self.assertNotIn("search_history", json.loads(metadata))

    def test_interface_reads_while_collector_transaction_is_open(self):
        storage.save_post(self.db, {"post_id": "p", "text": "Đã lưu trước"})
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE posts SET content='Đang ghi' WHERE post_key='p'")
        try:
            # WAL: giao diện đọc bản đã commit ngay, không chờ khóa ghi của collector.
            self.assertEqual(web_data.summary()["posts"], 1)
            self.assertEqual(web_data.get_post("p")["text"], "Đã lưu trước")
        finally:
            self.db.rollback()


class UpgradeV3Tests(PersistenceFixture, unittest.TestCase):
    def test_v3_database_moves_history_paths_and_seeds_versions(self):
        self.data.mkdir(parents=True)
        image = self.data / "images" / "p_0.jpg"
        with closing(sqlite3.connect(storage.DB_PATH)) as db:
            create_tables(db)
            # Dựng lại dữ liệu kiểu v3: chưa có lịch sử, search_history trong JSON, đường dẫn tuyệt đối.
            db.execute("DELETE FROM post_versions")
            history = [{"keyword": "k", "entity_type": "profile", "name": "N", "found_at": "2026-01-01T00:00:00+00:00",
                        "search_url": "https://www.facebook.com/search/people/?q=k"}] * 2
            db.execute("INSERT INTO sources(id,source_type,source_label,source_ref,metadata) VALUES(1,'profile','N',?,?)",
                       ("https://www.facebook.com/n", json.dumps({"search_history": history})))
            db.execute("INSERT INTO posts(id,source_id,post_key,content,last_seen_at) VALUES(1,1,'p','Nội dung','2026-02-01')")
            db.execute("INSERT INTO media(post_id,media_type,file_path) VALUES(1,'image',?)", (str(image),))
            db.execute("PRAGMA user_version=3")
            db.commit()
        db = storage.init_db()
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(storage.count_rows(db, "search_hits"), 2)
        self.assertNotIn("search_history", json.loads(db.execute("SELECT metadata FROM sources").fetchone()[0]))
        self.assertEqual(db.execute("SELECT file_path FROM media").fetchone()[0], "images/p_0.jpg")
        version = db.execute("SELECT content, seen_at FROM post_versions").fetchall()
        self.assertEqual(version, [("Nội dung", "2026-02-01")])
        self.assertTrue(list(self.data.glob(f"posts.before-v{SCHEMA_VERSION}-*.db")))
        # Mở lại không chuyển lần hai.
        upgrade(db, storage.DB_PATH, self.data)
        self.assertEqual(storage.count_rows(db, "search_hits"), 2)


if __name__ == "__main__":
    unittest.main()
