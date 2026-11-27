"""Lệnh phân tích: python main.py analyze <lệnh con> [tùy chọn]. Không mở trình duyệt.

  run [--force]                      tiền xử lý + chấm điểm (tăng dần; --force chấm lại tất cả)
  report [--json FILE]               thống kê tổng quan, rule, thời gian, nội dung tương tự
  list [--level HIGH] [--limit 20]   bài theo điểm rủi ro, kèm giải thích
  show POST                          chi tiết một bài: rule, bằng chứng, đặc trưng, ngữ cảnh bình luận
  review POST STATUS [--note ...]    ghi kết quả duyệt: CONFIRMED_SCAM | FALSE_POSITIVE | NEEDS_INFO | PENDING
  similar [--threshold 0.5] [--flagged]
  rules [--enable ID] [--disable ID] [--weight ID=SỐ] [--import FILE.json]
  keywords [--add TEXT --category JOB] [--remove ID|TEXT] [--disable ID] [--enable ID]
  expand [--level MEDIUM] [--text "..."] | suggestions [--accept ID] [--reject ID]
  import FILE [--format csv|json|jsonl|sqlite] [--table posts] [--dataset TÊN]
  evaluate [--apply]                 precision/recall theo ngưỡng từ nhãn duyệt/dataset; --apply lưu ngưỡng gợi ý
  settings [KEY=VALUE ...]           xem/đổi cấu hình (risk_thresholds=25,60; similarity_threshold=0.5 ...)
"""
import argparse
import json
from pathlib import Path

from fb_scraper.analysis import analytics, evaluation, expansion, importer, pipeline, store
from fb_scraper.analysis.scoring import CATEGORY_LABELS, LEVEL_LABELS
from fb_scraper.storage import database as storage


def _post_id(db, value):
    row = db.execute("SELECT id FROM posts WHERE id=? OR post_key=?",
                     (int(value) if str(value).isdigit() else -1, str(value))).fetchone()
    if row is None:
        raise ValueError(f"Không tìm thấy bài {value}.")
    return row[0]


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_list(db, args):
    query = ("SELECT r.post_id, p.post_key, r.risk_score, r.risk_level, r.category, r.matched_rules, r.review_status, "
             "substr(replace(coalesce(p.content,''),char(10),' '),1,110) FROM risk_assessments r JOIN posts p "
             "ON p.id=r.post_id" + (" WHERE r.risk_level=?" if args.level else "") +
             " ORDER BY r.risk_score DESC, r.post_id LIMIT ?")
    params = ((args.level,) if args.level else ()) + (args.limit,)
    for post_id, key, score, level, category, rules, review, preview in db.execute(query, params):
        print(f"#{post_id:<5} {score:>3} {LEVEL_LABELS[level]:<10} {CATEGORY_LABELS.get(category, category):<38} "
              f"{','.join(json.loads(rules)):<28} [{review}]\n       {preview}")


def cmd_show(db, args):
    post_id = _post_id(db, args.post)
    cursor = db.execute("SELECT r.*, a.features, a.comment_context, a.duplicate_of, a.ocr_text, p.post_key, p.url, "
                        "p.content FROM risk_assessments r JOIN post_analysis a ON a.post_id=r.post_id "
                        "JOIN posts p ON p.id=r.post_id WHERE r.post_id=?", (post_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("Bài chưa được phân tích; chạy: main.py analyze run")
    record = pipeline.assessment_view(dict(zip((column[0] for column in cursor.description), row)))
    output = {"post_id": record["post_key"], "category": record["category"], "risk_score": record["risk_score"],
              "risk_level": record["risk_level"], "matched_rules": record["matched_rules"],
              "review_status": record["review_status"], "explanation": record["explanation"],
              "evidence": [{"rule_id": item["rule_id"], "points": item["points"], "reason": item["reason"],
                            "matches": item["evidence"]} for item in record["evidence"]],
              "context": record["context"], "features": record["features"],
              "comment_context": record["comment_context"], "duplicate_of": record["duplicate_of"],
              "url": record["url"]}
    _print(output)


def cmd_rules(db, args):
    store.ensure_defaults(db)
    if args.import_file:
        rules = json.loads(Path(args.import_file).read_text(encoding="utf-8"))
        with db:
            for rule in rules if isinstance(rules, list) else [rules]:
                store.write_rule(db, rule)
        print(f"Đã nhập {len(rules) if isinstance(rules, list) else 1} rule.")
    for rule_id in args.enable or []:
        store.update_rule(db, rule_id, enabled=True)
    for rule_id in args.disable or []:
        store.update_rule(db, rule_id, enabled=False)
    for item in args.weight or []:
        rule_id, _, value = item.partition("=")
        store.update_rule(db, rule_id, weight=int(value))
    for rule in store.load_rules(db, enabled_only=False):
        flag = "BẬT" if rule["enabled"] else "tắt"
        print(f"{rule['rule_id']:<5} [{flag}] w={rule['weight']:<3} p={rule['priority']} {rule['category']:<10} "
              f"{rule['group']:<20} {rule['name']}")


def cmd_keywords(db, args):
    store.ensure_defaults(db)
    if args.add:
        identity, created = store.add_keyword(db, args.add, args.category)
        print(("Đã thêm" if created else "Đã có (bật lại)") + f" từ khóa #{identity}.")
    if args.remove:
        print(f"Đã xóa {store.remove_keyword(db, args.remove)} từ khóa.")
    for identity in args.disable or []:
        store.set_keyword_enabled(db, int(identity), False)
    for identity in args.enable or []:
        store.set_keyword_enabled(db, int(identity), True)
    for row in store.list_keywords(db):
        print(f"#{row['id']:<4} [{'BẬT' if row['enabled'] else 'tắt'}] {row['category']:<10} {row['origin']:<9} "
              f"{row['keyword']}  (dùng lần cuối: {row['last_used_at'] or '—'})")


def cmd_suggestions(db, args):
    for identity in args.accept or []:
        store.decide_suggestion(db, int(identity), True, args.category)
        print(f"Đã chấp nhận đề xuất #{identity} thành từ khóa.")
    for identity in args.reject or []:
        store.decide_suggestion(db, int(identity), False)
        print(f"Đã từ chối đề xuất #{identity}.")
    for row in store.list_suggestions(db):
        print(f"#{row['id']:<4} {row['score']:>6} support={row['support']:<3} {row['category']:<10} {row['phrase']}")


def cmd_settings(db, args):
    store.ensure_defaults(db)
    for item in args.values:
        key, _, value = item.partition("=")
        if key == "risk_thresholds":
            medium, high = value.split(",")
            value = {"medium": medium, "high": high}
        print(f"{key} = {store.set_setting(db, key, value)}")
    _print(store.get_settings(db))


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py analyze", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run").add_argument("--force", action="store_true")
    sub.add_parser("report").add_argument("--json", dest="json_file")
    item = sub.add_parser("list")
    item.add_argument("--level", choices=("LOW", "MEDIUM", "HIGH"))
    item.add_argument("--limit", type=int, default=20)
    sub.add_parser("show").add_argument("post")
    item = sub.add_parser("review")
    item.add_argument("post")
    item.add_argument("status", choices=("CONFIRMED_SCAM", "FALSE_POSITIVE", "NEEDS_INFO", "PENDING"))
    item.add_argument("--note")
    item = sub.add_parser("similar")
    item.add_argument("--threshold", type=float)
    item.add_argument("--flagged", action="store_true")
    item = sub.add_parser("rules")
    item.add_argument("--enable", action="append")
    item.add_argument("--disable", action="append")
    item.add_argument("--weight", action="append", metavar="ID=SỐ")
    item.add_argument("--import", dest="import_file")
    item = sub.add_parser("keywords")
    item.add_argument("--add")
    item.add_argument("--category", default="OTHER", choices=sorted(store.CATEGORIES))
    item.add_argument("--remove")
    item.add_argument("--disable", action="append")
    item.add_argument("--enable", action="append")
    item = sub.add_parser("expand")
    item.add_argument("--level", default="MEDIUM", choices=("LOW", "MEDIUM", "HIGH"))
    item.add_argument("--text")
    item.add_argument("--limit", type=int, default=20)
    item = sub.add_parser("suggestions")
    item.add_argument("--accept", action="append")
    item.add_argument("--reject", action="append")
    item.add_argument("--category", choices=sorted(store.CATEGORIES))
    item = sub.add_parser("import")
    item.add_argument("file")
    item.add_argument("--format", choices=("csv", "json", "jsonl", "sqlite", "db"))
    item.add_argument("--table")
    item.add_argument("--dataset")
    sub.add_parser("evaluate").add_argument("--apply", action="store_true")
    sub.add_parser("settings").add_argument("values", nargs="*", metavar="KEY=VALUE")
    return parser


def dispatch(db, args):
    if args.command == "run":
        _print(pipeline.run_analysis(db, force=args.force))
    elif args.command == "report":
        pipeline.run_analysis(db)
        result = analytics.report(db, store.load_rules(db, enabled_only=False), store.get_settings(db))
        if args.json_file:
            Path(args.json_file).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Đã ghi {args.json_file}")
        _print({key: result[key] for key in ("disclaimer", "overview")} |
               {"top_rules": result["signals"]["rules"][:10], "phrases": result["phrases"][:10],
                "clusters": [{key: cluster[key] for key in ("cluster_id", "size", "post_ids", "max_similarity",
                                                            "top_terms")} for cluster in result["similarity"]["clusters"]]})
    elif args.command == "list":
        cmd_list(db, args)
    elif args.command == "show":
        cmd_show(db, args)
    elif args.command == "review":
        pipeline.set_review(db, _post_id(db, args.post), args.status, args.note)
        print("Đã ghi kết quả duyệt.")
    elif args.command == "similar":
        threshold = args.threshold if args.threshold is not None else store.get_settings(db)["similarity_threshold"]
        result = analytics.similar_content(db, threshold, args.flagged)
        _print({key: result[key] for key in ("threshold", "documents", "clusters", "note")})
    elif args.command == "rules":
        cmd_rules(db, args)
    elif args.command == "keywords":
        cmd_keywords(db, args)
    elif args.command == "expand":
        pipeline.run_analysis(db)
        for item in expansion.suggest(db, min_level=args.level, text=args.text, limit=args.limit):
            print(f"{item['score']:>6}  support={item['support']:<3} {item['category']:<10} {item['phrase']}")
        print("Đề xuất ở trạng thái chờ duyệt: main.py analyze suggestions --accept ID")
    elif args.command == "suggestions":
        cmd_suggestions(db, args)
    elif args.command == "import":
        result = importer.import_file(db, args.file, file_format=args.format, table=args.table, dataset=args.dataset)
        result["analysis"] = pipeline.run_analysis(db)
        storage.export_jsonl(db)
        _print(result)
    elif args.command == "evaluate":
        pipeline.run_analysis(db)
        result = evaluation.evaluate(db, store.get_settings(db))
        print(evaluation.as_text(result))
        if args.apply and result["suggested"]:
            print("Đã lưu ngưỡng:", store.set_setting(db, "risk_thresholds", result["suggested"]))
            pipeline.run_analysis(db)
    elif args.command == "settings":
        cmd_settings(db, args)


def run_cli(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    db = storage.init_db()
    try:
        store.ensure_defaults(db)
        dispatch(db, args)
    except (ValueError, OSError) as error:
        parser.exit(1, f"LỖI: {error}\n")
    finally:
        db.close()
