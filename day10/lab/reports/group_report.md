# Báo Cáo Nhóm — Lab Day 10: Data Pipeline & Data Observability

**Tên nhóm:** Nhóm 71  
**Thành viên:**
| Tên | Vai trò (Day 10) | Email |
|-----|------------------|-------|
| Ngo Van Long | Ingestion / Raw Owner + Cleaning & Quality Owner | _ |
| _ | Embed & Idempotency Owner | _ |
| _ | Monitoring / Docs Owner | _ |

**Ngày nộp:** 2026-04-15  
**Repo:** dangdeptraiht/Lab08-Nhom71  
**Độ dài khuyến nghị:** 600–1000 từ

---

> **Nộp tại:** `reports/group_report.md`  
> Có **run_id**, **đường dẫn artifact**, và **bằng chứng before/after** (CSV eval hoặc screenshot).

---

## 1. Pipeline tổng quan

**Nguồn raw:** `data/raw/policy_export_dirty.csv` — export mô phỏng từ hệ DB policy/HR/CS với các lỗi cố ý: duplicate, thiếu ngày, doc_id lạ, ngày không ISO, xung đột version HR (10 vs 12 ngày phép), và chunk refund sai cửa sổ (14 vs 7 ngày).

**Chuỗi lệnh end-to-end:**
```
python etl_pipeline.py run --run-id sprint2
```
Pipeline thực hiện: `load_raw_csv` → `clean_rows` (9 rules) → `write_cleaned_csv + write_quarantine_csv` → `run_expectations` (8 expectation) → `cmd_embed_internal` (upsert Chroma, prune stale ids) → `write manifest` → `check_manifest_freshness`.

**run_id:** Được gán qua flag `--run-id` hoặc UTC timestamp tự động. Xuất hiện ở dòng đầu log (`run_id=sprint2`), trong manifest JSON, và trong metadata mỗi vector Chroma (`metadata.run_id`).

**Lệnh chạy một dòng:**
```bash
python etl_pipeline.py run
```

**Kết quả Sprint 1 vs Sprint 2:**

| Metric | Sprint 1 (`run_id=sprint1`) | Sprint 2 (`run_id=sprint2`) |
|--------|-----------------------------|-----------------------------|
| raw_records | 10 | 14 |
| cleaned_records | 6 | 7 |
| quarantine_records | 4 | 7 |
| embed vectors | 6 | 7 |
| Expectations passed | 6/6 | 8/8 |

---

## 2. Cleaning & expectation

### 2a. Bảng metric_impact (bắt buộc — chống trivial)

| Rule / Expectation mới (tên ngắn) | Trước (số liệu) | Sau / khi inject (số liệu) | Chứng cứ |
|-----------------------------------|------------------|-----------------------------|-----------|
| **Rule A** `quarantine_missing_exported_at` | Sprint 1: quarantine=4 (row 14 chưa có) | Sprint 2: quarantine=7 (+1 từ row 14 `exported_at=''`) | `quarantine_sprint2.csv`: `chunk_id=14, reason=missing_exported_at`; inject Sprint 3 (tắt Rule A): E7 FAIL |
| **Rule B** `strip_bom_and_control_chars` | Sprint 1: row 12/13 chưa có | Sprint 2: row 12 BOM stripped → **cleaned** (text sạch); row 13 BOM-only → **quarantine** `empty_after_bom_strip` | `cleaned_sprint2.csv`: chunk `policy_refund_v4_7_*`; `quarantine_sprint2.csv`: row 13 |
| **Rule C** `quarantine_chunk_too_short` | Sprint 1: row 11 chưa có | Sprint 2: row 11 ("Liên hệ IT.", 11 chars) → quarantine `chunk_too_short` | `quarantine_sprint2.csv`: `chunk_id=11, chunk_length=11`; inject (tắt Rule C): E8 warn FAIL |
| **E7** `no_missing_exported_at` (halt) | inject row 14 (exported_at='') + tắt Rule A: **E7 FAIL halt** | Rule A bật: quarantine row 14 trước expectations → **E7 PASS** | Log sprint2: `expectation[no_missing_exported_at] OK (halt) :: missing_exported_at_count=0` |
| **E8** `chunk_text_min_length_20` (warn) | inject row 11 + tắt Rule C: **E8 FAIL warn**, short_chunks_under_20=1 | Rule C bật: quarantine row 11 → **E8 PASS** | Log sprint2: `expectation[chunk_text_min_length_20] OK (warn) :: short_chunks_under_20=0` |

**Rule chính (baseline + mở rộng):**

- **Baseline** (6 rules): unknown_doc_id quarantine; normalize effective_date; stale HR 2025 quarantine; empty chunk_text quarantine; dedupe; fix refund 14→7
- **Sprint 2 mới** (3 rules): quarantine_missing_exported_at; strip_bom_and_control_chars; quarantine_chunk_too_short (< 20 chars)

**Ví dụ expectation fail và cách xử lý:**
Sprint 3 inject: `python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate` → expectation `refund_no_stale_14d_window` FAIL halt, nhưng `--skip-validate` cho phép embed để demo eval xấu (xem Mục 3).

---

## 3. Before / after ảnh hưởng retrieval

**Kịch bản inject (Sprint 3):**
Chạy `python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate` → embed chunk chứa "14 ngày làm việc" (stale policy-v3). Câu hỏi `q_refund_window` sẽ trả về chunk stale → `hits_forbidden=yes`.

**Kết quả định lượng** (`artifacts/eval/before_after_eval.csv`, top-k=5):

| question_id | inject-bad contains/forbidden | sprint3-clean contains/forbidden | Kết luận |
|-------------|-------------------------------|----------------------------------|----------|
| q_refund_window | yes / **YES** | yes / **NO** | Stale "14 ngày" lọt top-5 khi bỏ fix → retrieval sai |
| q_leave_version | yes / **YES** | yes / **NO** | Stale HR 2025 lọt index khi bypass quarantine |
| q_p1_sla | yes / no | yes / no | Không bị ảnh hưởng |
| q_lockout | yes / no | yes / no | Không bị ảnh hưởng |

---

## 4. Freshness & monitoring

**SLA chọn:** `FRESHNESS_SLA_HOURS=24` (từ `.env`). Measured at: `publish` boundary.

**Ý nghĩa kết quả:**
- **PASS**: `age_hours ≤ 24` — data được export trong vòng 24 giờ, đủ tươi để agent trả lời chính xác.
- **WARN**: `no_timestamp_in_manifest` — manifest thiếu timestamp, cần kiểm tra lại pipeline ingest.
- **FAIL**: `age_hours > 24` — data cũ; Sprint 1/2 đều FAIL (`age=~120h`) vì CSV mẫu có `exported_at=2026-04-10`. Đây là hành vi **hợp lý và có chủ đích** để demo freshness.

**Lệnh kiểm tra:**
```bash
python etl_pipeline.py freshness --manifest artifacts/manifests/manifest_sprint2.json
```

**Giải thích FAIL hợp lý:** CSV mẫu cố ý dùng `exported_at=2026-04-10T08:00:00` (5 ngày cũ). Trong môi trường production, FAIL sẽ trigger alert kênh `slack:#data-ops-alert`. Nhóm có thể đặt `FRESHNESS_SLA_HOURS=200` trong `.env` để PASS với data mẫu — xem `docs/runbook.md`.

---

## 5. Liên hệ Day 09

Pipeline này dùng **collection tách biệt** `day10_kb` (không dùng chung với Day 09). Lý do: Day 09 dùng collection raw từ `data/docs/*.txt` trực tiếp, chưa có lớp cleaning + expectation. Day 10 pipeline ingest qua CSV export (mô phỏng DB sync), apply 9 cleaning rules, validate 8 expectation, rồi mới embed — đảm bảo chất lượng dữ liệu trước khi agent đọc.

Nếu muốn tích hợp: chạy `python etl_pipeline.py run` → collection `day10_kb` có dữ liệu clean → cập nhật `CHROMA_COLLECTION=day10_kb` trong Day 09 agent config.

---

## 6. Sprint 4 — Monitoring, docs & hoàn thiện

### 6a. Tài liệu hoàn thiện

| File | Trạng thái | Nội dung chính |
|------|-----------|----------------|
| `docs/pipeline_architecture.md` | ✅ Hoàn chỉnh | Mermaid diagram + ASCII fallback; bảng ranh giới 5 thành phần; idempotency evidence; liên hệ Day 09 |
| `docs/data_contract.md` | ✅ Hoàn chỉnh (từ Sprint 1) | Source map 4 nguồn; schema; quarantine reason codes; SLA |
| `docs/runbook.md` | ✅ Hoàn chỉnh | 5 mục: Symptom → Detection → Diagnosis → Mitigation → Prevention; lệnh recovery cụ thể |
| `docs/quality_report.md` | ✅ Hoàn chỉnh (Sprint 3) | run_id, before/after bảng, interpret, corruption inject 2 loại |

### 6b. Freshness 2 boundary (Bonus +1)

Cập nhật `monitoring/freshness_check.py` + `etl_pipeline.py`:
- **ingest_boundary**: `latest_exported_at` — tuổi data từ nguồn DB export
- **publish_boundary**: `run_timestamp` — tuổi lần pipeline hoàn thành embed

Log mẫu (sau Sprint 4):
```
freshness_check ingest_boundary=FAIL  {"timestamp": "2026-04-10T08:00:00", "age_hours": 120.8, ...}
freshness_check publish_boundary=PASS {"timestamp": "2026-04-15T08:44:38+00:00", "age_hours": 0.1, ...}
freshness_check overall=FAIL
```

→ Phân biệt rõ: data nguồn cũ 5 ngày (ingest FAIL) nhưng pipeline vừa chạy (publish PASS).

### 6c. Distinction criterion d — rule versioning không hard-code

`transform/cleaning_rules.py` nay đọc `hr_leave_min_effective_date` từ `contracts/data_contract.yaml`:

```python
HR_LEAVE_MIN_EFFECTIVE_DATE = _load_hr_cutoff_from_contract()  # "2026-01-01" từ YAML
```

**Chứng minh inject làm đổi quyết định clean:** Thay `hr_leave_min_effective_date: "2027-01-01"` trong YAML → bản HR `effective_date=2026-04-15` sẽ bị quarantine `stale_hr_policy_effective_date` mà không sửa code Python.

### 6d. Lệnh một dòng end-to-end

```bash
python etl_pipeline.py run
```

### 6e. Peer review 3 câu hỏi (slide Phần E)

| Câu hỏi | Trả lời nhóm |
|---------|-------------|
| Nếu thêm doc mới vào corpus, pipeline cần thay đổi gì? | Thêm `doc_id` vào `ALLOWED_DOC_IDS` trong `cleaning_rules.py` và `allowed_doc_ids` trong `data_contract.yaml`; rerun pipeline |
| Khi nào nên dùng `warn` vs `halt` cho expectation? | `halt` khi vi phạm ảnh hưởng trực tiếp đến correctness (stale content, missing traceability field); `warn` khi là chất lượng advisory không chặn serving |
| Làm thế nào để pipeline trở nên idempotent? | Dùng `chunk_id` ổn định (SHA-256 của nội dung) + `upsert` + prune ids thừa sau mỗi publish |

## 7. Rủi ro còn lại

- Eval dùng keyword-based; chưa có LLM-judge để đánh giá chất lượng ngữ nghĩa sâu hơn.
- `grading_questions.json` chưa public (release 17:00) — chạy `grading_run.py` khi GV phát hành:
  ```bash
  python grading_run.py --out artifacts/eval/grading_run.jsonl
  ```
