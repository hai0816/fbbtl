# Hệ thống trinh sát & phát hiện dấu hiệu lừa đảo tuyển dụng, đầu tư

Đề tài: *Xây dựng hệ thống trinh sát và phát hiện dấu hiệu lừa đảo tuyển dụng, đầu tư tài chính trên
mạng xã hội Facebook bằng phương pháp Rule-based kết hợp phân tích dữ liệu.*

Module mới nằm trong `fb_scraper/analysis/`. Module này **mở rộng** collector sẵn có (Playwright + Chrome thật,
SQLite, OCR) chứ không thay thế. Mọi phần thu thập cũ giữ nguyên.

## 1. Kiến trúc

```text
Thu thập (đã có)                 Phân tích (mới, fb_scraper/analysis)
──────────────────               ─────────────────────────────────────────────────────────
posts/  entities/  search/  ──►  SQLite (posts, comments, media.ocr_text)
web/worker (giao diện)            │
analysis/monitor (định kỳ)        ├─ text.py / features.py   Tiền xử lý: NFC, bỏ dấu, viết tắt, tách câu,
analysis/importer (CSV/JSON/DB)   │                          URL/SĐT/Zalo, thu nhập đ/ngày, % lợi nhuận/tháng
                                  ├─ rules.py               Rule engine: keyword/phrase/regex, AND/OR, cửa sổ câu,
                                  │                          phủ định, câu/bài cảnh báo
                                  ├─ scoring.py             Điểm 0–100, chống cộng trùng, giải thích
                                  ├─ pipeline.py            Chạy tăng dần → post_analysis, risk_assessments
                                  ├─ similarity.py          TF-IDF + cosine + gom cụm
                                  ├─ expansion.py           Đề xuất từ khóa mới (cần người dùng duyệt)
                                  ├─ evaluation.py          Precision/Recall/F1 theo ngưỡng → hiệu chỉnh
                                  └─ analytics.py           Thống kê tổng quan, rule, thời gian, nội dung lặp
Giao diện: http://127.0.0.1:8765/analysis      CLI: python main.py analyze …   |   python main.py monitor
```

Sau mỗi lượt thu thập trên giao diện, worker **tự chấm điểm** các bài mới. Lệnh `posts`/`entity` chạy riêng
từ CLI thì cần chạy thêm `analyze run`.

## 2. Thu thập có định hướng

| Phương pháp | Cài đặt |
|---|---|
| **Keyword-based** | Bảng `keywords` gồm 14 từ khóa mặc định (7 tuyển dụng, 7 đầu tư). Có thể thêm/xóa/tắt trên giao diện hoặc bằng `analyze keywords`. Nút *Thu thập theo từ khóa* dùng lại worker sẵn có (tìm `facebook.com/search/posts`). |
| **Source-based** | Bảng `sources` có thêm `monitor_enabled`, `collection_status`, `last_error`; `last_checked_at` là thời điểm thu thập gần nhất. Lệnh `main.py monitor --once` thu thập các nguồn đến hạn (có thể đặt lịch trong Windows Task Scheduler), hoặc dùng `--loop`. |
| **Query Expansion** | Trích cụm từ (tách từ theo từ điển + n-gram) từ câu làm bằng chứng của bài đáng ngờ. Điểm = support × idf × (1 + signal) × hệ số độ dài. Đề xuất được lưu ở trạng thái `PENDING` và **chỉ thành từ khóa khi người dùng chấp nhận**. |
| **Import Dataset** | CSV / JSON / JSONL / SQLite, tự nhận diện tên cột. Bỏ dòng trùng trong tệp, cập nhật bài trùng `post_id`, báo lỗi theo từng dòng, chuẩn hóa thời gian (epoch, ISO, dd/mm/yyyy; giờ không có múi giờ được hiểu là UTC+7). Nguồn được ghi là `source_type='dataset'`, `metadata.collection_method='import'`, kèm nhật ký `import_batches`. |

**Giới hạn đạo đức/pháp lý:** hệ thống không giả định Facebook có API tìm kiếm toàn bộ bài công khai. Collector
chỉ đọc nội dung công khai hiển thị với phiên đăng nhập của người dùng; nhóm chỉ được thu thập khi xác định là
*Công khai*. Hệ thống không vượt CAPTCHA, cơ chế đăng nhập hay giới hạn truy cập. Nguồn bị chặn được ghi
`collection_status='error'`. Khi không có quyền thu thập trực tiếp, dùng chức năng Import Dataset.

## 3. Tiền xử lý

Dữ liệu gốc (`posts.content`, `media.ocr_text`, `comments`) **không bị sửa**. Kết quả tiền xử lý được lưu ở `post_analysis`:

- `normalized_text`: Unicode NFC, bỏ ký tự vô hình, gộp khoảng trắng.
- `combined_text`: nội dung bài cộng với chữ OCR (gắn nhãn `[OCR]`).
- `folded_text`: chữ thường, bỏ dấu, mở rộng viết tắt (CTV → cộng tác viên, ko/k → không, ck → chuyển khoản),
  gộp chữ bị tách để né bộ lọc (`N.Ạ.P T.I.Ề.N` → `nap tien`).
- `features`: URL, tên miền, SĐT, kênh liên hệ (Zalo/Telegram/inbox), thu nhập quy về đ/ngày, % lợi nhuận quy về %/tháng.
- `text_hash` / `duplicate_of`: phát hiện bài trùng nội dung tuyệt đối. Bài gần giống được xử lý ở phần TF-IDF.
- `comment_context`: bình luận được phân tích **riêng** (số bình luận nhắc tới lừa đảo, bình luận kiểu "đã rút
  được tiền", lời mời inbox) và chỉ dùng làm ngữ cảnh cho người duyệt, **không cộng vào điểm**.

**Vấn đề nhập nhằng khi bỏ dấu:** "lãi/lại", "sàn/sản", "mất tiền/mặt tiền", "đừng/dùng" trùng nhau khi bỏ dấu.
Engine giữ hai dạng văn bản có độ dài bằng nhau: dạng có dấu (`lower_form`) và dạng không dấu (`fold`). Các từ
dễ nhập nhằng (tiền tố `acc:` trong rule) được so khớp **có dấu** nếu câu viết có dấu, và chuyển sang so khớp
**không dấu** nếu bài cố tình viết không dấu.

## 4. Rule-based Detection Engine

Mỗi rule (`default_rules.py`, lưu trong bảng `detection_rules`) gồm: Rule ID, tên, nhóm (JOB / INVESTMENT /
COMMON / COMBO), mô tả, điều kiện, trọng số, trạng thái bật/tắt, mức ưu tiên (1–3) và **nhóm tương đương**
(`dedup_group`).

| ID | Rule | Nhóm tương đương | W |
|---|---|---|---|
| J01 / J01B | Thu nhập cao bất thường (cụm từ / số tiền ≥ 500.000đ/ngày) | job_income | 12 / 14 |
| J02 | Không cần kinh nghiệm nhưng thu nhập lớn | job_easy | 10 |
| J03 | Chuyển tiền/đóng phí trước khi nhận việc | job_upfront_payment | 28 |
| J05 | Đặt cọc để giữ việc | job_upfront_payment | 25 |
| J04 | Nạp tiền để làm nhiệm vụ | job_task_deposit | 28 |
| J06 | Nạp tiền để nhận hoa hồng/hoàn tiền | payment_for_return | 25 |
| J07 | Áp lực chuyển tiền trong thời gian ngắn | urgency_payment | 12 |
| J08 | Việc làm dạng nhiệm vụ online | task_job | 8 |
| I01 / I01B | Lợi nhuận cao bất thường (cụm từ / ≥ 10%/tháng) | inv_high_return | 15 / 18 |
| I02 | Cam kết không rủi ro | inv_no_risk | 20 |
| I03 | Nạp tiền để nhận lợi nhuận | payment_for_return | 18 |
| I04 | Đóng phí/thuế để rút tiền | withdraw_fee | 30 |
| I05 | Áp lực đầu tư ngay | urgency_invest | 8 |
| I06 | Lợi nhuận bảo đảm/cố định | inv_guaranteed | 15 |
| I08 | Kênh đầu tư thiếu minh bạch | inv_platform | 6 |
| C01 | Tự khẳng định uy tín, "không lừa đảo" | trust_claim | 6 |
| C02 | Dẫn sang kênh liên hệ riêng | offplatform_contact | 4 |
| C04 | Dịch vụ "lấy lại tiền bị lừa" | recovery_scam | 30 |
| X01 | *Kết hợp:* mồi nhử + yêu cầu chuyển tiền | combo_lure_payment | 15 |
| I07 | *Kết hợp:* chuyển hướng tuyển dụng → đầu tư | job_to_invest | 15 |

**Cú pháp điều kiện:** `conditions` là danh sách mệnh đề nối với nhau bằng **AND**. Mỗi mệnh đề là một trong các dạng:
`{"any": [...]}` (**OR** giữa các phrase, `re:` cho regex, `acc:` cho từ cần so khớp có dấu),
`{"feature": ..., "setting": ...}` (so ngưỡng đặc trưng số) hoặc `{"groups_any": [...]}` (rule kết hợp).
`window` quy định số câu liên tiếp mà các mệnh đề phải cùng nằm trong đó; `exclude` liệt kê cụm loại trừ.
Có thể thêm rule riêng bằng `analyze rules --import rules.json`.

**Phủ định & ngữ cảnh:**
1. *Phủ định cục bộ:* với rule `negatable`, lần khớp đứng sau "không / đừng / không nên / không yêu cầu /
   nếu ai…" trong **cùng vế câu**, hoặc đứng trước "miễn phí", sẽ bị bỏ qua. Ví dụ: "Không thu phí hồ sơ" không kích hoạt J03.
2. *Câu cảnh báo:* câu chứa từ cảnh báo ("thủ đoạn", "lừa đảo", "tỉnh táo"…) không tạo bằng chứng cho rule
   `negatable`. "Không lừa đảo" hay "nói không với lừa đảo" được coi là lời **tự khẳng định** (C01), không phải cảnh báo.
3. *Bài cảnh báo:* nếu bài có ≥ 2 loại từ cảnh báo và số này lớn hơn số lời kêu gọi liên hệ, bài được phân loại
   `WARNING_OR_AWARENESS` và điểm mọi rule bị nhân hệ số 0,3 (cấu hình được). Riêng C04 không bị giảm điểm.

Ví dụ yêu cầu trong đề: *"Không nên nạp tiền trước khi nhận việc. Đây là thủ đoạn lừa đảo…"* → 0 điểm,
phân loại "Bài cảnh báo" (có test `test_warning_post_is_not_flagged_for_mentioning_deposit`).

Mỗi rule kích hoạt trả về: `rule_id`, trọng số, điểm thực cộng, lý do (mô tả rule) và các **đoạn văn bản khớp**,
có gắn nhãn nguồn (bài / OCR / đặc trưng / kết hợp) và đánh dấu cụm từ khớp.

## 5. Chấm điểm rủi ro

```text
e_r   = w_r × m_r            m_r = 0,3 nếu bài cảnh báo hoặc mọi bằng chứng nằm trong câu cảnh báo; ngược lại 1
G_g   = max_{r ∈ g} e_r       (rule tương đương chỉ tính một lần)
score = min(100, round(Σ_g G_g))      rule COMBO được xét sau trên tập nhóm đã kích hoạt
level = LOW < 25 ≤ MEDIUM < 60 ≤ HIGH (ngưỡng cấu hình trong analysis_settings)
```

- Reaction, share và số bình luận **không** tham gia công thức.
- Điểm là **mức ưu tiên kiểm tra thủ công, không phải xác suất** bài viết là lừa đảo. Phần giải thích của mỗi bài đều ghi rõ điều này.
- Kết quả được lưu ở `risk_assessments`, ví dụ:
  `{"post_id": …, "category": "JOB_SCAM_INDICATORS", "risk_score": 75, "risk_level": "HIGH", "matched_rules": ["J04","J06","X01"], "review_status": "PENDING"}`.
- **Hiệu chỉnh ngưỡng:** duyệt bài (`CONFIRMED_SCAM` / `FALSE_POSITIVE`) hoặc nhập dataset có cột `label`, rồi chạy
  `analyze evaluate` (hoặc mở tab *Rule & ngưỡng*). Hệ thống tính precision/recall/F1/FPR theo từng ngưỡng và gợi ý
  ngưỡng mới (`--apply` để lưu).

## 6. Nội dung tương tự

TF-IDF thuần Python: âm tiết đã bỏ dấu, unigram + bigram, tf dạng sublinear, idf có làm trơn, chuẩn hóa L2.
Tính cosine qua chỉ mục ngược, sau đó gom cụm bằng union-find theo ngưỡng (mặc định 0,5). Ví dụ
"Tuyển CTV online, làm nhiệm vụ nhận hoa hồng" và "Tuyển cộng tác viên làm online, hoàn thành nhiệm vụ để nhận
hoa hồng" có cosine ≈ 0,71. Mỗi cụm báo cáo số bài, cosine, cụm từ đặc trưng, tên miền/SĐT dùng chung.
**Hệ thống không kết luận cùng người đăng/tổ chức.**

## 7. Phân tích dữ liệu

Tab *Tổng quan* và lệnh `analyze report --json report.json` cung cấp:
- tổng số bài, bình luận, bài có OCR, bài gắn cờ (mức Trung bình/Cao), phân bố mức, phương pháp thu thập;
- rule kích hoạt nhiều nhất (tách riêng tuyển dụng và đầu tư), phân loại bài, cụm từ lặp lại trong bài đáng ngờ;
- số bài theo ngày (ưu tiên ngày đăng), số bài gắn cờ theo ngày và theo nhóm dấu hiệu;
- cụm nội dung tương tự, liên kết/tên miền/SĐT dùng lại ở nhiều bài.

Mọi kết quả đều kèm câu: *số liệu chỉ mô tả tập dữ liệu đã thu thập, không đại diện cho xu hướng toàn bộ Facebook.*

## 8. Lệnh nhanh

```powershell
python main.py web --open                                   # giao diện; mở /analysis
python main.py analyze import tests\fixtures\sample_posts.csv  # dữ liệu mẫu TỔNG HỢP (tự viết, có nhãn)
python main.py analyze run [--force]
python main.py analyze list --level HIGH
python main.py analyze show 12
python main.py analyze review 12 CONFIRMED_SCAM --note "đã xác minh"
python main.py analyze evaluate [--apply]
python main.py analyze similar --threshold 0.4
python main.py analyze keywords --add "kích hoạt nhiệm vụ" --category JOB
python main.py analyze expand ; python main.py analyze suggestions --accept 3
python main.py analyze rules --disable C02 --weight J04=30
python main.py analyze settings risk_thresholds=25,60 similarity_threshold=0.5
python main.py monitor --once --interval-hours 24 [--keywords]
```

## 9. Hạn chế cần nêu trong báo cáo

- Rule dựa trên từ vựng nên có thể bị né bằng cách viết lóng mới hoặc chèn chữ vào ảnh không OCR được.
  Cần cập nhật rule và từ khóa định kỳ (mục đích của Query Expansion).
- Nhận diện bài cảnh báo dùng heuristic, nên có thể nhầm với bài lừa đảo "lấy lại tiền" (C04 được thiết kế để giảm lỗi này).
- Kết quả đo trên tập mẫu tổng hợp 20 bài chỉ để kiểm thử. Độ chính xác thật phải đo trên dữ liệu thực đã gán nhãn
  độc lập, và nên có ≥ 30 mẫu mỗi lớp.
- Thu thập phụ thuộc vào giao diện Facebook và quyền truy cập; tìm kiếm trên Facebook không đảm bảo đầy đủ.

## 10. Cải tiến độ chính xác khi thu thập

| Vấn đề | Cách xử lý |
|---|---|
| Bình luận/bài thiếu thời gian ("Thứ Sáu, 28 Tháng 8, 2026 lúc 17:35", "Hôm qua lúc 10:05", "10 Tháng 11 lúc 08:03", "November 10 at 8:03 PM") | Bộ đọc thời gian chung `common/times.py` cho cả bài viết và bình luận; "3 ngày nữa…" trong nội dung không bị nhận nhầm |
| Bài không có permalink (trang tìm kiếm, quảng cáo) bị lưu trùng khi bản rút gọn và bản đầy đủ khác nhau | ID dựa trên tác giả + 60 chữ/số đầu nội dung (bỏ "Xem thêm") |
| Không phân biệt quảng cáo | `is_sponsored` từ nhãn "Được tài trợ"/"Sponsored" hoặc link chiến dịch trả phí (`utm_medium=paid`…); hiển thị trong chi tiết bài |
| Facebook search trả cả bài không liên quan | `keyword_relevance`: tỷ lệ âm tiết của từ khóa có trong bài (chỉ để tham khảo) |
| Ảnh feed là bản thu nhỏ, OCR kém | Khi bật OCR, mở trang ảnh để lấy bản độ phân giải cao; OCR phóng to tới ~1600 px, tăng tương phản khi cần, ghép chữ theo thứ tự dòng, ghi độ tin cậy |
| Lỗi mạng khi tải ảnh làm mất ảnh | Thử lại 3 lần (bỏ qua 403/404 vì link đã hết hạn), kiểm tra đúng là ảnh, lưu đúng đuôi tệp |
| Ký tự ẩn chống đọc tự động | Bỏ trước khi kiểm tra/lưu nội dung |

Sửa dữ liệu đã thu thập (không xóa gì, không cần mở Facebook):

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py storage --repair
```

## 11. Thu thập đúng mục tiêu (bài lừa đảo thật, không phải bài nói về lừa đảo)

**Vấn đề quan sát được:** từ khóa "việc nhẹ lương cao" trả về quảng cáo tuyển dụng hợp pháp, review phim, bài cảnh
báo của Cục An ninh mạng, tin tuyển dụng thường; chỉ 1/6 bài là bài mời gọi đáng ngờ. Nguyên nhân:
1. Từ khóa trong đề là **tên gọi** của kiểu lừa đảo, xuất hiện nhiều trong báo chí/cảnh báo; bài mời gọi thật dùng
   ngôn ngữ "tác nghiệp" (CTV, chốt đơn, hoa hồng %, ib zalo).
2. Tìm kiếm mặc định ưu tiên bài nhiều tương tác (trang lớn, báo chí); bài lừa đảo thường mới, ít tương tác.

**Giải pháp đã cài đặt:**

| Cơ chế | Mô tả |
|---|---|
| Ưu tiên bài mới nhất | Tham số `filters` (recent_posts) của trang tìm kiếm; bật mặc định trên giao diện, `--recent` trên CLI |
| Bộ lọc dấu hiệu (`scam_filter`) | Chấm điểm bằng rule TRƯỚC khi lưu. Giữ bài nếu: không phải bài cảnh báo, điểm ≥ `filter_min_score` (10) và có ≥ 2 nhóm dấu hiệu độc lập hoặc 1 dấu hiệu cốt lõi. Bài có ảnh được OCR rồi xét lại. Số bài bị loại được ghi nhận |
| Từ khóa tác nghiệp | 13 từ khóa mới (ví dụ "tuyển ctv chốt đơn tại nhà", "kèm 1-1 lợi nhuận mỗi ngày") bên cạnh 14 từ khóa khái niệm |
| Hiệu quả từ khóa | `keywords.stats` (schema v6): số lượt, bài thấy, đã lưu, bị lọc; tab Từ khóa hiển thị **yield** = bài gắn cờ / bài đã lưu |
| Rule mới | J09 nhắm vào nhóm dễ tổn thương (sinh viên, mẹ bỉm…), C05 chủ động phủ nhận nghi ngờ lừa đảo |

Trên 10 bài thật đã thu, bộ lọc chỉ giữ bài mời gọi đáng ngờ và loại đúng 9 bài còn lại.

**Lưu ý phương pháp:** bộ lọc làm tăng tỷ lệ bài liên quan nhưng loại bỏ mẫu âm và có thể bỏ sót lừa đảo kiểu mới
mà rule chưa biết. Khi xây tập dữ liệu để **đánh giá độ chính xác**, hãy TẮT bộ lọc để tránh sai lệch chọn mẫu.
Quy trình đề xuất: thu với bộ lọc tắt → xem yield từng từ khóa → giữ từ khóa yield cao, thêm từ khóa từ
"Mở rộng từ khóa" → thu định kỳ với bộ lọc bật.
