"""OCR: đọc chữ trong ảnh bằng EasyOCR, hỗ trợ tiếng Việt và tiếng Anh.

Cải thiện độ chính xác cho ảnh quảng cáo/tuyển dụng (chữ nhỏ, nền màu):
- phóng to để cạnh dài ≈ 1600 px (tối đa ×3) thay vì luôn ×2;
- thêm bản xám tăng tương phản (CLAHE) và chọn kết quả có tổng độ tin cậy cao hơn;
- ghép các mảnh chữ thành dòng theo vị trí (trên xuống, trái sang phải) để câu không bị đảo thứ tự.
"""
import re

import cv2
import easyocr

URL_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s]*)?", re.I)
PHONE_RE = re.compile(r"(?:\+?84|0)(?:[\s.]?\d){8,10}")
TARGET_SIDE = 1600
MAX_SCALE = 3.0
GOOD_CONFIDENCE = 0.75


def order_lines(detections, min_conf):
    """detections: [(box, text, conf)] → danh sách dòng chữ theo thứ tự đọc, kèm độ tin cậy trung bình."""
    items = []
    for box, text, conf in detections:
        if conf < min_conf or not str(text).strip():
            continue
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append({"text": str(text).strip(), "conf": float(conf), "top": min(ys), "bottom": max(ys), "left": min(xs)})
    if not items:
        return [], 0.0
    heights = sorted(item["bottom"] - item["top"] for item in items)
    tolerance = max(4.0, heights[len(heights) // 2] * 0.5)
    lines = []
    for item in sorted(items, key=lambda entry: ((entry["top"] + entry["bottom"]) / 2, entry["left"])):
        center = (item["top"] + item["bottom"]) / 2
        if lines and abs(lines[-1]["center"] - center) <= tolerance:
            lines[-1]["items"].append(item)
        else:
            lines.append({"center": center, "items": [item]})
    texts = [" ".join(entry["text"] for entry in sorted(line["items"], key=lambda entry: entry["left"])) for line in lines]
    return texts, sum(item["conf"] for item in items) / len(items)


class OCREngine:
    def __init__(self, langs=("vi", "en"), gpu=False, min_conf=0.35):
        # Tạo bộ đọc một lần; lần đầu EasyOCR có thể cần tải model.
        self.reader = easyocr.Reader(list(langs), gpu=gpu)
        self.min_conf = min_conf

    @staticmethod
    def _preprocess(img):
        """Trả về các biến thể ảnh để thử: ảnh màu đã phóng to và bản xám tăng tương phản."""
        height, width = img.shape[:2]
        scale = min(MAX_SCALE, TARGET_SIDE / max(height, width))
        if scale > 1.05:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        return [img, contrast]

    def read(self, path: str) -> dict:
        """Đọc một ảnh và trả về nội dung, URL, số điện thoại, độ tin cậy trung bình."""
        image = cv2.imread(path)
        if image is None:
            return {"text": "", "urls": [], "phones": [], "confidence": 0.0}

        best_lines, best_conf, best_score = [], 0.0, -1.0
        for variant in self._preprocess(image):
            detections = self.reader.readtext(variant, detail=1, paragraph=False)
            lines, confidence = order_lines(detections, self.min_conf)
            score = confidence * sum(len(line) for line in lines)  # nhiều chữ đọc chắc chắn hơn = tốt hơn
            if score > best_score:
                best_lines, best_conf, best_score = lines, confidence, score
            if best_conf >= GOOD_CONFIDENCE and sum(len(line) for line in best_lines) >= 20:
                break  # lần đọc đầu đã tốt; bỏ qua bản tăng tương phản để tiết kiệm thời gian

        text = "\n".join(best_lines)
        phones = {re.sub(r"[\s.]", "", phone.strip()) for phone in PHONE_RE.findall(text)}
        return {"text": text, "urls": sorted(set(URL_RE.findall(text))), "phones": sorted(phones),
                "confidence": round(best_conf, 3)}
