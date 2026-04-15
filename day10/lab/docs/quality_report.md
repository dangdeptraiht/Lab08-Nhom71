# Quality Report — Lab Day 10

**run_id tham chiếu:** `sprint3-clean` (pipeline clean sau inject)  
**run_id inject:** `inject-bad`  
**Ngày:** 2026-04-15  
**Nhóm:** Nhóm 71  
**Artifact:** `artifacts/eval/before_after_eval.csv`

---

## 1. Tóm tắt số liệu

| Chỉ số | inject-bad (`--no-refund-fix --skip-hr-stale --skip-validate`) | sprint3-clean (chuẩn) | Ghi chú |
|--------|---------------------------------------------------------------|----------------------|---------|
| raw_records | 14 | 14 | Cùng nguồn CSV |
| cleaned_records | 8 | 7 | inject-bad +1 (HR stale lọt qua) |
| quarantine_records | 6 | 7 | inject-bad −1 (HR stale bypassed) |
| Expectation halt? | **YES** — 2 fail: `refund_no_stale_14d_window`, `hr_leave_no_stale_10d_annual` | NO — 8/8 PASS | `--skip-validate` bắt buộc để tiếp tục embed inject-bad |
| Chroma vectors | 8 | 7 | inject-bad có thêm 1 HR stale |
| embed_prune_removed | — | 5 (loại stale từ inject-bad) | Prune hoạt động đúng |

---

## 2. Before / after retrieval (bằng chứng)

**File:** [`artifacts/eval/before_after_eval.csv`](../artifacts/eval/before_after_eval.csv)  
**top-k sử dụng:** 5 (khớp với `grading_run.py`)

### Câu hỏi refund window — `q_refund_window`

| Scenario | contains_expected | hits_forbidden | Diễn giải |
|----------|------------------|----------------|-----------|
| **inject-bad** | yes | **YES** | Top-5 chunks chứa "14 ngày làm việc" (chunk stale v3 chưa bị fix) |
| **sprint3-clean** | yes | **NO** | Rule fix_refund_window đã thay "14 ngày" → "7 ngày"; chunk stale không còn trong index |

> **Chứng cứ log inject-bad:**
> ```
> expectation[refund_no_stale_14d_window] FAIL (halt) :: violations=1
> WARN: expectation failed but --skip-validate -> tiep tuc embed ...
> embed_upsert count=8 collection=day10_kb
> ```

### Câu hỏi HR leave version — `q_leave_version` (Merit)

| Scenario | contains_expected | hits_forbidden | top1_doc_expected | Diễn giải |
|----------|------------------|----------------|-------------------|-----------|
| **inject-bad** | yes | **YES** | yes | Top-5 chứa "10 ngày phép năm" (bản HR 2025 bypassed quarantine) |
| **sprint3-clean** | yes | **NO** | yes | Stale HR 2025 bị quarantine `stale_hr_policy_effective_date`; top-1 là bản 2026 (12 ngày) |

> **Chứng cứ log inject-bad:**
> ```
> expectation[hr_leave_no_stale_10d_annual] FAIL (halt) :: violations=1
> ```
> **Chứng cứ quarantine sprint3-clean** (`quarantine_sprint3-clean.csv`):  
> `chunk_id=7, reason=stale_hr_policy_effective_date, effective_date_normalized=2025-01-01`

### Tóm tắt toàn bộ 4 câu hỏi

| question_id | inject-bad contains/forbidden | sprint3-clean contains/forbidden | Kết luận |
|-------------|-------------------------------|----------------------------------|----------|
| q_refund_window | yes / **YES** | yes / **NO** | Fix refund window hoạt động |
| q_p1_sla | yes / no | yes / no | Không ảnh hưởng (SLA data sạch) |
| q_lockout | yes / no | yes / no | Không ảnh hưởng |
| q_leave_version | yes / **YES** | yes / **NO** | HR stale quarantine hoạt động |

---

## 3. Freshness & monitor

**SLA:** 24 giờ (`FRESHNESS_SLA_HOURS=24`, tính từ `latest_exported_at` của CSV).

| Manifest | freshness_check | age_hours | Giải thích |
|----------|----------------|-----------|------------|
| `manifest_sprint1.json` | FAIL | ~120h | CSV mẫu `exported_at=2026-04-10`, cũ 5 ngày |
| `manifest_sprint2.json` | FAIL | ~120h | Cùng nguồn CSV |
| `manifest_inject-bad.json` | FAIL | ~120h | Cùng nguồn CSV |
| `manifest_sprint3-clean.json` | FAIL | ~120h | Cùng nguồn CSV — **hợp lý và có chủ đích** |

**Giải thích FAIL hợp lý:** CSV mẫu lab dùng `exported_at=2026-04-10T08:00:00` để demo SLA. Trong production, trigger cần alert kênh `slack:#data-ops-alert` và nhóm re-export. Xem `docs/runbook.md` — mục Mitigation.

---

## 4. Corruption inject (Sprint 3)

### Cơ chế inject

```bash
python etl_pipeline.py run \
  --run-id inject-bad \
  --no-refund-fix \       # Tắt rule fix 14→7 ngày
  --skip-hr-stale \       # Bypass quarantine HR bản 2025
  --skip-validate         # Bỏ qua halt expectation để embed tiếp
```

### Hai loại corruption được inject

| Loại | Chunk bị inject | Expectation phát hiện | Eval impact |
|------|----------------|----------------------|-------------|
| Stale refund window | `policy_refund_v4` — "14 ngày làm việc" (v3 migration lỗi) | `refund_no_stale_14d_window` FAIL halt | `q_refund_window hits_forbidden=yes` |
| Stale HR policy | `hr_leave_policy` — "10 ngày phép năm" (bản 2025) | `hr_leave_no_stale_10d_annual` FAIL halt | `q_leave_version hits_forbidden=yes` |

### Phát hiện & tracing

1. **Expectation suite** phát hiện trước khi embed (nếu không có `--skip-validate`)
2. **Eval retrieval** chứng minh impact lên chất lượng câu trả lời
3. **Manifest** ghi `no_refund_fix: true`, `skip_hr_stale: true` → truy vết được nguyên nhân
4. **embed_prune_removed=5** khi restore sprint3-clean → xác nhận stale vectors đã bị xóa

---

## 5. Hạn chế & việc chưa làm

- Freshness chỉ đo ở boundary `publish`; chưa đo `ingest` (khi raw CSV được tạo từ DB).
- Eval dùng keyword-based; chưa có LLM-judge để đánh giá chất lượng câu trả lời ngữ nghĩa sâu hơn.
- Chưa có `grading_questions.json` (release 17:00) — chưa chạy `grading_run.py`.
- Rule versioning đọc cutoff từ `contracts/data_contract.yaml` (Distinction d) chưa tích hợp vào code; hiện vẫn so sánh string "2026-01-01" trực tiếp (tuy đã có field `hr_leave_min_effective_date` trong YAML).
