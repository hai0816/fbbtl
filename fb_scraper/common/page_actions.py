"""Thao tác dùng chung trên một tab Facebook: mở trang, chờ ngẫu nhiên, mở nội dung bị thu gọn."""
import asyncio
import random

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from fb_scraper.common.content import EXPAND_LABEL_RE, EXPAND_LABEL_SOURCE
from fb_scraper.common.logs import get_logger

log = get_logger(__name__)
NAVIGATION_TIMEOUT = 45000
READY_TIMEOUT = 20000
# Vùng nội dung chính, hoặc form đăng nhập để không phải chờ hết giờ khi phiên đã hết hạn.
READY_SELECTOR = '[role="main"], #login_form, form[action*="login"]'


async def open_page(page: Page, url, *, timeout=NAVIGATION_TIMEOUT, ready_timeout=READY_TIMEOUT,
                    ready_selector=READY_SELECTOR, attempts=2):
    """Mở URL Facebook mà không phụ thuộc sự kiện DOMContentLoaded.

    Facebook là ứng dụng nặng; tab thu thập thường chạy nền nên Chrome giảm tốc và
    DOMContentLoaded có thể đến rất muộn dù nội dung đã hiển thị, làm goto() hết giờ.
    Ở đây chỉ chờ 'commit' (đã nhận phản hồi, URL đã đổi), rồi chờ vùng nội dung chính.
    Không thấy vùng nội dung vẫn trả về để bên gọi tự nhận biết trang đăng nhập/lỗi.
    """
    response = None
    for attempt in range(1, attempts + 1):
        try:
            response = await page.goto(url, wait_until="commit", timeout=timeout)
            break
        except PlaywrightTimeoutError:
            if attempt == attempts:
                raise
            log.warning("Hết thời gian mở %s (lần %s); thử lại", url, attempt)
            print(f"  Facebook phản hồi chậm; thử mở lại {url}", flush=True)
    try:
        await page.wait_for_selector(ready_selector, state="attached", timeout=ready_timeout)
    except PlaywrightTimeoutError:
        log.warning("Chưa thấy vùng nội dung trên %s sau %s ms", url, ready_timeout)
    return response


async def human_pause(a=1.5, b=4.0):
    """Chờ ngẫu nhiên từ a đến b giây giữa các thao tác, không chặn vòng lặp asyncio."""
    await asyncio.sleep(random.uniform(a, b))


async def expand_see_more(page: Page):
    """Mở các đoạn bị thu gọn, kể cả span/button/a và DOM thay đổi sau mỗi lần bấm."""
    clicked = 0
    for _round in range(5):
        # Chụp handle để việc nút bị xóa không làm nth(index) bỏ qua nút kế tiếp.
        buttons = await page.get_by_text(EXPAND_LABEL_RE).element_handles()
        expanded = 0
        for button in buttons:
            try:
                in_content = await button.evaluate("""el =>
                    !document.querySelector('[role="main"], [role="dialog"]') ||
                    Boolean(el.closest('[role="main"], [role="dialog"]'))""")
                if not in_content or not await button.is_visible():
                    continue
                await button.click(timeout=1500)
                clicked += 1
                expanded += 1
                try:
                    await page.wait_for_function(
                        "([el, pattern]) => !el.isConnected || el.getAttribute('aria-expanded') === 'true' || "
                        "!new RegExp(pattern, 'i').test(el.innerText || el.textContent || '')",
                        arg=[button, EXPAND_LABEL_SOURCE], timeout=1200,
                    )
                except Exception:
                    pass
            except Exception:
                # Nút có thể vừa biến mất do một nút khác mở toàn bộ đoạn văn.
                continue
        if not expanded:
            break
    return clicked
