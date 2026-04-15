"""
Expectation suite đơn giản (không bắt buộc Great Expectations).

Sinh viên có thể thay bằng GE / pydantic / custom — miễn là có halt có kiểm soát.

=== Expectation mới (Sprint 2 — Nhóm 71) ===

E7 — no_missing_exported_at (halt):
    Không có dòng cleaned nào có exported_at rỗng.
    Tác động đo được: nếu tắt Rule A (apply_missing_exported_at_rule=False) và inject row 14
    (exported_at='') → E7 FAIL halt. Với Rule A bật → quarantine trước expectations → E7 PASS.

E8 — chunk_text_min_length_20 (warn):
    Không có chunk_text nào < 20 chars trong cleaned.
    Tác động đo được: nếu tắt Rule C (apply_short_chunk_rule=False) và inject row 11
    (text='Liên hệ IT.', 11 chars) → E8 FAIL warn. Với Rule C bật → quarantine → E8 PASS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class ExpectationResult:
    name: str
    passed: bool
    severity: str  # "warn" | "halt"
    detail: str


def run_expectations(cleaned_rows: List[Dict[str, Any]]) -> Tuple[List[ExpectationResult], bool]:
    """
    Trả về (results, should_halt).

    should_halt = True nếu có bất kỳ expectation severity halt nào fail.
    """
    results: List[ExpectationResult] = []

    # E1: có ít nhất 1 dòng sau clean
    ok = len(cleaned_rows) >= 1
    results.append(
        ExpectationResult(
            "min_one_row",
            ok,
            "halt",
            f"cleaned_rows={len(cleaned_rows)}",
        )
    )

    # E2: không doc_id rỗng
    bad_doc = [r for r in cleaned_rows if not (r.get("doc_id") or "").strip()]
    ok2 = len(bad_doc) == 0
    results.append(
        ExpectationResult(
            "no_empty_doc_id",
            ok2,
            "halt",
            f"empty_doc_id_count={len(bad_doc)}",
        )
    )

    # E3: policy refund không được chứa cửa sổ sai 14 ngày (sau khi đã fix)
    bad_refund = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "policy_refund_v4"
        and "14 ngày làm việc" in (r.get("chunk_text") or "")
    ]
    ok3 = len(bad_refund) == 0
    results.append(
        ExpectationResult(
            "refund_no_stale_14d_window",
            ok3,
            "halt",
            f"violations={len(bad_refund)}",
        )
    )

    # E4: chunk_text đủ dài (baseline warn — 8 chars)
    short_8 = [r for r in cleaned_rows if len((r.get("chunk_text") or "")) < 8]
    ok4 = len(short_8) == 0
    results.append(
        ExpectationResult(
            "chunk_min_length_8",
            ok4,
            "warn",
            f"short_chunks={len(short_8)}",
        )
    )

    # E5: effective_date đúng định dạng ISO sau clean (phát hiện parser lỏng)
    iso_bad = [
        r
        for r in cleaned_rows
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", (r.get("effective_date") or "").strip())
    ]
    ok5 = len(iso_bad) == 0
    results.append(
        ExpectationResult(
            "effective_date_iso_yyyy_mm_dd",
            ok5,
            "halt",
            f"non_iso_rows={len(iso_bad)}",
        )
    )

    # E6: không còn marker phép năm cũ 10 ngày trên doc HR (conflict version sau clean)
    bad_hr_annual = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "hr_leave_policy"
        and "10 ngày phép năm" in (r.get("chunk_text") or "")
    ]
    ok6 = len(bad_hr_annual) == 0
    results.append(
        ExpectationResult(
            "hr_leave_no_stale_10d_annual",
            ok6,
            "halt",
            f"violations={len(bad_hr_annual)}",
        )
    )

    # NEW E7: không có exported_at rỗng trong cleaned (halt)
    # Tắt Rule A + inject row exported_at='' → E7 FAIL; Rule A bật → E7 PASS luôn.
    bad_exp = [r for r in cleaned_rows if not (r.get("exported_at") or "").strip()]
    ok7 = len(bad_exp) == 0
    results.append(
        ExpectationResult(
            "no_missing_exported_at",
            ok7,
            "halt",
            f"missing_exported_at_count={len(bad_exp)}",
        )
    )

    # NEW E8: chunk_text tối thiểu 20 chars trong cleaned (warn — nghiêm hơn E4)
    # Tắt Rule C + inject short text → E8 FAIL warn; Rule C bật → E8 PASS.
    short_20 = [r for r in cleaned_rows if len((r.get("chunk_text") or "")) < 20]
    ok8 = len(short_20) == 0
    results.append(
        ExpectationResult(
            "chunk_text_min_length_20",
            ok8,
            "warn",
            f"short_chunks_under_20={len(short_20)}",
        )
    )

    halt = any(not r.passed and r.severity == "halt" for r in results)
    return results, halt
