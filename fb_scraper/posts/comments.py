"""Đọc và tải thêm bình luận (kèm reply) trong tab riêng."""
import re

from playwright.async_api import BrowserContext, Page

from fb_scraper.common.comments import comment_key, merge_comment_records, normalize_comments
from fb_scraper.common.content import comment_author, is_collapsed_text
from fb_scraper.common.logs import get_logger
from fb_scraper.common.page_actions import expand_see_more, human_pause, open_page
from fb_scraper.common.urls import canonical_post_url, is_login_url
from fb_scraper.posts.comment_reader import EXTRACT_COMMENTS_JS
from fb_scraper.posts.labels import COMMENT_LABEL_JS, COMMENT_LABEL_RE, REPLY_LABEL_RE
from fb_scraper.posts.parser import parse_relative_time

log = get_logger(__name__)

# Tăng khi cách đọc bình luận thay đổi; bài đã quét bằng phiên bản cũ sẽ được quét lại.
# v2: mở reply bằng click trong trang, nhận nhãn "Phản hồi bình luận của…", giới hạn theo bình luận gốc.
COMMENT_SCAN_VERSION = 2
# Mỗi luồng giữ tối đa bấy nhiêu reply để một bình luận "hot" không chiếm hết thời gian đọc.
REPLIES_PER_ROOT = 20
SEPARATOR_LINES = {"·", "•", "…", "..."}

COMMENT_MORE_RE = re.compile(
    r"^\s*(?:Xem thêm bình luận|Xem (?:các )?bình luận trước|Xem bình luận khác|"
    r"Xem \d+ bình luận (?:khác|nữa)|Hiển thị thêm bình luận|"
    r"(?:View|See|Load) (?:more|previous|\d+ more) comments)[\s﻿]*$", re.I
)
REPLY_MORE_RE = re.compile(
    r"^\s*(?:Xem (?:thêm |tất cả )?(?:\d+ )?(?:câu trả lời|phản hồi)|"
    r"(?:View|See|Load) (?:(?:all|more|previous) )?(?:\d+ )?(?:more )?repl(?:y|ies))\s*$", re.I
)
# JavaScript cuộn vùng chứa bình luận; cuộn trang nếu không tìm thấy vùng cuộn riêng.
SCROLL_COMMENTS_JS = r"""() => {
  const comments = [...document.querySelectorAll('[role="article"][aria-label]')]
    .filter(el => __COMMENT_LABEL__.test(el.getAttribute('aria-label') || ''));
  let node = comments.at(-1)?.parentElement;
  while (node && node !== document.body) {
    if (/(auto|scroll)/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight) {
      node.scrollTop += Math.max(500, node.clientHeight * 0.8);
      return;
    }
    node = node.parentElement;
  }
  window.scrollBy(0, 700);
}""".replace("__COMMENT_LABEL__", COMMENT_LABEL_JS)

# Nút "Xem N phản hồi/câu trả lời" của Facebook có kích thước 0×0 nên Playwright coi là
# không hiển thị và từ chối bấm. click() gọi trong trang vẫn kích hoạt trình xử lý của React.
# Chỉ bấm nút có nhãn khớp đúng mẫu mở thêm (không bao giờ khớp "Trả lời"/"Gửi").
CLICK_BUTTONS_JS = r"""([pattern, limit]) => {
  const re = new RegExp(pattern, 'i');
  let clicked = 0;
  for (const button of document.querySelectorAll('[role="button"]')) {
    if (clicked >= limit) break;
    if (re.test((button.innerText || '').trim())) { button.click(); clicked++; }
  }
  return clicked;
}"""


def clean_comment_text(text):
    """Bỏ dòng chỉ có dấu phân cách (bình luận chỉ có ảnh/sticker từng bị lưu thành '·')."""
    lines = [line for line in (text or "").strip().splitlines() if line.strip() not in SEPARATOR_LINES]
    return "\n".join(lines).strip()


# Lọc nhãn bình luận và cập nhật dictionary collected theo ID để loại trùng trong lượt đọc.
def merge_comments(collected: dict, raw_comments: list):
    """Giữ ID và quan hệ trả lời thay vì chỉ trả về nội dung chuỗi."""
    incoming = []
    for raw_comment in raw_comments:
        label = raw_comment.get("label", "")
        if not COMMENT_LABEL_RE.match(label):
            continue

        text = clean_comment_text(raw_comment.get("text", ""))
        if not text and not raw_comment.get("media"):
            continue

        record = {key: value for key, value in raw_comment.items() if key not in {"id", "label"}}
        record.update(comment_id=raw_comment.get("id"), text=text)
        record["author"] = record.get("author") or comment_author(label)
        if not record.get("created_at"):
            record["created_at"] = parse_relative_time(record.get("posted_raw") or "")
        if "is_reply" not in record:
            record["is_reply"] = bool(REPLY_LABEL_RE.match(label))
        incoming.append(record)
    for comment in merge_comment_records(list(collected.values()), incoming):
        collected[comment_key(comment)] = comment


def root_count(comments):
    return sum(1 for comment in comments if not comment["is_reply"])


def select_threads(comments, n):
    """n bình luận gốc đầu tiên và các reply thuộc luồng của chúng (tối đa REPLIES_PER_ROOT/luồng).

    Reply chưa nối được với bình luận cha vẫn được giữ: đó là dữ liệu thật, giao diện ghi rõ
    "chưa xác định bình luận cha" thay vì đoán.
    """
    roots = [comment for comment in comments if not comment["is_reply"]][:n]
    root_ids = {comment["comment_id"] for comment in roots if comment["comment_id"]}
    chosen_roots = {id(comment) for comment in roots}
    by_id = {comment["comment_id"]: comment for comment in comments if comment["comment_id"]}

    def thread_of(comment):
        seen = set()
        current = comment
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if current.get("root_comment_id") in root_ids:
                return current["root_comment_id"]
            if not current["is_reply"]:
                return current["comment_id"]
            current = by_id.get(current.get("parent_comment_id"))
        return None

    per_root = {}
    selected = []
    for comment in comments:
        if not comment["is_reply"]:
            if id(comment) in chosen_roots:
                selected.append(comment)
            continue
        thread = thread_of(comment)
        if thread is None and not comment.get("parent_comment_id") and not comment.get("root_comment_id"):
            selected.append(comment)
        elif thread in root_ids and per_root.get(thread, 0) < REPLIES_PER_ROOT:
            per_root[thread] = per_root.get(thread, 0) + 1
            selected.append(comment)
    return selected


# Tìm và bấm nút tải thêm bình luận; trả True nếu bấm được, ngược lại trả False.
async def click_more(page: Page, pattern):
    buttons = page.locator('[role="button"], a').filter(has_text=pattern)
    for index in range(min(await buttons.count(), 5)):
        button = buttons.nth(index)
        try:
            if await button.is_visible():
                await button.click(timeout=2500)
                return True
        except Exception:
            continue
    return False


async def click_in_page(page: Page, pattern, limit):
    return await page.evaluate(CLICK_BUTTONS_JS, [pattern.pattern, limit])


async def expand_replies(page: Page):
    """Mở mọi nhánh reply đang có trên trang; trả số nút đã bấm."""
    return await click_in_page(page, REPLY_MORE_RE, 30)


async def load_more_comments(page: Page):
    if await click_more(page, COMMENT_MORE_RE):
        return True
    return bool(await click_in_page(page, COMMENT_MORE_RE, 1))


# Mở permalink trong tab phụ, đọc n bình luận gốc kèm reply của chúng.
# Giữ kết quả đã đọc nếu có lỗi và luôn đóng tab phụ sau khi xử lý.
async def scrape_top_comments(context: BrowserContext, url: str, n: int):
    """Lấy n bình luận gốc và reply của chúng; lỗi về sau không xóa kết quả đã đọc."""
    if n <= 0:
        return []

    post_url = canonical_post_url(url)
    if not post_url:
        print("  Bỏ qua bình luận: chưa có permalink bài viết hợp lệ.")
        return []

    page = await context.new_page()
    collected_comments = {}

    try:
        await open_page(page, post_url)
        await human_pause(2.5, 4)
        idle_turns = 0
        incomplete_turns = 0
        maximum_turns = max(8, n // 5 + 8)

        for turn in range(maximum_turns):
            if is_login_url(page.url):
                print("  Facebook yêu cầu đăng nhập/xác minh khi lấy bình luận.")
                break

            await expand_see_more(page)
            opened = await expand_replies(page)
            if opened:
                await human_pause(1, 2)
                # Các reply vừa tải cũng có thể chứa nút mở nội dung dài.
                await expand_see_more(page)
            previous_count = len(collected_comments)
            raw_comments = await page.evaluate(EXTRACT_COMMENTS_JS)
            merge_comments(collected_comments, raw_comments)
            current = list(collected_comments.values())
            roots = root_count(current)
            incomplete = any(is_collapsed_text(comment["text"]) for comment in select_threads(current, n))
            incomplete_turns = incomplete_turns + 1 if incomplete else 0
            if incomplete:
                print("  Một số bình luận chưa mở hết nội dung; sẽ thử mở lại ở lượt đọc tiếp theo.", flush=True)

            print(f"  Bình luận đã đọc: {min(roots, n)}/{n} bình luận gốc, "
                  f"{len(current) - roots} phản hồi", flush=True)
            if roots >= n and not opened and (not incomplete or incomplete_turns >= 3):
                break

            # Bấm mở reply nhưng không có bản ghi mới (nút không biến mất) vẫn tính là lượt rỗng.
            if len(collected_comments) == previous_count:
                idle_turns += 1
            else:
                idle_turns = 0

            if idle_turns >= 3:
                break

            if roots < n:
                loaded_more = await load_more_comments(page)
                if not loaded_more:
                    await page.evaluate(SCROLL_COMMENTS_JS)
            await human_pause(1, 2)

        roots = root_count(collected_comments.values())
        if not collected_comments:
            print("  Chưa đọc được bình luận trên trang bài viết; kiểm tra quyền xem hoặc phần bình luận trên Chrome.")
        elif roots < n:
            print(f"  Đã lấy {roots}/{n} bình luận gốc; giao diện chưa tải thêm bình luận.")

    except Exception as error:
        log.warning("Lỗi đọc bình luận tại %s", post_url, exc_info=True)
        print(f"  Lỗi đọc bình luận ({type(error).__name__}); giữ {len(collected_comments)} bình luận đã đọc.")
    finally:
        await page.close()

    return select_threads(normalize_comments(list(collected_comments.values())), n)
