"""Đọc permalink trong tab phụ khi thẻ nguồn vẫn bị rút gọn hoặc thiếu người đăng."""
from fb_scraper.common.content import fuller_text, is_collapsed_text
from fb_scraper.common.logs import get_logger
from fb_scraper.common.page_actions import expand_see_more, human_pause, open_page
from fb_scraper.common.urls import canonical_post_url, is_login_url

log = get_logger(__name__)


async def complete_post(page, post):
    url = canonical_post_url(post.get("url", ""))
    if not url:
        return post
    from fb_scraper.posts.reader import EXTRACT_JS
    from fb_scraper.posts.parser import parse_article

    detail = None
    try:
        detail = await page.context.new_page()
        await open_page(detail, url)
        await human_pause(1.5, 2.5)
        if is_login_url(detail.url):
            return post
        await expand_see_more(detail)
        for raw in await detail.evaluate(EXTRACT_JS):
            candidate = parse_article(raw, post["source"])
            if candidate and candidate["post_id"] == post["post_id"]:
                post["text"] = fuller_text(post.get("text"), candidate.get("text"))
                if candidate.get("author"):
                    post["author"] = candidate["author"]
                break
        if is_collapsed_text(post.get("text")):
            print(f"  Chưa mở hết nội dung bài {post['post_id']} trên giao diện Facebook.", flush=True)
    except Exception as error:
        log.warning("Không đọc được permalink %s", url, exc_info=True)
        print(f"  Chưa đọc được nội dung đầy đủ ({type(error).__name__}); giữ phần đã đọc.", flush=True)
    finally:
        if detail is not None:
            await detail.close()
    return post
