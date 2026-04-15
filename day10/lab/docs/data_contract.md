# Data Contract — Lab Day 10

**Nhóm:** Nhóm 71  
**Cập nhật:** 2026-04-15  
**Đồng bộ với:** `contracts/data_contract.yaml`

---

## 1. Nguồn dữ liệu (source map)

| Nguồn | Phương thức ingest | Failure mode chính | Metric / alert |
|-------|-------------------|-------------------|----------------|
| `data/raw/policy_export_dirty.csv` — Export định kỳ từ hệ DB policy/CS/HR | `load_raw_csv()` đọc CSV UTF-8 qua `csv.DictReader` | Duplicate chunk_text; thiếu effective_date; doc_id lạ không thuộc allowlist; ngày sai format (DD/MM/YYYY thay vì ISO) | `raw_records`, `quarantine_records` trong log/manifest |
| `data/docs/*.txt` — Tài liệu canonical (source of truth) | Không ingest trực tiếp trong pipeline Sprint 1; dùng làm tham chiếu kiểm tra version | Conflict version (HR 10 ngày 2025 vs 12 ngày 2026); refund window sai (14 ngày cũ vs 7 ngày v4) | `expectation[hr_leave_no_stale_10d_annual]`, `expectation[refund_no_stale_14d_window]` |
| `data/docs/hr_leave_policy.txt` — Chính sách nghỉ phép HR (canonical) | Đọc chunk qua pipeline; lọc theo `effective_date >= 2026-01-01` | Bản cũ 2025 có thể lọt vào cleaned nếu thiếu rule date filter | `quarantine_records` tăng khi có bản HR stale; expectation `hr_leave_no_stale_10d_annual` |
| `data/docs/policy_refund_v4.txt` — Chính sách hoàn tiền v4 (canonical) | Đọc chunk; áp dụng rule fix `14 ngày → 7 ngày` khi `apply_refund_window_fix=True` | Chunk stale chứa "14 ngày làm việc" từ v3 migration lỗi | `expectation[refund_no_stale_14d_window]` halt nếu còn chunk 14 ngày sau fix |

---

## 2. Schema cleaned

| Cột | Kiểu | Bắt buộc | Constraint | Ghi chú |
|-----|------|----------|------------|---------|
| `chunk_id` | string | Có | unique, non-empty | SHA-256 hash 16 ký tự của `doc_id|chunk_text|seq` — ổn định qua rerun |
| `doc_id` | string | Có | thuộc `ALLOWED_DOC_IDS` | Khóa logic tài liệu nguồn; quarantine nếu lạ |
| `chunk_text` | string | Có | `len >= 8` | Nội dung chunk đã fix stale content; không rỗng |
| `effective_date` | date | Có | format `YYYY-MM-DD` | Chuẩn hoá từ DD/MM/YYYY hoặc ISO; quarantine nếu không parse được |
| `exported_at` | datetime | Có | ISO 8601 | Dùng để tính freshness SLA |

**Allowlist `doc_id` (khớp `contracts/data_contract.yaml`):**
- `policy_refund_v4`
- `sla_p1_2026`
- `it_helpdesk_faq`
- `hr_leave_policy`

---

## 3. Quy tắc quarantine vs drop

| Reason code | Điều kiện | Hành động |
|-------------|-----------|-----------|
| `unknown_doc_id` | `doc_id` không thuộc allowlist | Quarantine (lưu CSV) — cần review thêm doc mới |
| `missing_effective_date` | `effective_date` rỗng | Quarantine |
| `invalid_effective_date_format` | Không parse được sang ISO | Quarantine với `effective_date_raw` |
| `stale_hr_policy_effective_date` | `doc_id=hr_leave_policy` và `effective_date < 2026-01-01` | Quarantine — conflict version, bản cũ 2025 |
| `missing_chunk_text` | `chunk_text` rỗng sau strip | Quarantine |
| `duplicate_chunk_text` | Nội dung chunk trùng (case-insensitive, collapse whitespace) | Quarantine — giữ bản đầu tiên |

**Ai approve merge lại:** Data owner (nhóm Cleaning & Quality) review `artifacts/quarantine/*.csv` trước mỗi sprint. Bản cũ HR không được merge lại trừ khi có chỉ thị cập nhật policy chính thức.

---

## 4. Phiên bản & canonical

| Tài liệu | File canonical | Cutoff version |
|----------|---------------|----------------|
| Chính sách hoàn tiền | `data/docs/policy_refund_v4.txt` | v4 — cửa sổ 7 ngày làm việc |
| Chính sách nghỉ phép HR | `data/docs/hr_leave_policy.txt` | Bản 2026 (`effective_date >= 2026-01-01`) — 12 ngày phép năm |
| SLA P1 | `data/docs/sla_p1_2026.txt` | 2026 — 15 phút phản hồi, 4 giờ resolution |
| IT Helpdesk FAQ | `data/docs/it_helpdesk_faq.txt` | current |

**Cutoff versioning HR (đọc từ contract/env — tránh hard-code):**  
`hr_leave_min_effective_date: "2026-01-01"` (xem `contracts/data_contract.yaml › policy_versioning`)

---

## 5. SLA Freshness

| Boundary | SLA | Kết quả mẫu Sprint 1 |
|----------|-----|----------------------|
| `publish` | 24 giờ (`FRESHNESS_SLA_HOURS=24`) | **FAIL** — `exported_at=2026-04-10`, age ~120h; giải thích trong runbook |

> CSV mẫu dùng `exported_at=2026-04-10T08:00:00` (cố ý cũ để demo SLA FAIL). Pipeline vẫn PASS vì `PIPELINE_OK` tách với `freshness_check`. Xem `docs/runbook.md` để biết cách xử lý FAIL.

---

## 6. Chủ sở hữu

| Thành phần | Owner |
|------------|-------|
| Pipeline ETL | Nhóm 71 — Ingestion Owner |
| Cleaning rules | Nhóm 71 — Cleaning & Quality Owner |
| Embedding & index | Nhóm 71 — Embed Owner |
| Monitoring / freshness | Nhóm 71 — Monitoring / Docs Owner |
| Alert channel | `__TODO__` (điền kênh Slack/email khi deploy thật) |
