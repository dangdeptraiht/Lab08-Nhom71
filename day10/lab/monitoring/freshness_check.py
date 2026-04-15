"""
Kiểm tra freshness từ manifest pipeline — đo 2 boundary: ingest + publish.

Bonus criterion: freshness đo ở 2 boundary (ingest + publish) có log minh chứng.
  - ingest_boundary:  latest_exported_at  — khi nguồn DB export raw CSV
  - publish_boundary: run_timestamp       — khi pipeline hoàn thành, embed xong

Sinh viên mở rộng: đọc watermark DB, so sánh với clock batch, v.v.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple


def parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # Cho phép "2026-04-10T08:00:00" không có timezone
        if ts.endswith("Z"):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _boundary_status(age_hours: float, sla_hours: float) -> str:
    return "PASS" if age_hours <= sla_hours else "FAIL"


def check_manifest_freshness(
    manifest_path: Path,
    *,
    sla_hours: float = 24.0,
    now: datetime | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Trả về ("PASS" | "WARN" | "FAIL", detail dict).

    Đọc trường `latest_exported_at` hoặc max exported_at trong cleaned summary.
    Hàm gốc giữ nguyên để tương thích ngược; dùng check_manifest_freshness_dual
    để đo cả 2 boundary.
    """
    now = now or datetime.now(timezone.utc)
    if not manifest_path.is_file():
        return "FAIL", {"reason": "manifest_missing", "path": str(manifest_path)}

    data: Dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    ts_raw = data.get("latest_exported_at") or data.get("run_timestamp")
    dt = parse_iso(str(ts_raw)) if ts_raw else None
    if dt is None:
        return "WARN", {"reason": "no_timestamp_in_manifest", "manifest": data}

    age_hours = (now - dt).total_seconds() / 3600.0
    detail = {
        "latest_exported_at": ts_raw,
        "age_hours": round(age_hours, 3),
        "sla_hours": sla_hours,
    }
    if age_hours <= sla_hours:
        return "PASS", detail
    return "FAIL", {**detail, "reason": "freshness_sla_exceeded"}


def check_manifest_freshness_dual(
    manifest_path: Path,
    *,
    sla_hours: float = 24.0,
    now: datetime | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Bonus: đo freshness ở 2 boundary (ingest + publish).

    - ingest_boundary:  latest_exported_at — tuổi data từ nguồn DB
    - publish_boundary: run_timestamp      — tuổi lần pipeline chạy gần nhất

    Trả về overall status = worst(ingest, publish).
    detail dict có 2 sub-dict: ingest_boundary và publish_boundary.
    """
    now = now or datetime.now(timezone.utc)
    if not manifest_path.is_file():
        return "FAIL", {"reason": "manifest_missing", "path": str(manifest_path)}

    data: Dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

    # --- Ingest boundary: latest_exported_at (khi nguồn export raw) ---
    ingest_ts = data.get("latest_exported_at", "")
    ingest_dt = parse_iso(str(ingest_ts)) if ingest_ts else None
    if ingest_dt:
        ingest_age = round((now - ingest_dt).total_seconds() / 3600.0, 3)
        ingest_status = _boundary_status(ingest_age, sla_hours)
        ingest_detail: Dict[str, Any] = {
            "timestamp": ingest_ts,
            "age_hours": ingest_age,
            "sla_hours": sla_hours,
            "status": ingest_status,
        }
        if ingest_status == "FAIL":
            ingest_detail["reason"] = "freshness_sla_exceeded"
    else:
        ingest_status = "WARN"
        ingest_detail = {"status": "WARN", "reason": "no_latest_exported_at"}

    # --- Publish boundary: run_timestamp (khi pipeline selesai embed) ---
    publish_ts = data.get("run_timestamp", "")
    publish_dt = parse_iso(str(publish_ts)) if publish_ts else None
    if publish_dt:
        publish_age = round((now - publish_dt).total_seconds() / 3600.0, 3)
        publish_status = _boundary_status(publish_age, sla_hours)
        publish_detail: Dict[str, Any] = {
            "timestamp": publish_ts,
            "age_hours": publish_age,
            "sla_hours": sla_hours,
            "status": publish_status,
        }
        if publish_status == "FAIL":
            publish_detail["reason"] = "freshness_sla_exceeded"
    else:
        publish_status = "WARN"
        publish_detail = {"status": "WARN", "reason": "no_run_timestamp"}

    # --- Overall: worst of ingest + publish ---
    priority = {"FAIL": 2, "WARN": 1, "PASS": 0}
    overall = max(ingest_status, publish_status, key=lambda s: priority[s])

    return overall, {
        "ingest_boundary": ingest_detail,
        "publish_boundary": publish_detail,
        "overall": overall,
        "run_id": data.get("run_id", ""),
    }
