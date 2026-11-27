"""Kiểm tra migration v5, nhập dataset, pipeline tăng dần, duyệt, hiệu chỉnh ngưỡng, mở rộng từ khóa."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest

from fb_scraper.analysis import analytics, evaluation, expansion, importer, monitor, pipeline, store
from fb_scraper.storage import database as storage
from fb_scraper.storage.schema import SCHEMA_VERSION
from tests.persistence_fixtures import PersistenceFixture

SAMPLE = Path(__file__).parent / "fixtures" / "sample_posts.csv"
V4_SOURCES = """CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL CHECK(source_type IN ('profile', 'page', 'group')), source_label TEXT NOT NULL,
    source_ref TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_checked_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}')"""


class MigrationV5Tests(PersistenceFixture, unittest.TestCase):
    def test_v4_database_is_rebuilt_without_losing_rows_and_backup_is_kept(self):
        self.data.mkdir(parents=True)
        with closing(sqlite3.connect(storage.DB_PATH)) as db:
            db.execute(V4_SOURCES)
            db.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL, "
                       "post_key TEXT NOT NULL UNIQUE, content TEXT, posted_at TEXT, is_shared INTEGER NOT NULL DEFAULT 0, "
                       "original_post_key TEXT, collected_at TEXT, last_seen_at TEXT, FOREIGN KEY (source_id) REFERENCES sources(id))")
            db.execute("INSERT INTO sources(id,source_type,source_label,source_ref) VALUES(7,'page','Trang','https://www.facebook.com/x')")
            db.execute("INSERT INTO posts(id,source_id,post_key,content) VALUES(3,7,'k','Nạp tiền để kích hoạt nhiệm vụ')")
            db.execute("PRAGMA user_version=4")
            db.commit()
        db = storage.init_db()
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(db.execute("SELECT id, source_type, source_label FROM sources").fetchall(), [(7, "page", "Trang")])
        self.assertEqual(db.execute("SELECT id, source_id FROM posts").fetchall(), [(3, 7)])
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        db.execute("INSERT INTO sources(source_type,source_label,source_ref) VALUES('dataset','D','dataset:d')")
        self.assertTrue(list(self.data.glob(f"posts.before-v{SCHEMA_VERSION}-*.db")))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO posts(source_id,post_key) VALUES(999,'orphan')")


class PipelineTests(PersistenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.db = storage.init_db()
        self.addCleanup(self.db.close)

    def imported(self):
        result = importer.import_file(self.db, SAMPLE)
        pipeline.run_analysis(self.db)
        return result

    def test_import_normalizes_time_records_source_and_skips_duplicates(self):
        result = self.imported()
        self.assertEqual((result["inserted"], result["errors"]), (20, []))
        source = self.db.execute("SELECT source_type, source_ref FROM sources WHERE id=?", (result["source_id"],)).fetchone()
        self.assertEqual(source, ("dataset", "dataset:sample_posts"))
        posted, metadata = self.db.execute("SELECT posted_at, metadata FROM posts WHERE post_key='demo-01'").fetchone()
        self.assertEqual(posted, "2026-11-18T01:15:00+00:00")  # 08:15 giờ Việt Nam
        self.assertEqual(json.loads(metadata)["collection_method"], "import")
        again = importer.import_file(self.db, SAMPLE)
        self.assertEqual((again["inserted"], again["updated"]), (0, 20))
        batches = self.db.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
        self.assertEqual(batches, 2)

    def test_import_handles_json_missing_fields_and_bad_rows(self):
        path = self.data / "rows.json"
        path.write_text(json.dumps({"posts": [{"text": "Đầu tư sinh lời 3%/ngày", "created_time": 1763424000},
                                              {"text": "Đầu tư sinh lời 3%/ngày", "created_time": 1763424000},
                                              {"author": "Thiếu nội dung"}, {"message": "Ok", "date": "không rõ"}]}),
                        encoding="utf-8")
        result = importer.import_file(self.db, path)
        self.assertEqual((result["inserted"], result["duplicates"]), (2, 1))
        self.assertEqual(result["errors"][0]["row"], 3)
        self.assertIn("thời gian", result["warnings"][0]["warning"])
        key = self.db.execute("SELECT post_key FROM posts WHERE content LIKE 'Đầu tư%'").fetchone()[0]
        self.assertTrue(key.startswith("import:"))

    def test_pipeline_is_incremental_keeps_raw_text_and_review_status(self):
        self.imported()
        self.assertEqual(pipeline.run_analysis(self.db)["analyzed"], 0)
        raw = self.db.execute("SELECT content FROM posts WHERE post_key='demo-16'").fetchone()[0]
        self.assertTrue(raw.startswith("tuyen ctv"))  # nội dung gốc không bị chuẩn hóa đè
        post_id = self.db.execute("SELECT id FROM posts WHERE post_key='demo-01'").fetchone()[0]
        pipeline.set_review(self.db, post_id, "CONFIRMED_SCAM", "Đã kiểm tra")
        storage.save_post(self.db, {"post_id": "demo-01", "text": "Nội dung mới: nạp tiền nhận hoa hồng"})
        self.assertEqual(pipeline.run_analysis(self.db)["analyzed"], 1)
        status = self.db.execute("SELECT review_status FROM risk_assessments WHERE post_id=?", (post_id,)).fetchone()[0]
        self.assertEqual(status, "CONFIRMED_SCAM")

    def test_rule_changes_trigger_rescoring_and_customization_survives_defaults(self):
        self.imported()
        store.update_rule(self.db, "J04", weight=5)
        store.update_rule(self.db, "C02", enabled=False)
        self.assertEqual(pipeline.run_analysis(self.db)["analyzed"], 20)
        store.ensure_defaults(self.db)
        rules = {rule["rule_id"]: rule for rule in store.load_rules(self.db, enabled_only=False)}
        self.assertEqual((rules["J04"]["weight"], rules["C02"]["enabled"]), (5, False))
        matched = [json.loads(row[0]) for row in self.db.execute("SELECT matched_rules FROM risk_assessments")]
        self.assertFalse(any("C02" in rules_ for rules_ in matched))

    def test_ocr_and_comments_are_analyzed_separately(self):
        storage.save_post(self.db, {"post_id": "p1", "text": "Việc làm online", "source_ref": "https://www.facebook.com/a",
                                    "ocr": [{"text": "NẠP TIỀN KÍCH HOẠT NHIỆM VỤ"}],
                                    "top_comments": [{"comment_id": "c1", "text": "Lừa đảo đấy, không rút được tiền"}]})
        pipeline.run_analysis(self.db)
        ocr, context = self.db.execute("SELECT ocr_text, comment_context FROM post_analysis").fetchone()
        self.assertIn("NẠP TIỀN", ocr)
        self.assertEqual(json.loads(context)["warning_mentions"], 1)
        rules = json.loads(self.db.execute("SELECT matched_rules FROM risk_assessments").fetchone()[0])
        self.assertIn("J04", rules)

    def test_evaluation_uses_labels_and_reviews(self):
        self.imported()
        result = evaluation.evaluate(self.db, store.get_settings(self.db))
        self.assertEqual((result["samples"], result["positives"]), (20, 12))
        current = evaluation.metrics_at(evaluation.labeled_scores(self.db), 25)
        self.assertGreaterEqual(current["recall"], 0.9)
        self.assertTrue(result["suggested"]["medium"] < result["suggested"]["high"])

    def test_expansion_requires_confirmation(self):
        self.imported()
        before = {row["keyword"] for row in store.list_keywords(self.db)}
        suggestions = expansion.suggest(self.db)
        self.assertTrue(suggestions)
        self.assertEqual({row["keyword"] for row in store.list_keywords(self.db)}, before)
        pending = store.list_suggestions(self.db)
        store.decide_suggestion(self.db, pending[0]["id"], True)
        store.decide_suggestion(self.db, pending[1]["id"], False)
        keywords = {row["keyword"] for row in store.list_keywords(self.db)}
        self.assertIn(pending[0]["phrase"], keywords)
        self.assertNotIn(pending[1]["phrase"], keywords)
        self.assertNotIn(pending[1]["phrase"], [item["phrase"] for item in expansion.suggest(self.db)])

    def test_text_expansion_extracts_meaningful_phrases(self):
        found = {item["phrase"] for item in expansion.suggest(
            self.db, text="Tuyển cộng tác viên online, nạp tiền để kích hoạt nhiệm vụ nhận hoa hồng.", save=False)}
        self.assertTrue({"kích hoạt nhiệm vụ", "nhận hoa hồng", "nạp tiền"} <= found)
        self.assertFalse(any("viên online nạp" in phrase for phrase in found))

    def test_keywords_crud_and_validation(self):
        store.ensure_defaults(self.db)
        identity, created = store.add_keyword(self.db, "  Việc  làm   ONLINE ", "JOB")
        self.assertFalse(created)  # trùng từ khóa mặc định sau chuẩn hóa
        identity, created = store.add_keyword(self.db, "Sàn USDT", "INVESTMENT")
        self.assertTrue(created)
        self.assertEqual(store.remove_keyword(self.db, identity), 1)
        with self.assertRaises(ValueError):
            store.add_keyword(self.db, "x", "BAD")

    def test_analytics_report_and_reused_contacts(self):
        self.imported()
        report = analytics.report(self.db, store.load_rules(self.db), store.get_settings(self.db))
        self.assertEqual(report["overview"]["posts"], 20)
        self.assertGreater(report["overview"]["flagged"], 0)
        self.assertIn("không đại diện", report["disclaimer"])
        phones = {item["value"] for item in report["reused"]["phones"]}
        self.assertIn("0900000001", phones)
        self.assertTrue(report["timeline"]["days"])
        self.assertTrue(report["similarity"]["clusters"])

    def test_monitor_plan_and_status(self):
        store.ensure_defaults(self.db)
        source_id = monitor.add_source(self.db, "https://www.facebook.com/groups/12345")
        plan = monitor.due_plan(self.db, 24)
        self.assertEqual(plan, [{"kind": "group", "mode": "group", "value": "https://www.facebook.com/groups/12345"}])
        monitor.record_status(self.db, plan[0], False, RuntimeError("Cần đăng nhập"))
        row = monitor.list_sources(self.db, monitored_only=True)[0]
        self.assertEqual((row["id"], row["collection_status"], row["last_error"]), (source_id, "error", "Cần đăng nhập"))
        monitor.record_status(self.db, plan[0], True)
        self.assertEqual(monitor.due_plan(self.db, 24), [])
        enabled = len(store.list_keywords(self.db, enabled_only=True))
        self.assertEqual(len(monitor.due_plan(self.db, 24, include_keywords=True)), enabled)
        with self.assertRaises(ValueError):
            monitor.add_source(self.db, "việc làm online")


if __name__ == "__main__":
    unittest.main()
