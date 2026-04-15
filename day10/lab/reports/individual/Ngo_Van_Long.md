# Báo Cáo Cá Nhân — Lab Day 10: Data Pipeline & Observability

**Họ và tên:** Ngo Van Long  
**Vai trò:** Ingestion / Raw Owner  
**Ngày nộp:** 2026-04-15  

---

## 1. Tôi phụ trách phần nào?

**File / module:**

- **`etl_pipeline.py`** — thiết kế entrypoint `cmd_run()`: đọc `data/raw/policy_export_dirty.csv`, sinh `run_id` theo timestamp, ghi log bốn trường bắt buộc (`raw_records`, `cleaned_records`, `quarantine_records`, `run_id`) vào `artifacts/logs/`, ghi manifest JSON vào `artifacts/manifests/`.
- **`contracts/data_contract.yaml`** — điền `owner`, `SLA`, khai báo hai nguồn dữ liệu (HR policy export + IT helpdesk chunk export) cùng failure mode tương ứng.
- **`artifacts/manifests/manifest_sprint1.json`** — artifact đầu tiên xác nhận DoD Sprint 1.

**Kết nối với thành viên khác:**

Cung cấp `run_id` và đường dẫn manifest chuẩn để **Monitoring / Docs Owner** đọc trong `freshness_check.py`; đảm bảo `raw_records` khớp số dòng CSV gốc để **Cleaning / Quality Owner** đối chiếu khi tính `quarantine_records`.

**Bằng chứng:**

Comment `# Ingestion Owner: NVL` trên hàm `_ingest()` và `_write_manifest()` trong `etl_pipeline.py`.

---

## 2. Một quyết định kỹ thuật

**Quyết định: ghi manifest ngay sau ingest, trước khi clean — không ghi một manifest duy nhất ở cuối pipeline.**

Ban đầu tôi định ghi manifest một lần ở cuối `cmd_run()` cho gọn. Tuy nhiên nếu pipeline halt ở expectation (Sprint 3 — inject có chủ đích), manifest sẽ không được tạo, khiến `freshness_check.py` không tìm thấy file và trả về lỗi không rõ nguyên nhân thay vì `FAIL: pipeline_halted`.

Quyết định cuối: ghi hai snapshot manifest — `stage=ingest` (ngay sau đọc raw, trước clean) và `stage=publish` (sau embed thành công). `freshness_check.py` đọc `stage=publish`; khi pipeline halt, chỉ có `stage=ingest` tồn tại → Monitoring Owner nhận tín hiệu rõ ràng "ingest xong nhưng chưa publish".

Kết quả đo được (`manifest_sprint1.json`):
```
"stage": "ingest", "raw_records": 20, "run_id": "sprint1"
```

---

## 3. Một lỗi hoặc anomaly đã xử lý

**Anomaly: `run_id` trùng khi chạy hai lần trong cùng một giây.**

Khi tôi test `python etl_pipeline.py run` liên tiếp, hai manifest sinh ra tên file giống nhau (`manifest_20260415_153201.json`) — file sau ghi đè file trước, mất log run đầu.

**Nguyên nhân:** `run_id` ban đầu chỉ dùng `datetime.now().strftime("%Y%m%d_%H%M%S")` — độ phân giải giây, không đủ khi test nhanh.

**Fix:** Thêm 4 chữ số random hex vào cuối: `run_id = f"{timestamp}_{uuid4().hex[:4]}"`. Sau fix, mỗi run có `run_id` duy nhất kể cả khi chạy cùng giây; manifest không còn bị ghi đè.

Kiểm tra: chạy `for i in 1 2 3; do python etl_pipeline.py run; done` → 3 file manifest khác nhau trong `artifacts/manifests/`.

---

## 4. Bằng chứng trước / sau

Trích `artifacts/logs/run_sprint1.log` và `run_sprint2.log` (`run_id`: `sprint1` → `sprint2`):

| run_id | raw_records | cleaned_records | quarantine_records |
|--------|-------------|-----------------|-------------------|
| `sprint1` | 20 | 16 | 4 |
| `sprint2` | 20 | 15 | 5 |

`quarantine_records` tăng từ 4 → 5 sau khi **Cleaning Owner** thêm Rule B (`strip_bom_and_control_chars`) — con số khớp bảng `metric_impact` trong `reports/group_report.md`, xác nhận manifest log phản ánh đúng thay đổi rule.

---

## 5. Cải tiến tiếp theo

Nếu có thêm 2 giờ, tôi sẽ thêm field `ingest_source_hash` (MD5 của file CSV raw) vào manifest. Khi rerun mà hash không đổi, pipeline có thể bỏ qua bước ingest và dùng lại `raw_records` từ manifest trước — tránh đọc lại file lớn không cần thiết và giúp phát hiện ngay khi file nguồn bị thay thế ngoài lịch export.
