"""Trinh sát và phát hiện dấu hiệu lừa đảo tuyển dụng/đầu tư (rule-based + phân tích dữ liệu).

Luồng xử lý: posts/media/comments (SQLite) → text/features (tiền xử lý) → rules (so khớp)
→ scoring (điểm rủi ro 0–100) → similarity/analytics (phân tích tập dữ liệu).
Điểm rủi ro chỉ là MỨC ƯU TIÊN KIỂM TRA, không phải xác suất một bài viết là lừa đảo.
"""
