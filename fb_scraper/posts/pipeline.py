"""Lưu bài trước, rồi bổ sung bình luận, ảnh và OCR."""
import asyncio
from pathlib import Path

from fb_scraper.common.comments import merge_comment_records, needs_comment_refresh
from fb_scraper.common.content import fuller_text
from fb_scraper.common.logs import get_logger
from fb_scraper.posts.comments import COMMENT_SCAN_VERSION, scrape_top_comments
from fb_scraper.posts.reader import iter_source_posts
from fb_scraper.posts.images import download_images
from fb_scraper.storage.database import known_ids, load_post, mark_comments_scanned, save_post

log = get_logger(__name__)


async def read_images(context, post, ocr):
    """Tải ảnh (bản lớn khi bật OCR) và đọc chữ; kết quả ghi vào post["image_files"], post["ocr"]."""
    image_files = await download_images(context, post, full_size=bool(ocr))
    post["image_files"] = image_files
    if ocr and image_files:
        post["ocr"] = [await asyncio.to_thread(ocr.read, image_file) for image_file in image_files]


def discard_files(post):
    """Bài bị bộ lọc loại: xóa ảnh đã tải để không để lại tệp mồ côi trong data/images."""
    for path in post.get("image_files", []):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            log.warning("Không xóa được ảnh tạm %s", path, exc_info=True)


async def collect_source_posts(page, context, db, source, options, known_post_ids=None, ocr=None, post_fields=None):
    """Thu thập và lưu bài của một nguồn; lưu nội dung trước, sau đó mới bổ sung bình luận, ảnh và OCR.

    options: CollectionOptions (max_posts, max_comments).
    known_post_ids: tập post_id đã có trong SQLite, được cập nhật khi lưu bài mới.
    post_fields: trường liên kết nguồn (entity_type, entity_id) gắn thêm vào bài.
    """
    if known_post_ids is None:
        known_post_ids = known_ids(db)

    saved_count = 0
    skipped_count = 0
    updated_count = 0
    seen_count = 0
    filtered_count = 0
    gate = None
    if getattr(options, "scam_filter", False):
        from fb_scraper.analysis.gate import ScamGate
        gate = ScamGate(db)

    async for post in iter_source_posts(page, source, options.max_posts):
        seen_count += 1
        if post_fields:
            post.update(post_fields)

        post_id = post["post_id"]
        if post_id in known_post_ids:
            previous_post = load_post(db, post_id)
            source_changed = False
            # Đọc lại phải cập nhật tên và nội dung, kể cả bài đã có đủ bình luận.
            full_text = fuller_text(previous_post.get("text"), post.get("text"))
            if full_text != previous_post.get("text", ""):
                previous_post["text"] = full_text
                source_changed = True
            if post.get("author") and post["author"] != previous_post.get("author"):
                previous_post["author"] = post["author"]
                source_changed = True
            if post.get("url") and not previous_post.get("url"):
                previous_post["url"] = post["url"]
                source_changed = True

            if post_fields:
                for field, value in post_fields.items():
                    if previous_post.get(field) != value:
                        source_changed = True
                        break

            if source_changed:
                previous_post.update(post_fields or {})
            for field in ("likes", "comments", "shares", "posted_at", "posted_raw"):
                if post.get(field) not in (None, ""):
                    previous_post[field] = post[field]
            # last_seen_at/last_checked_at phải đổi cả khi bài và bình luận đã đầy đủ.
            excluded = {"top_comments", "media", "images", "image_files", "ocr", "source_record", "comment_records"}
            save_post(db, {field: value for field, value in previous_post.items() if field not in excluded})

            old_comments = previous_post["top_comments"]
            # Bài quét bằng cách đọc cũ (chưa lấy được reply) được quét lại dù đã đủ bình luận gốc.
            outdated_scan = previous_post.get("comment_scan_version", 0) < COMMENT_SCAN_VERSION
            if needs_comment_refresh(old_comments, options.max_comments) or (options.max_comments > 0 and outdated_scan):
                print(f"  Bổ sung bình luận cho bài đã có: {post_id}", flush=True)
                comment_url = post["url"]
                if not comment_url:
                    comment_url = previous_post["url"]

                new_comments = await scrape_top_comments(context, comment_url, options.max_comments)
                if new_comments:
                    # Giữ các bản ghi cũ và quan hệ đã biết khi DOM chỉ tải một phần.
                    previous_post["top_comments"] = merge_comment_records(old_comments, new_comments)
                    if post["url"]:
                        previous_post["url"] = post["url"]

                    save_post(db, previous_post)
                    mark_comments_scanned(db, post_id, COMMENT_SCAN_VERSION)
                    updated_count += 1
                    comment_count = len(previous_post["top_comments"])
                    print(f"  Đã lưu {comment_count} bình luận vào SQLite", flush=True)

            skipped_count += 1
            continue

        ocr_done = False
        if gate is not None:
            verdict = gate.check(post.get("text"))
            if not verdict["keep"] and ocr and post.get("images"):
                # Phần chữ chưa đủ dấu hiệu: đọc chữ trong ảnh rồi xét lại.
                await read_images(context, post, ocr)
                ocr_done = True
                verdict = gate.check(post.get("text"), [item.get("text", "") for item in post.get("ocr", [])])
            if not verdict["keep"]:
                filtered_count += 1
                discard_files(post)
                print(f"  Bỏ qua {post_id}: {verdict['reason']}", flush=True)
                continue

        # SQLite commit ngay: lỗi tải ảnh/OCR hoặc Ctrl+C về sau vẫn giữ nội dung này.
        save_post(db, post)
        known_post_ids.add(post_id)
        saved_count += 1
        print(f"  Đã lưu {post_id} vào SQLite", flush=True)

        try:
            comments = await scrape_top_comments(context, post["url"], options.max_comments)
            post["top_comments"] = comments

            if comments:
                save_post(db, post)
                mark_comments_scanned(db, post_id, COMMENT_SCAN_VERSION)
            if options.max_comments:
                print(f"  Đã lưu {len(comments)} bình luận cho {post_id}", flush=True)

            if not ocr_done:
                await read_images(context, post, ocr)

        except Exception as error:
            log.warning("Bước bổ sung bình luận/ảnh/OCR lỗi cho bài %s", post_id, exc_info=True)
            print(f"  Bỏ qua bước bổ sung bị lỗi ({type(error).__name__}); nội dung bài đã lưu.")

        if post.get("image_files") or post.get("ocr"):
            save_post(db, post)

    print(
        f"  Đã lưu mới: {saved_count}; bài đã có: {skipped_count}; "
        f"bài bổ sung bình luận: {updated_count}"
        + (f"; bị bộ lọc dấu hiệu loại: {filtered_count}" if gate is not None else ""),
        flush=True,
    )
    # Thống kê để đo hiệu quả từ khóa/nguồn (worker ghi vào keywords.stats / sources).
    return {"seen": seen_count, "saved": saved_count, "existing": skipped_count,
            "filtered": filtered_count, "filter_enabled": gate is not None}
