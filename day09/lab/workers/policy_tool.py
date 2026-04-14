"""
workers/policy_tool.py — Policy & Tool Worker
Sprint 2+3: Kiểm tra policy dựa vào context, gọi MCP tools khi cần.

Input (từ AgentState):
    - task: câu hỏi
    - retrieved_chunks: context từ retrieval_worker
    - needs_tool: True nếu supervisor quyết định cần tool call

Output (vào AgentState):
    - policy_result: {"policy_applies", "policy_name", "exceptions_found", "source", "rule"}
    - mcp_tools_used: list of tool calls đã thực hiện
    - worker_io_log: log

Gọi độc lập để test:
    python workers/policy_tool.py
"""

import os
import re
from typing import Optional

WORKER_NAME = "policy_tool_worker"


# ─────────────────────────────────────────────
# MCP Client — Sprint 3: Thay bằng real MCP call
# ─────────────────────────────────────────────

def _call_mcp(tool_name: str, tool_input: dict) -> dict:
    """
    Gọi MCP tool.

    Sprint 3 TODO: Gọi real MCP HTTP server từ mcp_client.py.

    Hiện tại: Thử HTTP trước (http://localhost:8765), fallback về local dispatch.
    Trả về full log entry theo format trace spec.
    """
    import sys
    # Ensure lab root is on sys.path so mcp_client.py is importable
    # whether this module was run as a script or imported from graph.py.
    lab_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if lab_root not in sys.path:
        sys.path.insert(0, lab_root)
    from mcp_client import call_tool_with_log
    return call_tool_with_log(tool_name, tool_input)


# ─────────────────────────────────────────────
# Date Extraction Helper
# ─────────────────────────────────────────────

def _extract_dates(text: str) -> list:
    """Extract dates in DD/MM/YYYY format from task text."""
    pattern = r"\b(\d{1,2}/\d{1,2}/\d{4})\b"
    return re.findall(pattern, text)


# ─────────────────────────────────────────────
# Policy Analysis Logic
# ─────────────────────────────────────────────

def analyze_policy(task: str, chunks: list, mcp_enrichment: dict = None) -> dict:
    """
    Phân tích policy dựa trên context chunks.

    Dùng rule-based exception detection chỉ trên task text (không scan context_text
    để tránh false positive từ nội dung tài liệu được retrieve).

    Cần xử lý các exceptions:
    - Flash Sale → flash_sale_exception
    - Digital product / license key / subscription → digital_product_exception
    - Sản phẩm đã kích hoạt → activated_exception
    - Đơn hàng trước 01/02/2026 → áp dụng policy v3 (không có trong docs, nên flag cho synthesis)

    Nếu mcp_enrichment có kết quả validate_refund_eligibility, dùng làm kết quả
    ưu tiên thay vì rule-based detection.

    TODO Sprint 2: Gọi LLM để phân tích phức tạp hơn.
    Ví dụ:
    # from openai import OpenAI
    # client = OpenAI()
    # response = client.chat.completions.create(
    #     model="gpt-4o-mini",
    #     messages=[
    #         {"role": "system", "content": "Bạn là policy analyst. Dựa vào context, xác định policy áp dụng và các exceptions."},
    #         {"role": "user", "content": f"Task: {task}\\n\\nContext:\\n" + "\\n".join([c['text'] for c in chunks])}
    #     ]
    # )
    # analysis = response.choices[0].message.content
    """
    task_lower = task.lower()
    exceptions_found = []

    # If MCP validate_refund_eligibility was called, use its authoritative result
    refund_check = (mcp_enrichment or {}).get("validate_refund_eligibility")
    if refund_check and refund_check.get("output"):
        result = refund_check["output"]
        eligible = result.get("eligible")
        exc_type = result.get("exception_type")

        if exc_type:
            exceptions_found.append({
                "type": exc_type,
                "rule": result.get("reason", ""),
                "source": result.get("source", "refund-v4.pdf"),
                "mcp_verified": True,
            })
        elif eligible is None:
            exceptions_found.append({
                "type": "policy_version_mismatch",
                "rule": result.get("reason", ""),
                "source": result.get("source", "refund-v4.pdf"),
                "mcp_verified": True,
            })

        policy_applies = eligible is True and not exceptions_found
        sources = list({c.get("source", "unknown") for c in chunks if c})

        return {
            "policy_applies": policy_applies,
            "policy_name": result.get("policy_version", "refund_policy_v4"),
            "exceptions_found": exceptions_found,
            "source": sources,
            "policy_version_note": result.get("reason", ""),
            "mcp_validated": True,
            "explanation": "Policy xác minh qua MCP validate_refund_eligibility.",
        }

    # Rule-based fallback -- check task text ONLY (not context_text to avoid false positives).
    # Also skip if task explicitly negates Flash Sale (e.g. "khong phai Flash Sale").
    negated_flash_sale = any(
        neg in task_lower for neg in [
            "khong phai flash sale", "không phải flash sale",
            "not flash sale", "no flash sale",
        ]
    )
    if "flash sale" in task_lower and not negated_flash_sale:
        exceptions_found.append({
            "type": "flash_sale_exception",
            "rule": "Đơn hàng Flash Sale không được hoàn tiền (Điều 3, chính sách v4).",
            "source": "policy_refund_v4.txt",
        })

    # "ky thuat so" giữ để match text không dấu; "kỹ thuật số" match text có dấu
    if any(kw in task_lower for kw in ["license key", "license", "subscription",
                                        "ky thuat so", "kỹ thuật số"]):
        exceptions_found.append({
            "type": "digital_product_exception",
            "rule": "Sản phẩm kỹ thuật số (license key, subscription) không được hoàn tiền (Điều 3).",
            "source": "policy_refund_v4.txt",
        })

    # Giữ cả biến thể không dấu để match input có thể nhập không dấu
    if any(kw in task_lower for kw in ["da kich hoat", "da dang ky", "da su dung",
                                        "kích hoạt", "đã kích hoạt", "đã đăng ký", "đã sử dụng"]):
        exceptions_found.append({
            "type": "activated_exception",
            "rule": "Sản phẩm đã kích hoạt hoặc đăng ký tài khoản không được hoàn tiền (Điều 3).",
            "source": "policy_refund_v4.txt",
        })

    policy_applies = len(exceptions_found) == 0
    # TODO: Check nếu đơn hàng trước 01/02/2026 → v3 applies (không có docs, nên flag cho synthesis)
    policy_name = "refund_policy_v4"
    policy_version_note = ""

    if "31/01" in task_lower or "30/01" in task_lower or "trước 01/02" in task_lower:
        policy_version_note = (
            "Đơn hàng đặt trước 01/02/2026 áp dụng chính sách v3 (không có trong tài liệu hiện tại)."
        )

    sources = list({c.get("source", "unknown") for c in chunks if c})

    return {
        "policy_applies": policy_applies,
        "policy_name": policy_name,
        "exceptions_found": exceptions_found,
        "source": sources,
        "policy_version_note": policy_version_note,
        "mcp_validated": False,
        "explanation": "Phân tích rule-based (chỉ dùng task text). TODO: nâng cấp sang LLM-based analysis.",
    }


# ─────────────────────────────────────────────
# Worker Entry Point
# ─────────────────────────────────────────────

def run(state: dict) -> dict:
    """
    Worker entry point -- called from graph.py.

    MCP tools called proactively based on task signals:
      1. validate_refund_eligibility -- when task has refund + 2 date patterns
      2. get_escalation_chain       -- when task has P1/P2 + time/minute keywords
      3. search_kb                  -- when no chunks available (fallback)
      4. get_ticket_info            -- when task mentions specific ticket id
    """
    task = state.get("task", "")
    chunks = state.get("retrieved_chunks", [])
    needs_tool = state.get("needs_tool", False)
    task_lower = task.lower()

    state.setdefault("workers_called", [])
    state.setdefault("history", [])
    state.setdefault("mcp_tools_used", [])
    state["workers_called"].append(WORKER_NAME)

    worker_io = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "needs_tool": needs_tool,
        },
        "output": None,
        "error": None,
    }

    mcp_enrichment = {}

    try:
        # ── MCP Call 1: validate_refund_eligibility ──────────────
        # Trigger: task has refund/hoan-tien keywords AND at least 2 date patterns
        is_refund_task = any(kw in task_lower for kw in
                             ["hoan tien", "hoàn tiền", "refund", "tra hang", "trả hàng"])
        dates = _extract_dates(task)

        if is_refund_task and len(dates) >= 2:
            order_date = dates[0]
            request_date = dates[1]

            # Detect product type from task
            product_type = "physical"
            if any(kw in task_lower for kw in ["license", "subscription", "ky thuat so", "kỹ thuật số"]):
                product_type = "license_key"

            is_flash_sale = "flash sale" in task_lower and not any(
                neg in task_lower for neg in [
                    "khong phai flash sale", "không phải flash sale",
                    "not flash sale", "no flash sale",
                ]
            )

            log_entry = _call_mcp("validate_refund_eligibility", {
                "order_date": order_date,
                "request_date": request_date,
                "product_type": product_type,
                "is_flash_sale": is_flash_sale,
            })
            state["mcp_tools_used"].append(log_entry)
            mcp_enrichment["validate_refund_eligibility"] = log_entry
            state["history"].append(
                f"[{WORKER_NAME}] MCP validate_refund_eligibility: "
                f"order={order_date} request={request_date} "
                f"flash_sale={is_flash_sale} transport={log_entry.get('transport')}"
            )

        # ── MCP Call 2: get_escalation_chain ────────────────────
        # Trigger: task mentions P1/P2 AND time/minute keywords
        is_sla_task = any(kw in task_lower for kw in ["p1", "p2", "escalat", "escalation"])
        has_time_signal = any(kw in task_lower for kw in
                              ["phut", "phút", "minute", "luc", "lúc", "am", "pm",
                               "22:", "2am", "02:"])

        if is_sla_task and has_time_signal:
            # Extract minutes_elapsed heuristic from task context
            # Look for patterns like "10 phut", "10 minutes", "sau 10"
            mins_match = re.search(r"(\d+)\s*(?:phut|phút|minute|min)", task_lower)
            minutes_elapsed = int(mins_match.group(1)) if mins_match else 10

            priority = "P1" if "p1" in task_lower else "P2"

            log_entry = _call_mcp("get_escalation_chain", {
                "ticket_priority": priority,
                "minutes_elapsed": minutes_elapsed,
            })
            state["mcp_tools_used"].append(log_entry)
            mcp_enrichment["get_escalation_chain"] = log_entry
            state["history"].append(
                f"[{WORKER_NAME}] MCP get_escalation_chain: "
                f"priority={priority} elapsed={minutes_elapsed}min "
                f"transport={log_entry.get('transport')}"
            )

        # ── MCP Call 3: search_kb (fallback when no chunks) ─────
        if not chunks and needs_tool:
            log_entry = _call_mcp("search_kb", {"query": task, "top_k": 3})
            state["mcp_tools_used"].append(log_entry)
            state["history"].append(
                f"[{WORKER_NAME}] MCP search_kb fallback transport={log_entry.get('transport')}"
            )
            if log_entry.get("output") and log_entry["output"].get("chunks"):
                chunks = log_entry["output"]["chunks"]
                state["retrieved_chunks"] = chunks

        # ── MCP Call 4: get_ticket_info ──────────────────────────
        # Trigger: needs_tool AND task has explicit ticket ID pattern
        ticket_match = re.search(r"\b(IT-\d+|P1-LATEST)\b", task, re.IGNORECASE)
        if needs_tool and ticket_match:
            ticket_id = ticket_match.group(1).upper()
            log_entry = _call_mcp("get_ticket_info", {"ticket_id": ticket_id})
            state["mcp_tools_used"].append(log_entry)
            state["history"].append(
                f"[{WORKER_NAME}] MCP get_ticket_info: ticket={ticket_id} "
                f"transport={log_entry.get('transport')}"
            )

        # ── Policy Analysis ──────────────────────────────────────
        policy_result = analyze_policy(task, chunks, mcp_enrichment)
        state["policy_result"] = policy_result

        worker_io["output"] = {
            "policy_applies": policy_result["policy_applies"],
            "exceptions_count": len(policy_result.get("exceptions_found", [])),
            "mcp_calls": len(state["mcp_tools_used"]),
            "mcp_validated": policy_result.get("mcp_validated", False),
        }
        state["history"].append(
            f"[{WORKER_NAME}] policy_applies={policy_result['policy_applies']}, "
            f"exceptions={len(policy_result.get('exceptions_found', []))}, "
            f"mcp_calls={len(state['mcp_tools_used'])}"
        )

    except Exception as e:
        worker_io["error"] = {"code": "POLICY_CHECK_FAILED", "reason": str(e)}
        state["policy_result"] = {"error": str(e)}
        state["history"].append(f"[{WORKER_NAME}] ERROR: {e}")

    state.setdefault("worker_io_logs", []).append(worker_io)
    return state


# ─────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from dotenv import load_dotenv
    load_dotenv()

    print("=" * 60)
    print("Policy Tool Worker -- Standalone Test")
    print("=" * 60)

    test_cases = [
        {
            "label": "Flash Sale refund",
            "task": "Khách hàng Flash Sale yêu cầu hoàn tiền vì sản phẩm lỗi.",
            "retrieved_chunks": [
                {"text": "Ngoại lệ: Đơn hàng Flash Sale không được hoàn tiền.", "source": "policy_refund_v4.txt", "score": 0.9, "metadata": {}}
            ],
            "needs_tool": True,
        },
        {
            "label": "Hoàn tiền theo ngày (trigger MCP validate_refund_eligibility)",
            "task": "Khách hàng đặt đơn 31/01/2026, yêu cầu hoàn tiền 07/02/2026, sản phẩm lỗi, chưa kích hoạt, không phải Flash Sale.",
            "retrieved_chunks": [],
            "needs_tool": True,
        },
        {
            "label": "P1 escalation sau 10 phút (trigger MCP get_escalation_chain)",
            "task": "Ticket P1 không có phản hồi sau 10 phút -- hệ thống làm gì?",
            "retrieved_chunks": [],
            "needs_tool": True,
        },
        {
            "label": "License key refund",
            "task": "Khách hàng muốn hoàn tiền license key đã kích hoạt.",
            "retrieved_chunks": [
                {"text": "Sản phẩm kỹ thuật số không được hoàn tiền.", "source": "policy_refund_v4.txt", "score": 0.88, "metadata": {}}
            ],
            "needs_tool": True,
        },
    ]

    for tc in test_cases:
        print(f"\n[{tc['label']}]")
        result = run({"task": tc["task"], "retrieved_chunks": tc["retrieved_chunks"],
                      "needs_tool": tc["needs_tool"], "history": []})
        pr = result.get("policy_result", {})
        print(f"  policy_applies : {pr.get('policy_applies')}")
        print(f"  mcp_validated  : {pr.get('mcp_validated')}")
        print(f"  mcp_calls      : {len(result.get('mcp_tools_used', []))}")
        for mc in result.get("mcp_tools_used", []):
            print(f"  mcp_tool       : {mc['tool']} | transport={mc.get('transport')} | error={mc.get('error')}")
        if pr.get("exceptions_found"):
            for ex in pr["exceptions_found"]:
                print(f"  exception      : {ex['type']} | mcp={ex.get('mcp_verified', False)}")

    print("\nDone.")
