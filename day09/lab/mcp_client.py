"""
mcp_client.py -- MCP HTTP Client
Sprint 3: Calls the real MCP HTTP server.
Falls back to local dispatch if server is not running.

Usage:
    from mcp_client import call_tool, list_tools, is_server_running

    # Call a tool (tries HTTP first, then local)
    result = call_tool("search_kb", {"query": "SLA P1", "top_k": 3})
    result = call_tool("validate_refund_eligibility", {
        "order_date": "2026-02-10",
        "request_date": "2026-02-15",
        "product_type": "physical",
        "is_flash_sale": False,
    })
    result = call_tool("get_escalation_chain", {
        "ticket_priority": "P1",
        "minutes_elapsed": 12,
    })
"""

import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8765")
MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "3.0"))  # seconds


def is_server_running() -> bool:
    """Quick health-check ping to the MCP server."""
    try:
        import httpx
        r = httpx.get(f"{MCP_SERVER_URL}/health", timeout=MCP_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def list_tools() -> list:
    """
    Discover available tools from the MCP server.
    Falls back to local list_tools() if server is not running.
    """
    try:
        import httpx
        r = httpx.post(f"{MCP_SERVER_URL}/tools/list", timeout=MCP_TIMEOUT)
        r.raise_for_status()
        return r.json().get("tools", [])
    except Exception:
        from mcp_server import list_tools as local_list
        return local_list()


def call_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Call an MCP tool.

    Strategy:
        1. Try HTTP POST to MCP server (http://localhost:8765/tools/call)
        2. If server unavailable, fall back to local dispatch_tool()

    Args:
        tool_name: tool name (e.g. "search_kb", "validate_refund_eligibility")
        tool_input: dict matching the tool's inputSchema

    Returns:
        Tool result dict, always -- never raises.
    """
    # Try HTTP first
    try:
        import httpx
        payload = {"name": tool_name, "arguments": tool_input}
        r = httpx.post(
            f"{MCP_SERVER_URL}/tools/call",
            json=payload,
            timeout=MCP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        content = data.get("content", [])
        if content:
            raw = content[0].get("text", "{}")
            result = json.loads(raw)
            result["_transport"] = "http"
            return result
    except Exception:
        pass

    # Fallback: local in-process dispatch
    from mcp_server import dispatch_tool
    result = dispatch_tool(tool_name, tool_input)
    result["_transport"] = "local"
    return result


def call_tool_with_log(tool_name: str, tool_input: dict) -> dict:
    """
    Wrapper that returns the full MCP log entry format required by the trace spec.

    Returns:
        {
            "tool": str,
            "input": dict,
            "output": dict,
            "error": dict | None,
            "transport": "http" | "local",
            "timestamp": str,
        }
    """
    result = call_tool(tool_name, tool_input)
    transport = result.pop("_transport", "local")
    has_error = "error" in result

    return {
        "tool": tool_name,
        "input": tool_input,
        "output": None if has_error else result,
        "error": {"message": result["error"]} if has_error else None,
        "transport": transport,
        "timestamp": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("  MCP Client -- Connection Test")
    print("=" * 60)
    print(f"  Server URL: {MCP_SERVER_URL}")

    running = is_server_running()
    print(f"  Server running: {running}")
    print(f"  Mode: {'HTTP' if running else 'local fallback'}")

    print("\n--- Tool Discovery ---")
    tools = list_tools()
    for t in tools:
        print(f"  {t['name']}: {t.get('description', '')[:65]}...")

    print("\n--- Test: validate_refund_eligibility ---")
    cases = [
        {
            "label": "Normal refund (5 days, physical)",
            "input": {"order_date": "2026-02-10", "request_date": "2026-02-15",
                      "product_type": "physical", "is_flash_sale": False},
        },
        {
            "label": "Flash Sale order",
            "input": {"order_date": "2026-02-10", "request_date": "2026-02-15",
                      "product_type": "physical", "is_flash_sale": True},
        },
        {
            "label": "License key (digital)",
            "input": {"order_date": "2026-02-10", "request_date": "2026-02-15",
                      "product_type": "license_key", "is_flash_sale": False},
        },
        {
            "label": "Pre-v4 order (31/01/2026)",
            "input": {"order_date": "31/01/2026", "request_date": "07/02/2026",
                      "product_type": "physical", "is_flash_sale": False},
        },
    ]
    for case in cases:
        r = call_tool("validate_refund_eligibility", case["input"])
        print(f"  [{case['label']}]")
        print(f"    eligible={r.get('eligible')}  exception={r.get('exception_type')}  transport={r.get('_transport', r.get('transport', '?'))}")
        print(f"    reason: {str(r.get('reason', ''))[:90]}")

    print("\n--- Test: get_escalation_chain ---")
    for mins, label in [(5, "5 min (no escalation)"), (12, "12 min (escalation triggered)"), (20, "20 min (SLA response breached)")]:
        r = call_tool("get_escalation_chain", {"ticket_priority": "P1", "minutes_elapsed": mins})
        print(f"  [{label}]  escalate={r.get('should_escalate')}  target={r.get('escalation_target')}  breach={r.get('first_response_breached')}")

    print("\n--- Test: search_kb ---")
    r = call_tool("search_kb", {"query": "SLA P1 escalation", "top_k": 2})
    print(f"  found={r.get('total_found')}  sources={r.get('sources')}")

    print("\nDone.")
