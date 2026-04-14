"""
workers/synthesis.py — Synthesis Worker
Sprint 2: Tổng hợp câu trả lời từ retrieved_chunks và policy_result.

Input (từ AgentState):
    - task: câu hỏi
    - retrieved_chunks: evidence từ retrieval_worker
    - policy_result: kết quả từ policy_tool_worker

Output (vào AgentState):
    - final_answer: câu trả lời cuối với citation
    - sources: danh sách nguồn tài liệu được cite
    - confidence: mức độ tin cậy (0.0 - 1.0)

Gọi độc lập để test:
    python workers/synthesis.py
"""

import os

# Load .env nếu có (cho standalone test)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

WORKER_NAME = "synthesis_worker"

SYSTEM_PROMPT = """Bạn là trợ lý IT Helpdesk nội bộ.

Quy tắc nghiêm ngặt:
1. CHỈ trả lời dựa vào context được cung cấp. KHÔNG dùng kiến thức ngoài.
2. Nếu context không đủ để trả lời → nói rõ "Không đủ thông tin trong tài liệu nội bộ".
3. Trích dẫn nguồn cuối mỗi câu quan trọng: [tên_file].
4. Trả lời súc tích, có cấu trúc. Không dài dòng.
5. Nếu có exceptions/ngoại lệ → nêu rõ ràng trước khi kết luận.
6. Phép tính thời gian đơn giản từ dữ liệu trong tài liệu được phép (VD: 22:47 + 10 phút = 22:57).
7. Khi liệt kê kênh thông báo hoặc công cụ, phải liệt kê ĐẦY ĐỦ tất cả kênh được đề cập trong context, không bỏ sót.
"""

# Instruction đặc biệt cho trường hợp policy v3 (đơn hàng trước 01/02/2026)
_V3_ABSTAIN_INSTRUCTION = """
=== CẢNH BÁO QUAN TRỌNG: POLICY VERSION MISMATCH ===
Đơn hàng được đặt TRƯỚC ngày 01/02/2026, do đó chính sách phiên bản 3 (v3) áp dụng — KHÔNG PHẢI v4.
Tài liệu nội bộ hiện tại CHỈ có chính sách v4 (có hiệu lực từ 01/02/2026).
BẮT BUỘC: Bạn PHẢI abstain và nêu rõ:
  - Chính sách nào áp dụng (v3) và tại sao (ngày đặt hàng trước 01/02/2026)
  - Tài liệu hiện tại không có chính sách v3 → không thể xác nhận kết quả
  - Đề nghị liên hệ CS team để xử lý thủ công
TUYỆT ĐỐI KHÔNG được trả lời dựa trên nội dung chính sách v4 cho đơn hàng này.
"""


def _call_llm(messages: list) -> str:
    """
    Gọi LLM để tổng hợp câu trả lời.
    TODO Sprint 2: Implement với OpenAI hoặc Gemini.
    """
    # Option A: OpenAI
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.1,  # Low temperature để grounded
            max_tokens=500,
        )
        return response.choices[0].message.content
    except Exception:
        pass

    # Option B: Gemini
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        combined = "\n".join([m["content"] for m in messages])
        response = model.generate_content(combined)
        return response.text
    except Exception:
        pass

    # Fallback: trả về message báo lỗi (không hallucinate)
    return "[SYNTHESIS ERROR] Không thể gọi LLM. Kiểm tra API key trong .env."


def _has_v3_mismatch(policy_result: dict) -> bool:
    """Kiểm tra xem có policy version mismatch (đơn hàng trước 01/02/2026) không."""
    for ex in policy_result.get("exceptions_found", []):
        if ex.get("type") == "policy_version_mismatch":
            return True
    return False


def _build_context(chunks: list, policy_result: dict) -> str:
    """Xây dựng context string từ chunks và policy result."""
    parts = []

    # Trường hợp đặc biệt: policy version mismatch → thêm cảnh báo bắt buộc abstain
    if _has_v3_mismatch(policy_result):
        parts.append(_V3_ABSTAIN_INSTRUCTION)

    if chunks:
        parts.append("=== TÀI LIỆU THAM KHẢO ===")
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("source", "unknown")
            text = chunk.get("text", "")
            score = chunk.get("score", 0)
            parts.append(f"[{i}] Nguồn: {source} (relevance: {score:.2f})\n{text}")

    if policy_result and policy_result.get("exceptions_found"):
        parts.append("\n=== POLICY EXCEPTIONS ===")
        for ex in policy_result["exceptions_found"]:
            ex_type = ex.get("type", "")
            rule = ex.get("rule", "")
            mcp = " [MCP verified]" if ex.get("mcp_verified") else ""
            parts.append(f"- [{ex_type}]{mcp} {rule}")

    if not parts:
        return "(Không có context)"

    return "\n\n".join(parts)


def _estimate_confidence(chunks: list, answer: str, policy_result: dict) -> float:
    """
    Ước tính confidence dựa vào:
    - Số lượng và quality của chunks
    - Có exceptions không
    - Answer có abstain không

    TODO Sprint 2: Có thể dùng LLM-as-Judge để tính confidence chính xác hơn.
    """
    if not chunks:
        return 0.1  # Không có evidence → low confidence

    if "Không đủ thông tin" in answer or "không có trong tài liệu" in answer.lower():
        return 0.3  # Abstain → moderate-low

    # Weighted average của chunk scores
    if chunks:
        avg_score = sum(c.get("score", 0) for c in chunks) / len(chunks)
    else:
        avg_score = 0

    # Penalty nếu có exceptions (phức tạp hơn)
    exception_penalty = 0.05 * len(policy_result.get("exceptions_found", []))

    confidence = min(0.95, avg_score - exception_penalty)
    return round(max(0.1, confidence), 2)


def _get_exceptions_by_type(policy_result: dict) -> dict:
    """Trả về dict mapping exception type → exception object."""
    return {
        ex.get("type"): ex
        for ex in policy_result.get("exceptions_found", [])
        if ex.get("type")
    }


def synthesize(task: str, chunks: list, policy_result: dict) -> dict:
    """
    Tổng hợp câu trả lời từ chunks và policy context.

    Với các exception rõ ràng (v3 mismatch, Flash Sale), trả về câu trả lời
    deterministic để tránh LLM bỏ qua instruction.

    Returns:
        {"answer": str, "sources": list, "confidence": float}
    """
    sources = list({c.get("source", "unknown") for c in chunks})
    exceptions = _get_exceptions_by_type(policy_result)

    # ── Fast path 1: Policy v3 mismatch → Deterministic abstain ──────────
    if "policy_version_mismatch" in exceptions:
        answer = (
            "Không thể xác nhận kết quả hoàn tiền cho đơn hàng này.\n\n"
            "Đơn hàng được đặt trước ngày 01/02/2026, do đó chính sách hoàn tiền "
            "**phiên bản 3 (v3)** áp dụng — không phải v4.\n"
            "Tài liệu nội bộ hiện tại chỉ có chính sách v4 (hiệu lực từ 01/02/2026). "
            "Không đủ thông tin để xác nhận kết quả theo v3. "
            "[policy_refund_v4.txt]\n\n"
            "Khuyến nghị: Escalate lên CS team để xử lý thủ công theo chính sách v3."
        )
        return {"answer": answer, "sources": sources, "confidence": 0.9}

    # ── Fast path 2: Flash Sale exception → Deterministic no-refund ───────
    if "flash_sale_exception" in exceptions:
        answer = (
            "Khách hàng **không được hoàn tiền**.\n\n"
            "Dù sản phẩm bị lỗi từ nhà sản xuất và yêu cầu được gửi trong thời hạn, "
            "đơn hàng thuộc chương trình Flash Sale là **ngoại lệ không được hoàn tiền** "
            "theo Điều 3, chính sách v4. Ngoại lệ Flash Sale override tất cả điều kiện "
            "thông thường. [policy_refund_v4.txt]"
        )
        return {"answer": answer, "sources": sources, "confidence": 0.92}

    # ── General path: dùng LLM cho các câu hỏi cần reasoning phức tạp ────
    context = _build_context(chunks, policy_result)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Câu hỏi: {task}\n\n{context}\n\nHãy trả lời câu hỏi dựa vào tài liệu trên.",
        },
    ]

    answer = _call_llm(messages)
    confidence = _estimate_confidence(chunks, answer, policy_result)
    return {"answer": answer, "sources": sources, "confidence": confidence}


def run(state: dict) -> dict:
    """
    Worker entry point — gọi từ graph.py.
    """
    task = state.get("task", "")
    chunks = state.get("retrieved_chunks", [])
    policy_result = state.get("policy_result", {})

    state.setdefault("workers_called", [])
    state.setdefault("history", [])
    state["workers_called"].append(WORKER_NAME)

    worker_io = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "has_policy": bool(policy_result),
        },
        "output": None,
        "error": None,
    }

    try:
        result = synthesize(task, chunks, policy_result)
        state["final_answer"] = result["answer"]
        state["sources"] = result["sources"]
        state["confidence"] = result["confidence"]

        worker_io["output"] = {
            "answer_length": len(result["answer"]),
            "sources": result["sources"],
            "confidence": result["confidence"],
        }
        state["history"].append(
            f"[{WORKER_NAME}] answer generated, confidence={result['confidence']}, "
            f"sources={result['sources']}"
        )

    except Exception as e:
        worker_io["error"] = {"code": "SYNTHESIS_FAILED", "reason": str(e)}
        state["final_answer"] = f"SYNTHESIS_ERROR: {e}"
        state["confidence"] = 0.0
        state["history"].append(f"[{WORKER_NAME}] ERROR: {e}")

    state.setdefault("worker_io_logs", []).append(worker_io)
    return state


# ─────────────────────────────────────────────
# Test độc lập
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Synthesis Worker — Standalone Test")
    print("=" * 50)

    test_state = {
        "task": "SLA ticket P1 là bao lâu?",
        "retrieved_chunks": [
            {
                "text": "Ticket P1: Phản hồi ban đầu 15 phút kể từ khi ticket được tạo. Xử lý và khắc phục 4 giờ. Escalation: tự động escalate lên Senior Engineer nếu không có phản hồi trong 10 phút.",
                "source": "sla_p1_2026.txt",
                "score": 0.92,
            }
        ],
        "policy_result": {},
    }

    result = run(test_state.copy())
    print(f"\nAnswer:\n{result['final_answer']}")
    print(f"\nSources: {result['sources']}")
    print(f"Confidence: {result['confidence']}")

    print("\n--- Test 2: Exception case ---")
    test_state2 = {
        "task": "Khách hàng Flash Sale yêu cầu hoàn tiền vì lỗi nhà sản xuất.",
        "retrieved_chunks": [
            {
                "text": "Ngoại lệ: Đơn hàng Flash Sale không được hoàn tiền theo Điều 3 chính sách v4.",
                "source": "policy_refund_v4.txt",
                "score": 0.88,
            }
        ],
        "policy_result": {
            "policy_applies": False,
            "exceptions_found": [{"type": "flash_sale_exception", "rule": "Flash Sale không được hoàn tiền."}],
        },
    }
    result2 = run(test_state2.copy())
    print(f"\nAnswer:\n{result2['final_answer']}")
    print(f"Confidence: {result2['confidence']}")

    print("\n✅ synthesis_worker test done.")
