# Routing Decisions Log — Lab Day 09

**Nhóm:** Nhóm 71 (AI Vinuni)  
**Ngày:** 14/04/2026

---

## Routing Decision #1

**Task đầu vào:**
> SLA xử lý ticket P1 là bao lâu?

**Worker được chọn:** `retrieval_worker`  
**Route reason (từ trace):** `default route`  
**MCP tools được gọi:** `None`  
**Workers called sequence:** `["retrieval_worker", "synthesis_worker"]`

**Kết quả thực tế:**
- final_answer (ngắn): Câu trả lời được tổng hợp từ 1 chunks liên quan (chứa thông tin về ticket P1 SLA 4 giờ).
- confidence: `0.92`
- Correct routing? **Yes**

**Nhận xét:** 
Câu này hỏi thông tin RAG thông thường (SLA rule), rơi vào default route là chính xác và retrieval_worker sẽ handle mượt.

---

## Routing Decision #2

**Task đầu vào:**
> Khách hàng Flash Sale yêu cầu hoàn tiền vì sản phẩm lỗi — được không?

**Worker được chọn:** `policy_tool_worker`  
**Route reason (từ trace):** `task contains policy/access keyword`  
**MCP tools được gọi:** `search_kb`  
**Workers called sequence:** `["policy_tool_worker", "synthesis_worker"]`

**Kết quả thực tế:**
- final_answer (ngắn): Trường hợp Exception (Flash Sale => không được hoàn tiền theo khoản 3 V4).
- confidence: `0.83`
- Correct routing? **Yes**

**Nhận xét:**
Quyết định chính xác. Keywords `hoàn tiền`, `flash sale` kích hoạt route vào `policy_tool_worker`. Tool này phát hiện ra context flash_sale exception.

---

## Routing Decision #3

**Task đầu vào:**
> Cần cấp quyền Level 3 để khắc phục P1 khẩn cấp err-889. Quy trình là gì?

**Worker được chọn:** `human_review` (HITL)  
**Route reason (từ trace):** `unknown error code + risk_high → human review`  
**MCP tools được gọi:** `None`  
**Workers called sequence:** `["human_review", "retrieval_worker", "synthesis_worker"]`

**Kết quả thực tế:**
- final_answer (ngắn): Auto-approve (mock HITL), trả về SLA guidelines cho level 3.
- confidence: `0.75`
- Correct routing? **Yes**

**Nhận xét:**
Các keyword "cấp quyền", "khẩn cấp" + "err-" (risk_high) đã gọi đúng human review để phê duyệt an toàn.

---

## Tổng kết

### Routing Distribution

| Worker | Số câu được route | % tổng |
|--------|------------------|--------|
| retrieval_worker | 6 | 40% |
| policy_tool_worker | 8 | 53% |
| human_review | 1 | 7% |

### Routing Accuracy

- Câu route đúng: 13 / 15
- Câu route sai: 2 (Các câu dùng từ đồng nghĩa với "hoàn lại", supervisor không bắt được keyword). Đã sửa bằng prompt LLM sau này để catch intent.
- Câu trigger HITL: 1

### Lesson Learned về Routing

1. Rule base rất nhanh nhưng dễ bị lỗi lọt lưới nếu user gõ lóng chữ hoặc dùng đồng nghĩa. Về sau, cần chuyển Routing thành LLM Intent Router.
2. Việc sử dụng State `needs_tool` rất hay để `policy_tool_worker` biết liệu có nên tự search MCP KB mới chưa.

### Route Quality
Thông tin logs của routing reason `f"task contains policy/access keyword"` đủ để biết pipeline nhảy luồng vì điều kiện nào, cần ghi log keyword cụ thể đã kích hoạt thì dễ track hơn.
