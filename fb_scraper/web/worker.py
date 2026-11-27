"""Tiến trình riêng cho lượt thu thập; hủy tác vụ vẫn đi qua finally để xuất dữ liệu."""
import asyncio
import json
import sys
import threading

from fb_scraper.common.logs import get_logger, setup_logging
from fb_scraper.common.runtime import load_env, setup_console

EVENT_PREFIX = "@@SCRAPER_EVENT "
log = get_logger(__name__)


def emit(**event):
    print(EVENT_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)


def watch_stop(loop, task):
    for line in sys.stdin:
        if line.strip() == "stop":
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
            return


def _record_status(db, source, ok, error=None, stats=None):
    """Ghi trạng thái thu thập của nguồn/từ khóa; lỗi ghi trạng thái không làm dừng lượt chạy."""
    try:
        from fb_scraper.analysis.monitor import record_status
        record_status(db, source, ok, error, stats)
    except Exception:
        log.warning("Không ghi được trạng thái nguồn %s", source.get("value"), exc_info=True)


def _analyze(db):
    """Chấm điểm rủi ro các bài mới/cập nhật sau lượt thu thập (chạy tăng dần, nhanh)."""
    try:
        from fb_scraper.analysis.pipeline import run_analysis
        result = run_analysis(db)
        if result["analyzed"]:
            levels = result["levels"]
            print(f"Đã phân tích {result['analyzed']} bài: cao {levels['HIGH']}, trung bình {levels['MEDIUM']}, "
                  f"thấp {levels['LOW']}.", flush=True)
    except Exception as error:
        log.warning("Phân tích rủi ro sau lượt thu thập lỗi", exc_info=True)
        print(f"Chưa phân tích được rủi ro ({type(error).__name__}); dữ liệu thu thập vẫn được lưu.", flush=True)


async def collect(config):
    from fb_scraper.browser.connection import browser_context
    from fb_scraper.common.models import CollectionOptions
    from fb_scraper.entities.collector import collect_entities
    from fb_scraper.posts.collector import build_sources
    from fb_scraper.posts.pipeline import collect_source_posts
    from fb_scraper.storage import database as storage

    options = CollectionOptions.model_validate(config["options"])
    plan = config["plan"]
    task = asyncio.current_task()
    threading.Thread(target=watch_stop, args=(asyncio.get_running_loop(), task), daemon=True).start()
    db = storage.init_db()
    cancelled = False
    try:
        # Nạp model EasyOCR một lần cho cả lượt; nguồn từ khóa và nguồn URL dùng chung.
        ocr = None
        if options.ocr and plan:
            from fb_scraper.common.ocr import OCREngine
            ocr = await asyncio.to_thread(OCREngine, gpu=options.gpu)
        known = storage.known_ids(db)
        async with browser_context() as context:
            for index, source in enumerate(plan):
                emit(type="source", current=index + 1, completed=index, total=len(plan), label=source["value"])
                stats = None
                print(f"Bắt đầu nguồn {index + 1}/{len(plan)}: {source['value']}", flush=True)
                try:
                    if source["kind"] == "keyword":
                        page = await context.new_page()
                        try:
                            stats = await collect_source_posts(
                                page, context, db, build_sources([source["value"]], options.recent_first)[0],
                                options, known, ocr)
                        finally:
                            await page.close()
                    else:
                        await collect_entities(context, [source["value"]], source["kind"], options, db=db,
                                               source_type="page" if source.get("mode") == "page" else None,
                                               ocr_engine=ocr)
                        known = storage.known_ids(db)
                except Exception as error:
                    # Một nguồn lỗi (trang tải chậm, bị chặn…) không làm hỏng các nguồn còn lại.
                    # CancelledError (nút Dừng) không phải Exception nên vẫn đi tiếp ra ngoài.
                    log.warning("Nguồn %s lỗi", source["value"], exc_info=True)
                    print(f"  Bỏ qua nguồn do lỗi ({type(error).__name__}): {error}", flush=True)
                    _record_status(db, source, False, error)
                else:
                    _record_status(db, source, True, stats=stats)
                emit(type="source_done", completed=index + 1, total=len(plan))
    except asyncio.CancelledError:
        cancelled = True
        print("Đã nhận yêu cầu dừng. Đang lưu kết quả đã thu thập…", flush=True)
    finally:
        try:
            _analyze(db)
            # export_jsonl đã gồm sources.jsonl (thông tin cá nhân/Trang/nhóm).
            storage.export_jsonl(db)
            print(f"Đã lưu dữ liệu. Database hiện có {storage.count_rows(db, 'posts')} bài viết.", flush=True)
        finally:
            db.close()
    return 130 if cancelled else 0


def main():
    setup_console()
    load_env()
    setup_logging()
    config = json.loads(sys.stdin.readline())
    try:
        if config.get("action") == "browser":
            from fb_scraper.browser.launcher import open_chrome
            open_chrome()
            return 0
        return asyncio.run(collect(config))
    except Exception as error:
        log.exception("Worker dừng do lỗi")
        print(f"LỖI: {error} (chi tiết trong data/logs/scraper.log)", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
