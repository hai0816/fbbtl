# Database: sources, posts, comments, media, post_versions, search_hits

Schema thực thi nằm trong [schema.sql](../fb_scraper/storage/schema.sql), phiên bản 4
(`PRAGMA user_version`). Bốn bảng gốc `sources/posts/comments/media` và các cột của SQL
yêu cầu được giữ nguyên; v4 thêm hai bảng nhật ký `post_versions` và `search_hits`.
`sqlite_sequence` do SQLite tự quản lý AUTOINCREMENT.

```mermaid
erDiagram
    sources ||--o{ posts : source_id
    sources ||--o{ search_hits : source_id
    posts ||--o{ comments : post_id
    posts ||--o{ post_versions : post_id
    comments o|--o{ comments : parent_comment_id
    posts o|--o{ media : post_id
    comments o|--o{ media : comment_id
```

Một dòng `media` chỉ thuộc **một bài hoặc một bình luận**, được kiểm tra bằng CHECK.
Không có bảng `profiles`, `groups` hoặc `search_results` ở phiên bản mới.

## Nguyên tắc lưu trữ

- **SQLite là nơi lưu chính**, commit sau từng bài. JSONL chỉ là snapshot xuất khi kết thúc lượt.
- **WAL** (`journal_mode=WAL`, `synchronous=NORMAL`): giao diện đọc được trong lúc collector
  đang ghi. Khi sao chép database, chép cả `posts.db-wal` và `posts.db-shm` nếu còn tồn tại,
  hoặc chạy `storage --export-jsonl` trước để SQLite gộp WAL vào `posts.db`.
- **Không xóa ngầm:** trường bị bỏ qua giữ giá trị cũ; danh sách rỗng (`top_comments=[]`,
  `images=[]`, `links=[]`) cũng không xóa dữ liệu đã có. Muốn xóa bình luận của một bài phải
  gọi `storage.clear_comments(db, post_key)`.
- **posts giữ trạng thái mới nhất, post_versions giữ lịch sử** (xem bên dưới).

## Lịch sử phiên bản bài viết (post_versions)

Mỗi lần lưu, nếu nội dung, tác giả hoặc số cảm xúc/bình luận/chia sẻ khác lần ghi gần nhất,
một dòng mới được thêm với `content_sha256` (SHA-256 của nội dung) và `seen_at`. Lưu lại y hệt
không tạo dòng mới. Nhờ vậy giữ được bằng chứng "bài từng ghi X, sau đó sửa thành Y" và theo dõi
số tương tác theo thời gian; hash cho phép chứng minh nội dung đã lưu không bị chỉnh sửa.

```sql
SELECT seen_at, content_sha256, reaction_count, content
FROM post_versions WHERE post_id = (SELECT id FROM posts WHERE post_key = '123') ORDER BY id;
```

## Nhật ký tìm kiếm (search_hits)

Mỗi lần lệnh `search` phát hiện một người/nhóm là một dòng (`keyword`, `entity_type`, `name`,
`search_url`, `found_at`), kể cả khi cùng URL xuất hiện ở nhiều từ khóa. Trước v4 nhật ký này là
mảng `search_history` trong JSON của `sources.metadata`, phình theo số lần tìm và không truy vấn được.

```sql
SELECT h.keyword, COUNT(*) AS lan_tim_thay, s.source_label
FROM search_hits h JOIN sources s ON s.id = h.source_id GROUP BY h.keyword, s.id;
```

## Định danh và quan hệ

| Cột | Ý nghĩa |
| --- | --- |
| `sources.id`, `posts.id`, `comments.id`, `media.id` | Khóa chính số nguyên do SQLite cấp |
| `posts.source_id` | Khóa ngoại đến `sources.id` |
| `posts.post_key` | Định danh Facebook, UNIQUE |
| `comments.post_id` | Khóa ngoại đến `posts.id`, không phải post_key |
| `comments.comment_key` | Định danh bình luận Facebook, UNIQUE |
| `comments.parent_comment_id` | Khóa ngoại đến `comments.id` của cha trực tiếp |
| `comments.parent_comment_key` | Khóa Facebook của cha; giữ khi cha chưa được đọc để nối FK sau |
| `comments.root_comment_key` | Khóa Facebook của bình luận gốc của luồng |
| `posts.original_post_key` | Khóa Facebook của bài gốc được chia sẻ, có thể chưa được thu thập |

Reply được chèn theo hai lượt: lưu các bản ghi trước, sau đó nối FK. Nếu cha xuất
hiện ở lần thu thập sau, FK của các reply đang chờ được cập nhật. Không tạo bình
luận cha giả. Nếu chỉ biết gốc luồng, giữ `root_comment_key` và `is_reply=1`, để
`parent_comment_id=NULL`. Không suy ra cha trực tiếp từ tên người được nhắc trong text.
Trigger và storage từ chối cha thuộc bài khác hoặc quan hệ tạo chu kỳ.

ID base64 `comment:<post>_<comment>` và ID số được chuẩn hóa về cùng comment_key.
Bình luận cũ chỉ có text dùng khóa `local:...`, giữ trạng thái quan hệ chưa xác định.

## Cột bổ sung

Các cột gốc của SQL yêu cầu được giữ. Những cột sau bổ sung để giữ các chức năng hiện có:

| Bảng | Cột bổ sung |
| --- | --- |
| `sources` | `metadata`: thông tin Giới thiệu (`entity`); có index trên `entity.entity_id` |
| `posts` | `url`, `author`, `author_url`, `posted_raw`, `reaction_count`, `comment_count`, `share_count`, `metadata` |
| `comments` | `author`, `author_url`, `url`, `created_raw`, `parent_comment_key`, `root_comment_key`, `is_reply` |
| `media` | `media_url`, `metadata` |

`sources.source_type` phân biệt `profile`, `page`, `group`. Từ khóa là cách tìm bài,
được giữ ở `posts.metadata.source` dạng `kw:...`; không được lưu giả thành một Trang.
Khi chưa biết nguồn, dùng `source_ref=unresolved:<post_key>` cùng metadata đánh dấu
chưa xác định; không bịa URL Facebook. Với tài khoản chưa phân biệt được Trang/cá nhân,
collector tạm dùng `profile`; chọn chế độ Trang hoặc đọc Giới thiệu sẽ xác định lại.

`posts.collected_at` giữ lần lưu đầu, `last_seen_at` cập nhật khi đọc/lưu lại. Các trường
không đọc được để NULL; DEFAULT 0 của SQL vẫn áp dụng khi bỏ cột trong câu INSERT.

## Media và OCR

Mỗi ảnh/video/GIF là một dòng, giữ `position`. Ảnh tải về có `file_path` và hash SHA-256
trong `file_hash`. `file_path` là đường dẫn **tương đối so với thư mục `data/`** (ví dụ
`images/123_0.jpg`) nên database vẫn đúng khi chuyển project sang thư mục hoặc máy khác. OCR chính nằm ở `ocr_text`; chi tiết URL/số điện thoại OCR nhận diện
được giữ trong `metadata.ocr`. Media bình luận tham chiếu `comment_id`, không gắn đồng thời
vào post_id. URL video trực tiếp có thể được lưu khi DOM cung cấp; đường dẫn blob/stream
không được giả thành file đã tải. `file_path=NULL` nghĩa là chưa tải media về máy.

## Chuyển dữ liệu cũ

Khi gặp database phiên bản cũ, project tự tạo `posts.before-v4-<mã>.db` bằng SQLite backup,
sau đó chuyển dữ liệu trong một transaction. Nguồn được khử trùng theo URL; thông tin
profiles/groups chuyển vào `sources.metadata.entity`; lịch sử tìm kiếm chuyển vào bảng `search_hits`.
Từ v3 lên v4: `search_history` trong JSON chuyển sang `search_hits`, `file_path` tuyệt đối nằm
trong `data/` đổi thành tương đối, mỗi bài nhận một dòng `post_versions` gốc.
Nếu chuyển thất bại, rollback cả DDL lẫn dữ liệu và giữ bản sao; không xóa database để tạo lại.
Database đã tạo trực tiếp từ bốn bảng SQL gốc cũng được bổ sung cột mà không thay ID cũ.

`PRAGMA foreign_keys=ON` áp dụng cho mỗi kết nối ghi của ứng dụng. Kiểm tra thủ công:

```sql
PRAGMA foreign_key_check;
PRAGMA integrity_check;

SELECT p.id, p.post_key, s.source_type, s.source_label, p.content
FROM posts p JOIN sources s ON s.id = p.source_id;

SELECT c.id, c.comment_key, c.author, c.content,
       parent.comment_key AS parent_key, parent.content AS parent_content
FROM comments c
LEFT JOIN comments parent ON parent.id = c.parent_comment_id
WHERE c.post_id = 1
ORDER BY c.id;

SELECT m.*, p.post_key, c.comment_key
FROM media m
LEFT JOIN posts p ON p.id=m.post_id
LEFT JOIN comments c ON c.id=m.comment_id;
```

## JSONL và model Python

`sources.jsonl`, `posts.jsonl`, `comments.jsonl`, `media.jsonl`, `post_versions.jsonl`,
`search_hits.jsonl` dùng tên cột và ID của schema. `posts.jsonl` bổ sung `source_record`,
`comments`, `media`, `versions` để đọc một bài và các bản ghi liên quan trong một dòng. `data_agent.json` dùng cùng định dạng.

Model thu thập `Post`/`Comment` là DTO trung gian: `post_id` và `comment_id` trong
collector/agent là khóa Facebook; `text` là nội dung. Storage ánh xạ sang `post_key`,
`comment_key`, `content` và FK số. `load_post()` cung cấp projection cho pipeline;
`post_record()` trả bản ghi chuẩn hóa. Giao diện dùng `comment_records` với ID số
để dựng cây reply, không dùng tên hoặc vị trí text để đoán quan hệ.

Các file `profiles.jsonl`, `groups.jsonl`, `search_results.jsonl` còn lại từ phiên bản cũ
được giữ để đối chiếu; bản mới không cập nhật chúng. Lệnh xuất lại toàn bộ snapshot:

```powershell
.\Demo_Fb_scraper\.venv\Scripts\python.exe .\Demo_Fb_scraper\main.py storage --export-jsonl
```

## v5: trinh sát & phát hiện dấu hiệu lừa đảo

Migration `storage/migration_v5.py` dựng lại bảng `sources` (giữ nguyên mọi `id`) để cho phép
`source_type='dataset'`, và thêm `monitor_enabled`, `collection_status`, `last_error`. File backup
`posts.before-v5-*.db` được tạo trước khi đổi. Các bảng mới:

| Bảng | Nội dung |
|---|---|
| `keywords` | Bộ từ khóa trinh sát (JOB/INVESTMENT/OTHER), nguồn gốc default/user/expansion, bật/tắt |
| `keyword_suggestions` | Đề xuất mở rộng truy vấn: PENDING → ACCEPTED/REJECTED (người dùng duyệt) |
| `detection_rules` | Rule: điều kiện JSON, trọng số, bật/tắt, ưu tiên, nhóm tương đương |
| `analysis_settings` | Ngưỡng mức rủi ro, hệ số ngữ cảnh cảnh báo, ngưỡng tương đồng… |
| `post_analysis` | Kết quả tiền xử lý (văn bản chuẩn hóa, OCR gộp, đặc trưng, ngữ cảnh bình luận, hash trùng) |
| `risk_assessments` | Điểm 0–100, mức, phân loại, rule khớp, bằng chứng, giải thích, trạng thái duyệt |
| `import_batches` | Nhật ký nhập dataset (số dòng thêm/cập nhật/trùng/lỗi) |

Dữ liệu gốc ở `posts`/`media`/`comments` không bị sửa bởi bước phân tích. Chi tiết: [PHAN_TICH_LUA_DAO.md](PHAN_TICH_LUA_DAO.md).
