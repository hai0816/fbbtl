"""Làm sạch dữ liệu bài viết trong bộ nhớ; không mở Chrome hay ghi file."""
import re
from urllib.parse import parse_qs, urlparse

from fb_scraper.common.numbers import parse_count
from fb_scraper.common.times import parse_post_time
from fb_scraper.common.urls import canonical_post_url, make_post_id
from fb_scraper.common.models import Post

# Ký tự ẩn Facebook chèn vào chữ bị xáo trộn (chống đọc tự động).
HIDDEN_CHAR = "\u034f"
# Ví dụ aria-label: "Thích: 14 người", "Yêu thích: 1 người", "Like: 3 people".
REACTION_LABEL_RE = re.compile(r"^[^:,]{1,20}:\s*(\d[\d.,]*\s*(?:[KkMmNn]|Tr)?)\s*(?:người|people|person)$", re.I)
# Ví dụ aria-label: "Hành động đối với bài viết này của Việt Anh".
AUTHOR_LABEL_RE = re.compile(r"^(?:Hành động đối với bài viết này của|Actions for this post by)\s+(.+)$", re.I)


# Chọn liên kết bài viết phù hợp nhất trong danh sách; trả về None nếu không có permalink.
def find_post_link(links):
    """Chọn permalink tốt nhất: link bài trước link bình luận, rồi thời gian đăng."""
    valid_links = []
    for link in links:
        canonical_url = canonical_post_url(link.get("href", ""))
        if canonical_url:
            valid_links.append(link)

    if not valid_links:
        return None

    def rank(link):
        canonical_url = canonical_post_url(link["href"])
        query_parameters = parse_qs(urlparse(link["href"]).query)

        comment_priority = 0
        if "comment_id" in query_parameters or "reply_comment_id" in query_parameters:
            comment_priority = 1

        time_priority = 1
        if parse_relative_time(link.get("text", "")):
            time_priority = 0

        permalink_priority = 1
        if re.search(r"/(?:posts|permalink)/|story_fbid=", canonical_url):
            permalink_priority = 0

        # Python so sánh tuple từ trái sang phải; giá trị 0 được ưu tiên trước 1.
        return (comment_priority, time_priority, permalink_priority)

    best_link = valid_links[0]
    best_rank = rank(best_link)

    for link in valid_links[1:]:
        current_rank = rank(link)
        if current_rank < best_rank:
            best_link = link
            best_rank = current_rank

    return best_link


def parse_relative_time(raw: str):
    """Đổi mốc thời gian Facebook ('2 giờ', 'Hôm qua lúc 10:05', '28 Tháng 8, 2026 lúc 17:35'...) thành ISO UTC.

    Tên hàm giữ như cũ để không đổi nơi gọi; phần đọc nằm ở common/times.py (hỗ trợ cả ngày tuyệt đối).
    """
    return parse_post_time(raw)


def parse_vietnamese_date(raw: str):
    """Tương thích ngược: ngày tuyệt đối tiếng Việt/tiếng Anh, có hoặc không có năm."""
    return parse_post_time(raw)


def find_posted_time(card_text: str, post_text: str):
    """Tìm mốc thời gian đọc được ở phần đầu khung bài (trước nội dung bài).

    Trả (chuỗi gốc, ISO UTC) hoặc ("", None). Chỉ xét phần trước nội dung để
    câu như "3 ngày nữa..." trong bài không bị nhận nhầm là thời gian đăng.
    """
    header = card_text
    content_start = card_text.find(post_text[:30]) if post_text else -1
    if content_start > 0:
        header = card_text[:content_start]

    for line in header.splitlines():
        line = line.strip()
        if not line or HIDDEN_CHAR in line:
            continue
        posted_at = parse_vietnamese_date(line) or parse_relative_time(line)
        if posted_at:
            return line, posted_at
    return "", None


INVISIBLE_RE = re.compile("[\u034f\u200b-\u200f\u2060\ufeff\u00ad]")
# Quảng cáo: nhãn "Được tài trợ"/"Sponsored" hoặc link đích có tham số chiến dịch trả phí của Meta.
SPONSORED_LABEL_RE = re.compile(r"^\s*(?:Được tài trợ|Sponsored|Quảng cáo|Paid partnership|Hợp tác có trả phí)\s*$", re.I)
AD_LINK_RE = re.compile(r"[?&](?:utm_medium=paid|utm_source=fb_ads?)(?:&|$)|_aem_|/ads/about|[?&]ad_id=", re.I)


def is_sponsored(raw):
    """Đánh dấu bài quảng cáo (vẫn được lưu: quảng cáo lừa đảo là đối tượng cần trinh sát)."""
    if any(SPONSORED_LABEL_RE.match(label or "") for label in raw.get("labels", [])):
        return True
    if any(SPONSORED_LABEL_RE.match(line) for line in (raw.get("stats_text") or "").splitlines()[:8]):
        return True
    return any(AD_LINK_RE.search(link.get("href", "")) for link in raw.get("links", []))


def author_from_labels(labels):
    """Lấy tên người đăng từ aria-label, kể cả "Người tham gia ẩn danh"."""
    for label in labels:
        match = AUTHOR_LABEL_RE.match(label.strip())
        if match:
            return match.group(1).strip()
    return ""


def reaction_total(labels):
    """Cộng các nhãn "Thích: 14 người", "Yêu thích: 1 người" thành tổng cảm xúc."""
    total = None
    for label in labels:
        match = REACTION_LABEL_RE.match(label.strip())
        if match:
            count = parse_count(match.group(1))
            if count is not None:
                total = (total or 0) + count
    return total


# Đọc số cảm xúc, bình luận và chia sẻ từ văn bản; trả tuple gồm ba số hoặc None.
def parse_counts(text: str):
    """Trả về (số cảm xúc, số bình luận, số chia sẻ); trường không thấy là None."""
    number_pattern = r"(\d[\d.,]*\s*(?:[KkMmNn]|Tr)?)"
    likes_match = re.search(r"(?:Tất cả cảm xúc|All reactions)[:\s]*" + number_pattern, text)
    comments_match = re.search(number_pattern + r"\s*(?:bình luận|comments?)", text, re.I)
    shares_match = re.search(number_pattern + r"\s*(?:lượt chia sẻ|shares?)", text, re.I)

    counts = []
    for match in (likes_match, comments_match, shares_match):
        if match:
            counts.append(parse_count(match.group(1)))
        else:
            counts.append(None)

    return tuple(counts)


def without_post_text(card_text: str, post_text: str):
    """Bỏ nội dung bài khỏi chữ của khung bài trước khi đọc số liệu.

    Nếu không, câu "tặng 100 bình luận đầu tiên" trong bài bị hiểu thành 100 bình luận.
    """
    if not post_text:
        return card_text
    if post_text in card_text:
        return card_text.replace(post_text, "\n")
    body_lines = {line.strip() for line in post_text.splitlines() if line.strip()}
    kept_lines = [line for line in card_text.splitlines() if line.strip() not in body_lines]
    return "\n".join(kept_lines)


def card_counts(card_text: str, post_text: str, labels):
    """aria-label có nhãn rõ ràng nên được ưu tiên; chữ hiển thị chỉ bù trường còn thiếu."""
    from_labels = parse_counts("\n".join(labels))
    from_card = parse_counts(without_post_text(card_text, post_text))
    counts = []
    for label_value, card_value in zip(from_labels, from_card):
        counts.append(label_value if label_value is not None else card_value)
    return tuple(counts)


# Chuẩn hóa dữ liệu DOM thành dictionary bài viết; bỏ nội dung ngắn dưới 20 ký tự.
def parse_article(raw: dict, source: str):
    """Đổi dữ liệu DOM thành bài viết; chưa mở tab, tải ảnh hay ghi database."""
    # Bỏ ký tự ẩn Facebook chèn để chống đọc tự động trước khi kiểm tra độ dài.
    text = INVISIBLE_RE.sub("", raw["text"]).strip()
    if len(text) < 20:
        return None

    post_link = find_post_link(raw["links"])
    post_url = ""
    posted_raw = ""

    if post_link:
        post_url = canonical_post_url(post_link["href"])
        query_parameters = parse_qs(urlparse(post_link["href"]).query)
        is_comment_link = False

        if "comment_id" in query_parameters or "reply_comment_id" in query_parameters:
            is_comment_link = True

        # Facebook có thể xáo trộn chữ của mốc thời gian và chèn ký tự ẩn U+034F
        # để chống đọc tự động; chuỗi đó không đọc được nên để trống.
        if not is_comment_link and HIDDEN_CHAR not in post_link["text"]:
            posted_raw = post_link["text"]

    card_text = raw.get("stats_text", text)
    labels = raw.get("labels", [])
    likes, comments, shares = card_counts(card_text, text, labels)
    if likes is None:
        likes = reaction_total(labels)

    posted_at = parse_relative_time(posted_raw)
    if not posted_at:
        posted_raw, posted_at = find_posted_time(card_text, text)

    image_urls = []
    image_meta = []
    seen_image_urls = set()
    for image in raw["imgs"]:
        image_url = image["src"]
        if "scontent" not in image_url:
            continue
        if image["w"] < 200 or image["h"] < 200:
            continue

        if image_url not in seen_image_urls:
            seen_image_urls.add(image_url)
            image_urls.append(image_url)
            image_meta.append({"src": image_url, "w": image["w"], "h": image["h"], "page": image.get("page", "")})

    external_urls = set()
    for link in raw["links"]:
        link_url = link["href"]
        is_redirect_link = "l.facebook.com/l.php" in link_url
        is_external_link = link_url.startswith("http") and "facebook.com" not in link_url

        if is_redirect_link or is_external_link:
            external_urls.add(link_url)

    author = author_from_labels(labels) or raw["author"].strip()
    record = {
        "post_id": make_post_id(post_url, text, author),
        "url": post_url,
        "source": source,
        "author": author,
        "is_sponsored": is_sponsored(raw),
        "text": text,
        "posted_raw": posted_raw,
        "posted_at": posted_at,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "links": sorted(external_urls),
        "images": image_urls,
    }
    record["author_url"] = raw.get("author_url", "")
    if "is_shared" in raw:
        record["is_shared"] = bool(raw["is_shared"])
    original_url = canonical_post_url(raw.get("original_post_url", ""))
    if original_url and original_url != post_url:
        record.update(is_shared=True, original_post_key=make_post_id(original_url, ""))
    record["media"] = [{"media_type": "video", "media_url": url, "position": index}
                       for index, url in enumerate(raw.get("videos", [])) if url.startswith(("http://", "https://"))]
    validated_post = Post.model_validate(record).model_dump(exclude_unset=True)
    # Chỉ dùng trong lượt thu thập (kích thước, link trang ảnh) để chọn ảnh lớn cho OCR; không lưu DB.
    validated_post["image_meta"] = image_meta
    return validated_post
