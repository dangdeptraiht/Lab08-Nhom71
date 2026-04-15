# Báo Cáo Cá Nhân — Lab Day 10: Data Pipeline & Observability

**Họ và tên:** Đăng Nguyễn
**Vai trò:** Cleaning / Quality Owner
**Ngày nộp:** 15/04/2026
**Độ dài yêu cầu:** **400–650 từ**

---

## 1. Tôi phụ trách phần nào? (80–120 từ)

**File / module:**
- `day10/lab/transform/cleaning_rules.py`: Chịu trách nhiệm tiếp nhận luồng dữ liệu thô, tiến hành clean và xuất các row lỗi ra khu vực quarantine riêng. Tại đây, tôi đã trực tiếp phát triển 3 rule làm sạch mới cho dự án: Rule A (`quarantine_missing_exported_at`), Rule B (strip chuỗi ký tự BOM/control chars rác), và Rule C (`quarantine_chunk_too_short`).
- `day10/lab/quality/expectations.py`: Xây dựng các rào chắn pipeline (Expectations) dùng để chặn cảnh báo hoặc làm sập quá trình build pipeline khi lượng data sai lệch, bao gồm rào Expecation E7 (bắt buộc `exported_at`) và E8 (độ dài tối thiểu của chunk text).

**Kết nối với thành viên khác:**
Tôi phối hợp mật thiết với vai trò Ingestion để nhận CSV từ dữ liệu thô, sau đó luồng dữ liệu tiến qua khu vực làm sạch cung cấp bản data tĩnh chuẩn nhất cho thành viên phụ trách mảng Embed. Số liệu quarantine cũng đóng vai trò nền tảng cho việc xuất report cho Observability owner.

**Bằng chứng (commit / comment trong code):**
Tại module `cleaning_rules.py`:
```python
        # NEW Rule B: strip_bom_and_control_chars
        if apply_bom_strip_rule:
            text_stripped = _strip_bom_and_control(text)
```
Tại quarantine logs (`quarantine_sprint2.csv`):
```csv
13,it_helpdesk_faq, ,2026-02-01,2026-04-10T08:00:00,empty_after_bom_strip
```

---

## 2. Một quyết định kỹ thuật (100–150 từ)

Khi xây dựng danh sách các quy tắc trong thư mục `expectations.py`, tôi phải đối mặt với quyết định thiết kế hành vi cho từng Expectation: khi nào cần làm sập toàn bộ pipeline (severity mức `halt`) và khi nào chỉ nên dừng ở việc đánh dấu nhắc nhở hệ thống (severity mức `warn`).

Đối với E7 `no_missing_exported_at`, tôi ưu tiên áp dụng mức severity là `halt` bởi vì trường `exported_at` mang ý nghĩa như metadata thời điểm cấu thành nên file, trực tiếp quản lý phiên bản timeline tài liệu. Nếu thiếu giá trị này, toàn bộ logic tra cứu xử lý truy xuất phiên bản về sau sẽ hỏng, vì vậy việc không thoả hiệp là điều tất yếu.

Tuy nhiên với E8 `chunk_text_min_length_20`, tôi chọn chỉ dùng `warn`. Lý do vì vài đoạn văn bản dẫu ngắn (ví dụ "Liên hệ IT." - độ dài 11), tuy gây nhiễu nhưng vẫn được xem là document hợp lệ về mặt ngữ pháp. Loại bỏ lập tức nó ra hệ thống là hành động tuỳ tiện, do đó hành vi cảnh cáo `warn` cho kỹ sư rà soát sau sẽ hợp lý hơn thả nổi.

---

## 3. Một lỗi hoặc anomaly đã xử lý (100–150 từ)

**Triệu chứng:** Trong suốt Sprint 1 ban đầu, chúng ta gặp hiện tượng quá trình nhúng file vào Vector Database phát sinh các trường cực ngắn, thậm chí thỉnh thoảng một vài text bị nhìn thấy rỗng khi in ra nhưng thật ra vẫn qua được check logic. 

**Phát hiện:** Khi sử dụng script kiểm duyệt, tôi phát hiện ra những row này chứa những ký tự tàng hình (Zero Width) và đặc biệt là độ dài prefix theo dấu `BOM (U+FEFF)`.

**Khắc phục:** Xử lý ngay trong bước Clean, tôi bổ sung một logic Rule B. Thông qua việc dùng regex `_CONTROL_CHARS.sub()` với pattern `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff]`, text được strip toàn bộ dải ký tự tàng hình này. Sau khi triển khai rule quét vào `quarantine_sprint2.csv`, các row mắc lỗi toàn BOM (như `chunk_id 13`) sẽ lập tức đưa vào dạng cấm lọt qua tầng Embeds với phân định `"empty_after_bom_strip"`.

---

## 4. Bằng chứng trước / sau (80–120 từ)

Toàn bộ nỗ lực dọn rác và filter ngày policy hợp quy cũ trong bộ tool của tôi chứng minh hiệu quả cực lớn lên kết quả RAG đánh giá cuối cùng.

Cụ thể, đây là log so sánh rút trích từ file `artifacts/eval/before_after_eval.csv` so sánh giữa 2 quá trình `inject-bad` (trước bộ rule của tôi) và `sprint3-clean` (sau khi bộ rule chạy làm sạch hoàn toàn):

- **Trước (`inject-bad` run_id):**
  `inject-bad,q_leave_version,"Theo chính sách...",hr_leave_policy,...,yes,yes,yes,5`
- **Sau (`sprint3-clean` run_id):**
  `sprint3-clean,q_leave_version,"Theo chính sách...",hr_leave_policy,...,yes,no,yes,5`

Như vậy, thông số `hits_forbidden` đã từ `yes` chuyển thành `no`. Điều này làm bằng chứng khách quan mạnh mẽ cho việc đã loại trừ triệt để khỏi hệ thống phiên bản tài liệu nghỉ phép năm HR version cũ.

---

## 5. Cải tiến tiếp theo (40–80 từ)

Nếu có dư dả thêm 2 giờ, thay vì duy trì các hàm validation tuỳ biến tự viết, tôi sẽ port toàn bộ module `expectations.py` sang dùng framework thư viện **Great Expectations**. Bằng cách định nghĩa file chuẩn expectation suite để gắn trực tiếp config validation với định nghĩa `data_contract.yaml`, giúp sinh ra báo cáo chất lượng file HTML trực quan (Data Docs) thay vì csv.
