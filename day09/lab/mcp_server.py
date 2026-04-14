"""
mcp_server.py — Real HTTP MCP Server (Sprint 3 Advanced Mode)
Sprint 3: Implement ít nhất 2 MCP tools.

Mô phỏng MCP (Model Context Protocol) interface qua HTTP dùng FastAPI.
Agent (MCP client) gọi HTTP endpoint thay vì hard-code từng API.

Tools available:
    1. search_kb(query, top_k)                         → tìm kiếm Knowledge Base (ChromaDB)
    2. get_ticket_info(ticket_id)                      → tra cứu thông tin ticket (mock data)
    3. check_access_permission(level, requester_role)  → kiểm tra quyền truy cập (Access SOP)
    4. create_ticket(priority, title, description)     → tạo ticket mới (mock)
    5. validate_refund_eligibility(order_date, ...)    → kiểm tra điều kiện hoàn tiền [MỚI]
    6. get_escalation_chain(priority, minutes_elapsed) → tính timeline escalation P1 [MỚI]

Sprint 3 TODO:
    - Option Standard: Sử dụng file này as-is (mock class, gọi qua function)
    - Option Advanced: HTTP server này với FastAPI — đang chạy (bonus +2)

Khởi động server:
    python mcp_server.py
    # Lắng nghe trên http://localhost:8765
    # Docs tự động: http://localhost:8765/docs

Gọi qua HTTP:
    POST http://localhost:8765/tools/list
    POST http://localhost:8765/tools/call
    Body: {"name": "search_kb", "arguments": {"query": "SLA P1", "top_k": 3}}

Hoặc gọi local (không cần HTTP):
    from mcp_server import dispatch_tool, list_tools
"""

import os
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(
    title="Lab Internal MCP Server",
    description="MCP server for CS + IT Helpdesk multi-agent lab (Day 09)",
    version="2.0.0",
)

MCP_PORT = int(os.getenv("MCP_PORT", "8765"))


# ─────────────────────────────────────────────
# Tool Schema Registry
# ─────────────────────────────────────────────

TOOL_SCHEMAS = {
    "search_kb": {
        "name": "search_kb",
        "description": "Tìm kiếm Knowledge Base nội bộ bằng semantic search. Trả về top-k chunks liên quan nhất.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Câu hỏi hoặc keyword cần tìm"},
                "top_k": {"type": "integer", "description": "Số chunks cần trả về", "default": 3},
            },
            "required": ["query"],
        },
    },
    "get_ticket_info": {
        "name": "get_ticket_info",
        "description": "Tra cứu thông tin ticket từ hệ thống Jira nội bộ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "ID ticket (VD: IT-9847, P1-LATEST)"},
            },
            "required": ["ticket_id"],
        },
    },
    "check_access_permission": {
        "name": "check_access_permission",
        "description": "Kiểm tra điều kiện cấp quyền truy cập theo Access Control SOP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "access_level": {"type": "integer", "description": "Level cần cấp (1, 2, hoặc 3)"},
                "requester_role": {"type": "string", "description": "Vai trò của người yêu cầu"},
                "is_emergency": {"type": "boolean", "description": "Có phải khẩn cấp không", "default": False},
            },
            "required": ["access_level", "requester_role"],
        },
    },
    "create_ticket": {
        "name": "create_ticket",
        "description": "Tạo ticket mới trong hệ thống Jira (MOCK — không tạo thật trong lab).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["priority", "title"],
        },
    },
    "validate_refund_eligibility": {
        "name": "validate_refund_eligibility",
        "description": (
            "Kiểm tra điều kiện hoàn tiền dựa trên ngày đặt hàng, ngày yêu cầu, loại sản phẩm và phiên bản policy. "
            "Xử lý temporal scoping (v3 vs v4), ngoại lệ Flash Sale và sản phẩm kỹ thuật số."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_date": {
                    "type": "string",
                    "description": "Ngày xác nhận đơn hàng — định dạng YYYY-MM-DD hoặc DD/MM/YYYY",
                },
                "request_date": {
                    "type": "string",
                    "description": "Ngày yêu cầu hoàn tiền — định dạng YYYY-MM-DD hoặc DD/MM/YYYY",
                },
                "product_type": {
                    "type": "string",
                    "enum": ["physical", "digital", "license_key", "subscription"],
                    "default": "physical",
                    "description": "Loại sản phẩm cần hoàn tiền",
                },
                "is_flash_sale": {
                    "type": "boolean",
                    "default": False,
                    "description": "Đơn hàng có thuộc chương trình Flash Sale không",
                },
            },
            "required": ["order_date", "request_date"],
        },
    },
    "get_escalation_chain": {
        "name": "get_escalation_chain",
        "description": (
            "Tính toán trạng thái escalation SLA đầy đủ: có cần escalate không, ai nhận thông báo, "
            "qua kênh nào, deadline tiếp theo, và SLA có bị breach chưa."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_priority": {
                    "type": "string",
                    "enum": ["P1", "P2"],
                    "description": "Mức độ ưu tiên của ticket",
                },
                "minutes_elapsed": {
                    "type": "integer",
                    "description": "Số phút đã trôi qua kể từ khi ticket được tạo",
                },
                "current_time": {
                    "type": "string",
                    "description": "Timestamp hiện tại dạng ISO (không bắt buộc, mặc định là now)",
                },
            },
            "required": ["ticket_priority", "minutes_elapsed"],
        },
    },
}


# ─────────────────────────────────────────────
# Tool Implementations
# ─────────────────────────────────────────────

def tool_search_kb(query: str, top_k: int = 3) -> dict:
    """
    Tìm kiếm Knowledge Base bằng semantic search.

    TODO Sprint 3: Kết nối với ChromaDB thực.
    Hiện tại: Delegate sang retrieval worker (workers/retrieval.py).
    """
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from workers.retrieval import retrieve_dense
        chunks = retrieve_dense(query, top_k=top_k)
        sources = list({c["source"] for c in chunks})
        return {
            "chunks": chunks,
            "sources": sources,
            "total_found": len(chunks),
        }
    except Exception as e:
        return {
            "chunks": [],
            "sources": [],
            "total_found": 0,
            "error": f"ChromaDB search failed: {e}",
        }


MOCK_TICKETS = {
    "P1-LATEST": {
        "ticket_id": "IT-9847",
        "priority": "P1",
        "title": "API Gateway down — toàn bộ người dùng không đăng nhập được",
        "status": "in_progress",
        "assignee": "nguyen.van.a@company.internal",
        "created_at": "2026-04-13T22:47:00",
        "sla_deadline": "2026-04-14T02:47:00",
        "escalated": True,
        "escalated_to": "Senior Engineer team",
        "notifications_sent": [
            "slack:#incident-p1",
            "email:incident@company.internal",
            "pagerduty:oncall",
        ],
    },
    "IT-1234": {
        "ticket_id": "IT-1234",
        "priority": "P2",
        "title": "Feature login chậm cho một số user",
        "status": "open",
        "assignee": None,
        "created_at": "2026-04-13T09:15:00",
        "sla_deadline": "2026-04-14T09:15:00",
        "escalated": False,
    },
    "IT-9847": {
        "ticket_id": "IT-9847",
        "priority": "P1",
        "title": "API Gateway down — toàn bộ người dùng không đăng nhập được",
        "status": "in_progress",
        "assignee": "nguyen.van.a@company.internal",
        "created_at": "2026-04-13T22:47:00",
        "sla_deadline": "2026-04-14T02:47:00",
        "escalated": True,
        "escalated_to": "Senior Engineer team",
        "notifications_sent": [
            "slack:#incident-p1",
            "email:incident@company.internal",
            "pagerduty:oncall",
        ],
    },
}


def tool_get_ticket_info(ticket_id: str) -> dict:
    """Tra cứu thông tin ticket (mock data)."""
    ticket = MOCK_TICKETS.get(ticket_id.upper())
    if ticket:
        return ticket
    return {
        "error": f"Ticket '{ticket_id}' không tìm thấy trong hệ thống.",
        "available_mock_ids": list(MOCK_TICKETS.keys()),
    }


ACCESS_RULES = {
    1: {
        "required_approvers": ["Line Manager"],
        "emergency_can_bypass": False,
        "note": "Standard user access. Không có emergency bypass.",
    },
    2: {
        "required_approvers": ["Line Manager", "IT Admin"],
        "emergency_can_bypass": True,
        "emergency_bypass_note": (
            "Level 2 có thể cấp tạm thời với approval đồng thời của Line Manager "
            "và IT Admin on-call. Access hết hạn sau 4 giờ."
        ),
        "note": "Elevated access.",
    },
    3: {
        "required_approvers": ["Line Manager", "IT Admin", "IT Security"],
        "emergency_can_bypass": False,
        "note": "Admin access — không có emergency bypass. Phải đủ cả 3 approver.",
    },
}


def tool_check_access_permission(
    access_level: int, requester_role: str, is_emergency: bool = False
) -> dict:
    """Kiểm tra điều kiện cấp quyền theo Access Control SOP."""
    rule = ACCESS_RULES.get(access_level)
    if not rule:
        return {"error": f"Access level {access_level} không hợp lệ. Levels hợp lệ: 1, 2, 3."}

    notes = [rule["note"]]
    can_grant = True

    if is_emergency:
        if rule.get("emergency_can_bypass"):
            notes.append(rule.get("emergency_bypass_note", ""))
        else:
            notes.append(
                f"Level {access_level} KHÔNG có emergency bypass. Phải follow quy trình chuẩn."
            )

    return {
        "access_level": access_level,
        "requester_role": requester_role,
        "can_grant": can_grant,
        "required_approvers": rule["required_approvers"],
        "approver_count": len(rule["required_approvers"]),
        "emergency_override": is_emergency and rule.get("emergency_can_bypass", False),
        "notes": notes,
        "source": "access-control-sop.md",
    }


def tool_create_ticket(priority: str, title: str, description: str = "") -> dict:
    """Tạo ticket mới (MOCK — in log, không tạo thật)."""
    mock_id = f"IT-{9900 + abs(hash(title)) % 99}"
    ticket = {
        "ticket_id": mock_id,
        "priority": priority,
        "title": title,
        "description": description[:200],
        "status": "open",
        "created_at": datetime.now().isoformat(),
        "url": f"https://jira.company.internal/browse/{mock_id}",
        "note": "MOCK ticket — không tồn tại trong hệ thống thật",
    }
    print(f"  [MCP create_ticket] MOCK: {mock_id} | {priority} | {title[:50]}")
    return ticket


def _parse_date(date_str: str) -> datetime:
    """Parse date from YYYY-MM-DD or DD/MM/YYYY."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: '{date_str}'. Use YYYY-MM-DD or DD/MM/YYYY.")


def tool_validate_refund_eligibility(
    order_date: str,
    request_date: str,
    product_type: str = "physical",
    is_flash_sale: bool = False,
) -> dict:
    """
    Compute refund eligibility based on Policy v4.
    Handles: Flash Sale exception, digital product exception, temporal scoping (v3/v4 boundary),
    and the 7-working-day window.
    """
    try:
        order_dt = _parse_date(order_date)
        request_dt = _parse_date(request_date)
    except ValueError as e:
        return {"error": str(e)}

    days_elapsed = (request_dt - order_dt).days
    working_day_window = 7  # Policy v4, Article 2

    # Exception 1: Flash Sale -- policy_applies=False regardless of other conditions
    if is_flash_sale:
        return {
            "eligible": False,
            "exception_type": "flash_sale_exception",
            "reason": (
                "Flash Sale orders are NOT eligible for refund. "
                "(Policy v4, Article 3: orders using Flash Sale promotional discount are excluded.)"
            ),
            "days_elapsed": days_elapsed,
            "within_window": days_elapsed <= working_day_window,
            "policy_version": "refund_policy_v4",
            "source": "refund-v4.pdf",
        }

    # Exception 2: Digital product
    if product_type in ("digital", "license_key", "subscription"):
        return {
            "eligible": False,
            "exception_type": "digital_product_exception",
            "reason": (
                f"Digital products ({product_type}) are NOT eligible for refund. "
                "(Policy v4, Article 3: license keys, subscriptions excluded.)"
            ),
            "days_elapsed": days_elapsed,
            "within_window": days_elapsed <= working_day_window,
            "policy_version": "refund_policy_v4",
            "source": "refund-v4.pdf",
        }

    # Temporal scoping: Policy v4 effective 2026-02-01
    POLICY_V4_DATE = datetime(2026, 2, 1)
    if order_dt < POLICY_V4_DATE:
        return {
            "eligible": None,
            "exception_type": "policy_version_mismatch",
            "reason": (
                f"Order placed on {order_dt.strftime('%d/%m/%Y')} (before Policy v4 effective date 01/02/2026). "
                "Policy v3 applies but is NOT available in current documentation. "
                "Escalate to CS team for manual review."
            ),
            "days_elapsed": days_elapsed,
            "within_window": days_elapsed <= working_day_window,
            "policy_version": "refund_policy_v3 (not in docs)",
            "source": "refund-v4.pdf",
            "action_required": "manual_cs_review",
        }

    # Standard eligibility: within 7 working days
    within_window = 0 <= days_elapsed <= working_day_window
    return {
        "eligible": within_window,
        "exception_type": None,
        "reason": (
            f"Request submitted {days_elapsed} day(s) after order confirmation. "
            f"Policy v4 window is {working_day_window} working days. "
            + ("Eligible." if within_window else "Window exceeded -- not eligible.")
        ),
        "days_elapsed": days_elapsed,
        "within_window": within_window,
        "policy_version": "refund_policy_v4",
        "source": "refund-v4.pdf",
    }


# SLA rules from sla-p1-2026.pdf
SLA_RULES = {
    "P1": {
        "first_response_min": 15,
        "escalation_trigger_min": 10,
        "resolution_hours": 4,
        "escalation_target": "Senior Engineer",
        "notification_channels": [
            "slack:#incident-p1",
            "email:incident@company.internal",
            "pagerduty:oncall",
        ],
        "stakeholder_update_interval_min": 30,
    },
    "P2": {
        "first_response_min": 120,
        "escalation_trigger_min": 90,
        "resolution_hours": 24,
        "escalation_target": "Team Lead",
        "notification_channels": ["email:it-support@company.internal"],
        "stakeholder_update_interval_min": 120,
    },
}


def tool_get_escalation_chain(
    ticket_priority: str,
    minutes_elapsed: int,
    current_time: str = None,
) -> dict:
    """
    Given a P1/P2 ticket and minutes elapsed, return the full escalation status:
    - Whether escalation threshold is breached
    - Who receives notifications and via which channels
    - Next stakeholder update deadline
    - SLA resolution deadline
    """
    priority = ticket_priority.upper()
    rule = SLA_RULES.get(priority)
    if not rule:
        return {
            "error": f"Unknown priority '{ticket_priority}'. Supported: P1, P2."
        }

    try:
        current_dt = _parse_date(current_time) if current_time else datetime.now()
    except ValueError:
        current_dt = datetime.now()

    should_escalate = minutes_elapsed >= rule["escalation_trigger_min"]
    first_response_breached = minutes_elapsed > rule["first_response_min"]
    resolution_deadline = current_dt + timedelta(hours=rule["resolution_hours"])
    next_update_due = current_dt + timedelta(
        minutes=rule["stakeholder_update_interval_min"]
    )

    # Compute minutes until resolution SLA breach
    remaining_for_resolution = rule["resolution_hours"] * 60 - minutes_elapsed

    return {
        "ticket_priority": priority,
        "minutes_elapsed": minutes_elapsed,
        "should_escalate": should_escalate,
        "escalation_target": rule["escalation_target"] if should_escalate else None,
        "escalation_trigger_at_min": rule["escalation_trigger_min"],
        "notification_channels": rule["notification_channels"],
        "first_response_deadline_min": rule["first_response_min"],
        "first_response_breached": first_response_breached,
        "resolution_deadline": resolution_deadline.isoformat(),
        "remaining_minutes_to_resolution": max(0, remaining_for_resolution),
        "resolution_sla_breached": remaining_for_resolution < 0,
        "next_stakeholder_update": next_update_due.isoformat(),
        "stakeholder_update_interval_min": rule["stakeholder_update_interval_min"],
        "source": "sla-p1-2026.pdf",
    }


# ─────────────────────────────────────────────
# Dispatch Layer (local + HTTP)
# ─────────────────────────────────────────────

TOOL_REGISTRY = {
    "search_kb": tool_search_kb,
    "get_ticket_info": tool_get_ticket_info,
    "check_access_permission": tool_check_access_permission,
    "create_ticket": tool_create_ticket,
    "validate_refund_eligibility": tool_validate_refund_eligibility,
    "get_escalation_chain": tool_get_escalation_chain,
}


def list_tools() -> list:
    """MCP discovery: trả về danh sách tools có sẵn. Tương đương với tools/list trong MCP protocol."""
    return list(TOOL_SCHEMAS.values())


def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    """
    MCP execution local (không qua HTTP). Dùng làm fallback bởi mcp_client.py
    khi HTTP server chưa khởi động.

    Tương đương với tools/call trong MCP protocol.
    """
    if tool_name not in TOOL_REGISTRY:
        return {
            "error": f"Tool '{tool_name}' không tồn tại. Available: {list(TOOL_REGISTRY.keys())}"
        }
    try:
        return TOOL_REGISTRY[tool_name](**tool_input)
    except TypeError as e:
        return {
            "error": f"Invalid input for '{tool_name}': {e}",
            "schema": TOOL_SCHEMAS.get(tool_name, {}).get("inputSchema"),
        }
    except Exception as e:
        return {"error": f"Tool '{tool_name}' failed: {e}"}


# ─────────────────────────────────────────────
# HTTP Endpoints (MCP over HTTP)
# ─────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "Lab Internal MCP Server",
        "version": "2.0.0",
        "protocol": "MCP over HTTP",
        "tools": list(TOOL_REGISTRY.keys()),
        "endpoints": {
            "list_tools": "POST /tools/list",
            "call_tool": "POST /tools/call  body: {name, arguments}",
            "health": "GET /health",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/tools/list")
def http_list_tools():
    """MCP tools/list endpoint."""
    return {"tools": list_tools()}


@app.post("/tools/call")
async def http_call_tool(request: Request):
    """
    MCP tools/call endpoint.
    Body: {"name": "<tool_name>", "arguments": {<tool_input>}}
    Trả về MCP-format response với content array.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Request body JSON không hợp lệ"})

    tool_name = body.get("name")
    tool_input = body.get("arguments", {})

    if not tool_name:
        return JSONResponse(
            status_code=400,
            content={"error": "Thiếu trường 'name' trong request body"},
        )

    result = dispatch_tool(tool_name, tool_input)

    # MCP response format: content array with text items
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str),
            }
        ],
        "isError": "error" in result,
    }


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  Lab MCP Server (Advanced Mode -- Sprint 3)")
    print("=" * 60)
    print(f"  Listening on: http://localhost:{MCP_PORT}")
    print(f"  Docs:         http://localhost:{MCP_PORT}/docs")
    print(f"  Tools:        {list(TOOL_REGISTRY.keys())}")
    print()
    print("  Endpoints:")
    print("    GET  /            -- server info")
    print("    GET  /health      -- health check")
    print("    POST /tools/list  -- list all tools")
    print("    POST /tools/call  -- call a tool")
    print()
    print("  Press Ctrl+C to stop.")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT, log_level="warning")
