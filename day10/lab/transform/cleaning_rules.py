"""
Cleaning rules — raw export → cleaned rows + quarantine.

Baseline gồm các failure mode mở rộng (allowlist doc_id, parse ngày, HR stale version).
Sinh viên thêm ≥3 rule mới: mỗi rule phải ghi `metric_impact` (xem README — chống trivial).

=== Rule mới (Sprint 2 — Nhóm 71) ===

Rule A — quarantine_missing_exported_at:
    exported_at rỗng → quarantine reason="missing_exported_at"
    metric_impact: Sprint 1 (before): quarantine=4; Sprint 2 (after, row 14 thêm): quarantine tăng +1.
    Kịch bản inject Sprint 3: bỏ rule này → row 14 lọt vào cleaned → E7 "no_missing_exported_at" FAIL.

Rule B — strip_bom_and_control_chars:
    Strip BOM (U+FEFF) và ký tự điều khiển (0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F) khỏi chunk_text.
    Nếu sau strip text rỗng → quarantine reason="empty_after_bom_strip".
    metric_impact: row 12 (BOM prefix, text 107 chars) → cleaned (BOM stripped);
                   row 13 (BOM-only) → quarantine empty_after_bom_strip.

Rule C — quarantine_chunk_too_short:
    chunk_text < MIN_CHUNK_TEXT_LENGTH (20) → quarantine reason="chunk_too_short".
    metric_impact: row 11 ("Liên hệ IT.", 11 chars) → quarantine.
    Expectation E8 "chunk_text_min_length_20" (warn) sẽ FAIL nếu bỏ rule này + inject short text.

=== Distinction criterion d (Sprint 4 — Nhóm 71) ===

HR_LEAVE_MIN_EFFECTIVE_DATE đọc từ contracts/data_contract.yaml (policy_versioning.hr_leave_min_effective_date)
thay vì hard-code "2026-01-01". Khi policy thay đổi version, chỉ cần cập nhật YAML + rerun pipeline.
Chứng minh inject: thay đổi YAML → clean_rows() dùng cutoff mới → quyết định quarantine thay đổi.
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

def _load_hr_cutoff_from_contract(contract_path: Path | None = None) -> str:
    """
    Distinction criterion d: đọc hr_leave_min_effective_date từ contracts/data_contract.yaml
    thay vì hard-code. Fallback: "2026-01-01" nếu file không tồn tại hoặc field thiếu.

    Khi policy thay đổi (vd sang version 2027-01-01), chỉ cần cập nhật YAML + rerun pipeline
    mà không sửa code Python — rule versioning không hard-code ngày cố định.
    """
    if contract_path is None:
        contract_path = Path(__file__).resolve().parent.parent / "contracts" / "data_contract.yaml"
    try:
        import yaml  # pyyaml trong requirements.txt
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        cutoff = (data or {}).get("policy_versioning", {}).get("hr_leave_min_effective_date", "")
        if cutoff:
            return str(cutoff)
    except Exception:
        pass
    return "2026-01-01"


# Distinction d: cutoff đọc từ YAML, không hard-code.
# Inject test: thay "hr_leave_min_effective_date" trong data_contract.yaml → cutoff thay đổi
# → quyết định quarantine thay đổi mà không sửa code.
HR_LEAVE_MIN_EFFECTIVE_DATE: str = _load_hr_cutoff_from_contract()

# Khớp export hợp lệ trong lab (mở rộng khi nhóm thêm doc mới — phải đồng bộ contract).
ALLOWED_DOC_IDS = frozenset(
    {
        "policy_refund_v4",
        "sla_p1_2026",
        "it_helpdesk_faq",
        "hr_leave_policy",
    }
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

# Rule C: độ dài tối thiểu chunk có nghĩa (chars, sau khi đã strip)
MIN_CHUNK_TEXT_LENGTH = 20

# Rule B: ký tự BOM + control chars không in được (giữ lại \t, \n, \r để xử lý bằng split/strip)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff]")


def _norm_text(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()


def _stable_chunk_id(doc_id: str, chunk_text: str, seq: int) -> str:
    h = hashlib.sha256(f"{doc_id}|{chunk_text}|{seq}".encode("utf-8")).hexdigest()[:16]
    return f"{doc_id}_{seq}_{h}"


def _normalize_effective_date(raw: str) -> Tuple[str, str]:
    """
    Trả về (iso_date, error_reason).
    iso_date rỗng nếu không parse được.
    """
    s = (raw or "").strip()
    if not s:
        return "", "empty_effective_date"
    if _ISO_DATE.match(s):
        return s, ""
    m = _DMY_SLASH.match(s)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}", ""
    return "", "invalid_effective_date_format"


def _strip_bom_and_control(text: str) -> str:
    """
    Rule B: Strip BOM (U+FEFF) và ký tự điều khiển, chuẩn hoá whitespace.
    Trả về text đã clean (có thể rỗng nếu toàn bộ là BOM/control chars).
    """
    cleaned = _CONTROL_CHARS.sub("", text)
    return " ".join(cleaned.strip().split())


def load_raw_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows


def clean_rows(
    rows: List[Dict[str, str]],
    *,
    apply_refund_window_fix: bool = True,
    apply_hr_stale_quarantine: bool = True,
    apply_missing_exported_at_rule: bool = True,
    apply_bom_strip_rule: bool = True,
    apply_short_chunk_rule: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Trả về (cleaned, quarantine).

    Baseline (mở rộng theo narrative Day 10):
    1) Quarantine: doc_id không thuộc allowlist (export lạ / catalog sai).
    2) Chuẩn hoá effective_date sang YYYY-MM-DD; quarantine nếu không parse được.
    3) [NEW Rule A] Quarantine: exported_at rỗng → missing_exported_at.
    4) Quarantine: chunk hr_leave_policy có effective_date < 2026-01-01 (bản HR cũ / conflict version).
       Flag apply_hr_stale_quarantine=False để bypass (chỉ dùng inject Sprint 3).
    5) [NEW Rule B] Strip BOM + control chars khỏi chunk_text; quarantine nếu rỗng sau strip.
    6) Quarantine: chunk_text rỗng hoặc effective_date rỗng sau chuẩn hoá.
    7) [NEW Rule C] Quarantine: chunk_text < MIN_CHUNK_TEXT_LENGTH (20 chars) → chunk_too_short.
    8) Loại trùng nội dung chunk_text (giữ bản đầu).
    9) Fix stale refund: policy_refund_v4 chứa '14 ngày làm việc' → 7 ngày.

    Flags apply_* cho phép tắt từng rule để demo inject / Sprint 3.
    """
    quarantine: List[Dict[str, Any]] = []
    seen_text: set[str] = set()
    cleaned: List[Dict[str, Any]] = []
    seq = 0

    for raw in rows:
        doc_id = raw.get("doc_id", "")
        text = raw.get("chunk_text", "")
        eff_raw = raw.get("effective_date", "")
        exported_at = raw.get("exported_at", "")

        # Rule baseline 1: unknown doc_id
        if doc_id not in ALLOWED_DOC_IDS:
            quarantine.append({**raw, "reason": "unknown_doc_id"})
            continue

        # Rule baseline 2: normalize effective_date
        eff_norm, eff_err = _normalize_effective_date(eff_raw)
        if eff_err == "empty_effective_date":
            quarantine.append({**raw, "reason": "missing_effective_date"})
            continue
        if eff_err == "invalid_effective_date_format":
            quarantine.append({**raw, "reason": eff_err, "effective_date_raw": eff_raw})
            continue

        # NEW Rule A: quarantine_missing_exported_at
        if apply_missing_exported_at_rule and not exported_at.strip():
            quarantine.append({**raw, "reason": "missing_exported_at"})
            continue

        # Rule baseline 3: stale HR policy (tắt để demo inject Sprint 3)
        # Distinction d: dùng HR_LEAVE_MIN_EFFECTIVE_DATE đọc từ YAML, không hard-code "2026-01-01".
        if apply_hr_stale_quarantine and doc_id == "hr_leave_policy" and eff_norm < HR_LEAVE_MIN_EFFECTIVE_DATE:
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_hr_policy_effective_date",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        # NEW Rule B: strip_bom_and_control_chars
        if apply_bom_strip_rule:
            text_stripped = _strip_bom_and_control(text)
            if text_stripped != text:
                # BOM hoặc control chars đã bị loại bỏ
                if not text_stripped:
                    quarantine.append({**raw, "reason": "empty_after_bom_strip"})
                    continue
                text = text_stripped  # Cập nhật text đã clean

        # Rule baseline 4: empty chunk_text
        if not text:
            quarantine.append({**raw, "reason": "missing_chunk_text"})
            continue

        # NEW Rule C: quarantine_chunk_too_short
        if apply_short_chunk_rule and len(text) < MIN_CHUNK_TEXT_LENGTH:
            quarantine.append({**raw, "reason": "chunk_too_short", "chunk_length": len(text)})
            continue

        # Rule baseline 5: dedupe by content
        key = _norm_text(text)
        if key in seen_text:
            quarantine.append({**raw, "reason": "duplicate_chunk_text"})
            continue
        seen_text.add(key)

        # Rule baseline 6: fix stale refund window
        fixed_text = text
        if apply_refund_window_fix and doc_id == "policy_refund_v4":
            if "14 ngày làm việc" in fixed_text:
                fixed_text = fixed_text.replace(
                    "14 ngày làm việc",
                    "7 ngày làm việc",
                )
                fixed_text += " [cleaned: stale_refund_window]"

        seq += 1
        cleaned.append(
            {
                "chunk_id": _stable_chunk_id(doc_id, fixed_text, seq),
                "doc_id": doc_id,
                "chunk_text": fixed_text,
                "effective_date": eff_norm,
                "exported_at": exported_at or "",
            }
        )

    return cleaned, quarantine


def write_cleaned_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at\n", encoding="utf-8")
        return
    fieldnames = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def write_quarantine_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at,reason\n", encoding="utf-8")
        return
    keys: List[str] = []
    seen_k: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen_k:
                seen_k.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)
