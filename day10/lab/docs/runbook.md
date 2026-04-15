# Runbook — Lab Day 10: Data Pipeline Incidents

**Nhóm:** Nhóm 71  
**Cập nhật:** 2026-04-15  
**Collection:** `day10_kb` | **SLA freshness:** 24h (`FRESHNESS_SLA_HOURS=24`)

---

## Symptom

**Incident 1 — Refund window sai:**  
User / agent trả lời "khách hàng có 14 ngày làm việc để hoàn tiền" thay vì **7 ngày**. Câu trả lời nghe hợp lý nhưng sai policy v4.

**Incident 2 — HR policy version cũ:**  
Agent trả lời "nhân viên được 10 ngày phép năm" thay vì **12 ngày** (chính sách 2026). Bản 2025 stale lọt vào index.

**Incident 3 — Freshness FAIL:**  
Log pipeline ghi `freshness_check=FAIL` với `age_hours > 24`. Data cũ có thể chưa phản ánh cập nhật policy mới nhất.

---

## Detection

| Metric / check | Giá trị báo lỗi | Công cụ |
|----------------|----------------|---------|
| `expectation[refund_no_stale_14d_window]` | `FAIL (halt) :: violations=1` | `artifacts/logs/run_*.log` |
| `expectation[hr_leave_no_stale_10d_annual]` | `FAIL (halt) :: violations=1` | `artifacts/logs/run_*.log` |
| `eval hits_forbidden` | `yes` trên `q_refund_window` hoặc `q_leave_version` | `artifacts/eval/before_after_eval.csv` |
| `freshness_check ingest_boundary` | `FAIL age_hours=120` | Log pipeline hoặc `etl_pipeline.py freshness` |
| `freshness_check publish_boundary` | `FAIL age_hours>24` | Log pipeline |
| `skipped_validate: true` trong manifest | Pipeline đã bỏ qua halt — **chỉ dùng inject demo** | `artifacts/manifests/manifest_*.json` |

```bash
# Kiểm tra nhanh eval hiện tại:
python eval_retrieval.py --out artifacts/eval/check_now.csv
cat artifacts/eval/check_now.csv

# Kiểm tra freshness theo manifest gần nhất:
python etl_pipeline.py freshness --manifest artifacts/manifests/manifest_sprint3-clean.json
```

---

## Diagnosis

| Bước | Việc làm | Kết quả mong đợi |
|------|----------|------------------|
| 1 | Mở `artifacts/manifests/manifest_*.json` gần nhất; kiểm tra `no_refund_fix`, `skip_hr_stale`, `skipped_validate` | Nếu bất kỳ field nào là `true` → pipeline chạy ở inject mode |
| 2 | Mở `artifacts/logs/run_*.log` tương ứng; tìm dòng `expectation[...] FAIL` | Xác định rule nào fail: `refund_no_stale_14d_window` hay `hr_leave_no_stale_10d_annual` |
| 3 | Mở `artifacts/quarantine/*.csv`; đếm `reason=stale_hr_policy_effective_date` | Nếu count=0 → HR stale chưa bị quarantine (inject mode hoặc thiếu rule) |
| 4 | Chạy `python eval_retrieval.py --out artifacts/eval/diag_$(date +%s).csv` | Kiểm tra `hits_forbidden` trên `q_refund_window` và `q_leave_version` |
| 5 | Kiểm tra `embed_prune_removed` trong log | Nếu = 0 sau rerun, index đã clean; nếu > 0, stale vectors vừa được xóa |

**Root cause phổ biến:**

```
1. Pipeline chạy với --no-refund-fix hoặc --skip-hr-stale (inject mode)
   → Manifest field no_refund_fix=true / skip_hr_stale=true

2. CSV nguồn bị cập nhật sai (upstream lỗi, migration v3→v4 không đủ)
   → Expectation fail khi không có inject flag

3. Rerun chưa xảy ra sau khi fix upstream
   → latest_exported_at cũ / run_timestamp cũ → freshness FAIL
```

---

## Mitigation

**Bước 1 — Rerun pipeline chuẩn (không inject flags):**

```bash
# Pipeline chuẩn: fix refund 14→7, quarantine HR stale, validate, embed
python etl_pipeline.py run --run-id recovery-$(date +%Y%m%dT%H%M)
```

Kỳ vọng sau recovery:
- `expectation[refund_no_stale_14d_window] OK`
- `expectation[hr_leave_no_stale_10d_annual] OK`
- `embed_prune_removed > 0` (xóa stale vectors từ run lỗi)
- `PIPELINE_OK`

**Bước 2 — Xác nhận eval sau fix:**

```bash
python eval_retrieval.py --out artifacts/eval/after_recovery.csv
```

Kỳ vọng: `q_refund_window hits_forbidden=no`, `q_leave_version hits_forbidden=no`.

**Bước 3 — Nếu freshness vẫn FAIL (data nguồn cũ):**

- Liên hệ team DB/upstream để re-export với `exported_at` mới.
- Tạm thời: tăng `FRESHNESS_SLA_HOURS` trong `.env` nếu đây là data snapshot có chủ đích (VD demo lab: đặt `FRESHNESS_SLA_HOURS=200`).
- Ghi rõ trong manifest / runbook tại sao FAIL là "hợp lý và có chủ đích" (như CSV mẫu lab, `exported_at=2026-04-10`).

**Rollback embed (nếu cần khôi phục index trước đó):**

```bash
# Xóa collection hiện tại và rerun từ cleaned CSV cũ đã lưu
# (chỉ khi có cleaned CSV từ run tốt trước đó)
python etl_pipeline.py run --raw artifacts/cleaned/cleaned_sprint3-clean.csv \
  --run-id rollback-$(date +%Y%m%dT%H%M)
```

---

## Prevention

1. **Expectation halt tự động:** E3 (`refund_no_stale_14d_window`) và E6 (`hr_leave_no_stale_10d_annual`) là `halt` → pipeline **không embed** nếu còn stale content (trừ khi `--skip-validate` demo).

2. **Cutoff từ contract:** `hr_leave_min_effective_date` đọc từ `contracts/data_contract.yaml` thay vì hardcode → thay đổi version chỉ cần cập nhật YAML, không sửa code.

3. **Freshness 2 boundary:** Log `freshness_check ingest_boundary` + `publish_boundary` sau mỗi run; cảnh báo sớm khi nguồn DB chậm export.

4. **Prune stale vectors:** Mỗi run prune `prev_ids − current_ids` → không bao giờ để vector lạc hậu tồn tại trong index sau publish.

5. **Alert kênh:** `slack:#data-ops-alert` (định nghĩa trong `contracts/data_contract.yaml › freshness.alert_channel`) — trigger khi `freshness_check=FAIL` hoặc expectation halt trong production.

6. **Peer review eval:** Sau mỗi thay đổi policy upstream, chạy `eval_retrieval.py` và kiểm tra `hits_forbidden=no` trên bộ golden questions trước khi đưa vào serving — nối sang Day 11 guardrail nếu có.
