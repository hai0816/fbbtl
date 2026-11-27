"""Hiệu chỉnh ngưỡng bằng dữ liệu thực nghiệm.

Nhãn thật (ground truth) lấy theo thứ tự ưu tiên:
  1. Kết quả duyệt thủ công: CONFIRMED_SCAM = 1, FALSE_POSITIVE = 0 (bảng risk_assessments).
  2. Nhãn có sẵn trong dataset đã nhập (posts.metadata.label).
Với mỗi ngưỡng t, bài có risk_score ≥ t được coi là "gắn cờ" và tính precision, recall, F1, FPR.
Ngưỡng gợi ý: medium = ngưỡng có recall ≥ 0,9 cao nhất (sàng lọc rộng), high = ngưỡng có F1 lớn nhất
(ưu tiên kiểm tra trước). Kết quả chỉ có giá trị trên tập đã gán nhãn, cần đủ mẫu mới đáng tin.
"""
import json


def labeled_scores(db):
    rows = db.execute("SELECT r.post_id, r.risk_score, r.review_status, json_extract(p.metadata,'$.label') "
                      "FROM risk_assessments r JOIN posts p ON p.id=r.post_id").fetchall()
    samples = []
    for post_id, score, review, label in rows:
        truth = {"CONFIRMED_SCAM": 1, "FALSE_POSITIVE": 0}.get(review)
        source = "review"
        if truth is None and label is not None:
            truth, source = int(label), "dataset"
        if truth is not None:
            samples.append({"post_id": post_id, "score": score, "label": truth, "source": source})
    return samples


def metrics_at(samples, threshold):
    tp = sum(1 for item in samples if item["score"] >= threshold and item["label"] == 1)
    fp = sum(1 for item in samples if item["score"] >= threshold and item["label"] == 0)
    fn = sum(1 for item in samples if item["score"] < threshold and item["label"] == 1)
    tn = sum(1 for item in samples if item["score"] < threshold and item["label"] == 0)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3), "fpr": round(fp / (fp + tn), 3) if fp + tn else 0.0}


def evaluate(db, settings, step=5):
    samples = labeled_scores(db)
    positives = sum(item["label"] for item in samples)
    result = {"samples": len(samples), "positives": positives, "negatives": len(samples) - positives,
              "sources": {key: sum(1 for item in samples if item["source"] == key) for key in ("review", "dataset")},
              "current": settings["risk_thresholds"], "curve": [], "suggested": None, "at_current": {}}
    if not samples or positives in (0, len(samples)):
        result["note"] = "Cần cả mẫu lừa đảo và mẫu bình thường đã gán nhãn để hiệu chỉnh ngưỡng."
        return result
    curve = [metrics_at(samples, threshold) for threshold in range(step, 100, step)]
    result["curve"] = curve
    best_f1 = max(curve, key=lambda item: (item["f1"], item["precision"], item["threshold"]))
    recall_ok = [item for item in curve if item["recall"] >= 0.9 and item["threshold"] < best_f1["threshold"]]
    medium = max(recall_ok, key=lambda item: item["threshold"])["threshold"] if recall_ok else \
        max(step, best_f1["threshold"] - 2 * step)
    result["suggested"] = {"medium": medium, "high": best_f1["threshold"]}
    result["at_current"] = {name: metrics_at(samples, value) for name, value in settings["risk_thresholds"].items()}
    result["misclassified_at_high"] = [
        {"post_id": item["post_id"], "score": item["score"], "label": item["label"]} for item in samples
        if (item["score"] >= settings["risk_thresholds"]["high"]) != bool(item["label"])][:30]
    if len(samples) < 30:
        result["note"] = f"Chỉ có {len(samples)} mẫu có nhãn; ngưỡng gợi ý mang tính tham khảo."
    return result


def as_text(result):
    lines = [f"Mẫu có nhãn: {result['samples']} (lừa đảo {result['positives']}, bình thường {result['negatives']})"]
    if result.get("note"):
        lines.append("Lưu ý: " + result["note"])
    if result["curve"]:
        lines.append("Ngưỡng  Precision  Recall  F1     FPR")
        for item in result["curve"]:
            lines.append(f"{item['threshold']:>6}  {item['precision']:>9}  {item['recall']:>6}  {item['f1']:<5}  {item['fpr']}")
        lines.append("Ngưỡng hiện tại: " + json.dumps(result["current"]) +
                     " → gợi ý: " + json.dumps(result["suggested"]))
    return "\n".join(lines)
