"""Tải ảnh đính kèm: thử lại khi lỗi mạng, ưu tiên ảnh độ phân giải cao cho OCR.

Ảnh trong feed thường là bản thu nhỏ (ví dụ 320×320); chữ trong ảnh quảng cáo/tuyển dụng nhỏ nên
OCR đọc sai. Khi bật OCR và ảnh nhỏ hơn FULL_SIZE_MIN_WIDTH, mở trang ảnh (/photo/?fbid=…) trong tab
phụ để lấy bản lớn mà Facebook hiển thị cho chính người dùng; không thành công thì dùng ảnh thu nhỏ.
"""
import asyncio
import hashlib
from urllib.parse import urlparse

from fb_scraper.common.logs import get_logger
from fb_scraper.common.urls import FACEBOOK_HOSTS
from fb_scraper.storage import database as storage

log = get_logger(__name__)
FULL_SIZE_MIN_WIDTH = 720
RETRY_DELAYS = (1.0, 3.0)
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
# Ảnh chính trong trình xem ảnh của Facebook; lấy ảnh lớn nhất nếu selector đổi.
LARGEST_IMAGE_JS = r"""() => {
  const preferred = document.querySelector('img[data-visualcompletion="media-vc-image"]');
  const images = preferred ? [preferred] : [...document.querySelectorAll('img[src*="scontent"]')];
  const best = images.map(i => ({src: i.currentSrc || i.src, w: i.naturalWidth, h: i.naturalHeight}))
    .filter(i => i.src.startsWith('http')).sort((a, b) => b.w * b.h - a.w * a.h)[0];
  return best || null;
}"""


def is_photo_page(url):
    parsed = urlparse(url or "")
    return parsed.hostname in FACEBOOK_HOSTS and ("/photo" in parsed.path or "/photos/" in parsed.path)


async def resolve_full_image(context, photo_url, current_width):
    """Trả URL ảnh lớn hơn ảnh hiện có, hoặc None."""
    page = None
    try:
        page = await context.new_page()
        await page.goto(photo_url, wait_until="domcontentloaded", timeout=20000)
        try:
            await page.wait_for_selector('img[data-visualcompletion="media-vc-image"]', timeout=8000)
        except Exception:
            pass
        best = await page.evaluate(LARGEST_IMAGE_JS)
        if best and (best.get("w") or 0) > (current_width or 0):
            return best["src"]
    except Exception:
        log.warning("Không mở được trang ảnh %s", photo_url, exc_info=True)
    finally:
        if page is not None:
            await page.close()
    return None


async def fetch_image(context, url, attempts=len(RETRY_DELAYS) + 1):
    """Tải ảnh, thử lại khi lỗi mạng/5xx. Trả (bytes, content_type) hoặc ném lỗi cuối cùng."""
    last_error = None
    for attempt in range(attempts):
        try:
            response = await context.request.get(url, timeout=20000)
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if response.ok:
                body = await response.body()
                if not content_type.startswith("image/") or len(body) < 512:
                    raise ValueError(f"không phải ảnh hợp lệ ({content_type or 'không rõ'}, {len(body)} byte)")
                return body, content_type
            last_error = RuntimeError(f"HTTP {response.status}")
            if response.status < 500 and response.status != 429:
                break  # 403/404: link hết hạn, thử lại không có tác dụng
        except ValueError:
            raise
        except Exception as error:
            last_error = error
        if attempt < attempts - 1:
            await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    raise last_error or RuntimeError("Không tải được ảnh")


async def download_images(context, post, limit=4, full_size=False):
    """Tải tối đa limit ảnh vào data/images; một ảnh lỗi không ngăn các ảnh còn lại."""
    downloaded_files = []
    meta = {item.get("src"): item for item in post.get("image_meta", [])}
    for index, image_url in enumerate(post.get("images", [])[:limit]):
        info = meta.get(image_url, {})
        source_url, upgraded = image_url, False
        if full_size and (info.get("w") or 0) < FULL_SIZE_MIN_WIDTH and is_photo_page(info.get("page")):
            better = await resolve_full_image(context, info["page"], info.get("w"))
            if better:
                source_url, upgraded = better, True
        try:
            try:
                body, content_type = await fetch_image(context, source_url)
            except Exception:
                if not upgraded:
                    raise
                body, content_type = await fetch_image(context, image_url)  # quay về ảnh thu nhỏ
                upgraded = False
            image_path = storage.IMG_DIR / f"{post['post_id']}_{index}{EXTENSIONS.get(content_type, '.jpg')}"
            image_path.write_bytes(body)
            downloaded_files.append(str(image_path))
            post.setdefault("media", []).append({
                "media_type": "gif" if content_type == "image/gif" else "image",
                "media_url": image_url, "file_path": str(image_path), "position": index,
                "file_hash": hashlib.sha256(body).hexdigest(),
                "metadata": {"full_size": upgraded, "content_type": content_type},
            })
            if upgraded:
                print(f"  Ảnh {index}: đã lấy bản độ phân giải cao cho OCR", flush=True)
        except Exception as error:
            log.warning("Không tải được ảnh %s của bài %s", index, post.get("post_id"), exc_info=True)
            print(f"  Không tải được ảnh {index}: {type(error).__name__}: {error}", flush=True)
    return downloaded_files
