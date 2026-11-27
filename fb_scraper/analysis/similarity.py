"""Nội dung tương tự: TF-IDF + Cosine Similarity + gom cụm theo ngưỡng (union-find).

Cài đặt thuần Python (không cần scikit-learn):
- Token: âm tiết đã fold (bỏ dấu, mở rộng viết tắt: "CTV" ≡ "cộng tác viên"), bỏ stopword;
  đặc trưng gồm unigram + bigram để giữ cụm như "nhiem vu", "hoa hong".
- tf = 1 + log(số lần xuất hiện)  (sublinear), idf = ln((1 + N) / (1 + df)) + 1  (smooth),
  vector chuẩn hóa L2 ⇒ cosine = tích vô hướng.
- Hai bài có cosine ≥ ngưỡng được nối cạnh; mỗi thành phần liên thông là một cụm.
Một cụm chỉ cho biết NỘI DUNG giống nhau; không đủ để kết luận cùng người đăng/tổ chức.
"""
from collections import Counter, defaultdict
import math

from fb_scraper.analysis.text import content_tokens, ngrams


def vectorize(documents):
    """documents: {id: folded_text} → ({id: {term: weight}}, idf)."""
    term_counts = {}
    document_frequency = Counter()
    for key, text in documents.items():
        words = content_tokens(text, folded=True)
        counts = Counter(ngrams(words, 1, 2))
        term_counts[key] = counts
        document_frequency.update(counts.keys())
    total = len(documents)
    idf = {term: math.log((1 + total) / (1 + count)) + 1 for term, count in document_frequency.items()}
    vectors = {}
    for key, counts in term_counts.items():
        weights = {term: (1 + math.log(count)) * idf[term] for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
        vectors[key] = {term: value / norm for term, value in weights.items()}
    return vectors, idf


def cosine(left, right):
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def similar_pairs(vectors, threshold):
    """So sánh từng cặp qua chỉ mục ngược (chỉ cặp có chung ít nhất một term)."""
    index = defaultdict(list)
    for key, vector in vectors.items():
        for term in vector:
            index[term].append(key)
    pairs = []
    keys = sorted(vectors)
    for position, key in enumerate(keys):
        candidates = {other for term in vectors[key] for other in index[term] if other > key}
        for other in sorted(candidates):
            score = cosine(vectors[key], vectors[other])
            if score >= threshold:
                pairs.append({"a": key, "b": other, "score": round(score, 4)})
    return sorted(pairs, key=lambda item: -item["score"])


def clusters(keys, pairs):
    parent = {key: key for key in keys}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for pair in pairs:
        left, right = find(pair["a"]), find(pair["b"])
        if left != right:
            parent[max(left, right)] = min(left, right)
    groups = defaultdict(list)
    for key in keys:
        groups[find(key)].append(key)
    return [sorted(members) for members in groups.values() if len(members) > 1]


def top_terms(vectors, members, limit=8):
    """Cụm từ đặc trưng của một cụm: tổng trọng số TF-IDF, ưu tiên bigram."""
    totals = Counter()
    for key in members:
        for term, value in vectors[key].items():
            totals[term] += value * (1.3 if " " in term else 1.0)
    return [term for term, _ in totals.most_common(limit)]


def shared_values(features_by_id, members, field):
    counts = Counter(value for key in members for value in set(features_by_id.get(key, {}).get(field, [])))
    return [value for value, count in counts.most_common() if count > 1]


def analyze(documents, features_by_id=None, threshold=0.5):
    """Trả về cặp tương tự và cụm, kèm số lượng bài, cụm từ lặp lại, liên kết/SĐT dùng chung trong cụm."""
    features_by_id = features_by_id or {}
    documents = {key: text for key, text in documents.items() if text and text.strip()}
    vectors, _ = vectorize(documents)
    pairs = similar_pairs(vectors, threshold)
    best = defaultdict(dict)
    for pair in pairs:
        best[pair["a"]][pair["b"]] = pair["score"]
        best[pair["b"]][pair["a"]] = pair["score"]
    result = []
    for number, members in enumerate(sorted(clusters(sorted(vectors), pairs), key=lambda group: (-len(group), group)), 1):
        scores = [best[a][b] for a in members for b in members if a < b and b in best[a]]
        result.append({"cluster_id": number, "size": len(members), "post_ids": members,
                       "max_similarity": max(scores, default=0), "mean_similarity": round(sum(scores) / len(scores), 4)
                       if scores else 0, "top_terms": top_terms(vectors, members),
                       "shared_domains": shared_values(features_by_id, members, "domains"),
                       "shared_phones": shared_values(features_by_id, members, "phones")})
    return {"threshold": threshold, "documents": len(vectors), "pairs": pairs, "clusters": result}
