# System Architecture — Lab Day 09

**Nhóm:** Nhóm 71 (AI Vinuni)  
**Ngày:** 14/04/2026  
**Version:** 1.0

---

## 1. Tổng quan kiến trúc

> Mô tả ngắn hệ thống của nhóm: chọn pattern gì, gồm những thành phần nào.

**Pattern đã chọn:** Supervisor-Worker  
**Lý do chọn pattern này (thay vì single agent):**
Hệ thống cần xử lý nhiều loại request đa dạng (RAG thuần, tra cứu thông tin policy, gọi hệ thống MCP). Pattern này giúp tách biệt rõ ràng các nhiệm vụ, tránh nhồi nhét quá nhiều instruction vào 1 prompt (prompt bloat), đồng thời dễ mở rộng khi thêm capability mới.

---

## 2. Sơ đồ Pipeline

**Sơ đồ thực tế của nhóm:**

```text
User Request ("cấp quyền", "SLA", "refund")
     │
     ▼
┌──────────────┐
│  Supervisor  │ (Kiểm tra keyword: P1, refund, err-)
└──────┬───────┘
       │ route_reason, risk_high, needs_tool
   [route_decision]
       │
  ┌────┴────────────────────┐
  │                         │ (policy: hoàn tiền/cấp quyền)
  ▼                         ▼
Retrieval Worker     Policy Tool Worker 
  (ChromaDB)           (Check exception + Gọi MCP: search_kb, get_ticket_info)
  │                         │
  └─────────┬───────────────┘
            │
            ▼
      Synthesis Worker
       (GPT-4o-mini)
            │
            ▼
         Output (answer, confidence, sources)
```

---

## 3. Vai trò từng thành phần

### Supervisor (`graph.py`)

| Thuộc tính | Mô tả |
|-----------|-------|
| **Nhiệm vụ** | Phân tích `task` đầu vào và điều hướng (route) câu hỏi tới Worker phù hợp. |
| **Input** | `task` (user query). |
| **Output** | `supervisor_route`, `route_reason`, `risk_high`, `needs_tool`. |
| **Routing logic** | Rule-based (Keyword matching). Nếu chứa "hoàn tiền", "cấp quyền" -> pass sang `policy_tool_worker`. Nếu có "err-" và risk cao -> `human_review`. |
| **HITL condition** | `risk_high` == True VÀ "err-" có trong câu hỏi. |

### Retrieval Worker (`workers/retrieval.py`)

| Thuộc tính | Mô tả |
|-----------|-------|
| **Nhiệm vụ** | Embed câu hỏi và truy vấn ChromaDB để trả về văn bản nguồn liên quan. |
| **Embedding model** | Trở về SentenceTransformer(all-MiniLM-L6-v2) hoặc OpenAI text-embedding-3-small |
| **Top-k** | 3 |
| **Stateless?** | Yes |

### Policy Tool Worker (`workers/policy_tool.py`)

| Thuộc tính | Mô tả |
|-----------|-------|
| **Nhiệm vụ** | Kiểm tra các ngoại lệ (exception) trong chính sách và lấy thông tin bên ngoài bằng tool. |
| **MCP tools gọi** | `search_kb`, `get_ticket_info` |
| **Exception cases xử lý** | Đơn vị flash sale, Digital product, Sản phẩm đã kích hoạt. |

### Synthesis Worker (`workers/synthesis.py`)

| Thuộc tính | Mô tả |
|-----------|-------|
| **LLM model** | GPT-4o-mini |
| **Temperature** | 0.1 |
| **Grounding strategy** | Weighted average của chunk scores kết hợp với trừ penalty nếu có policy exceptions. |
| **Abstain condition** | Khi context bị thiếu, prompt ép output ra "Không đủ thông tin trong tài liệu nội bộ". |

### MCP Server (`mcp_server.py`)

| Tool | Input | Output |
|------|-------|--------|
| search_kb | query, top_k | chunks, sources |
| get_ticket_info | ticket_id | ticket details |
| check_access_permission | access_level, requester_role | can_grant, approvers |
| create_ticket | priority, title | ticket_id, url |

---

## 4. Shared State Schema

| Field | Type | Mô tả | Ai đọc/ghi |
|-------|------|-------|-----------|
| task | str | Câu hỏi đầu vào | supervisor đọc |
| supervisor_route | str | Worker được chọn | supervisor ghi |
| route_reason | str | Lý do route | supervisor ghi |
| retrieved_chunks | list | Evidence từ retrieval | retrieval ghi, synthesis đọc |
| policy_result | dict | Kết quả kiểm tra policy | policy_tool ghi, synthesis đọc |
| mcp_tools_used | list | Tool calls đã thực hiện | policy_tool ghi |
| final_answer | str | Câu trả lời cuối | synthesis ghi |
| confidence | float | Mức tin cậy | synthesis ghi |
| risk_high | bool | Đánh dấu rủi ro cao | supervisor ghi |
| history | list | Lịch sử xử lí các step | các workers/supervisor ghi |

---

## 5. Lý do chọn Supervisor-Worker so với Single Agent (Day 08)

| Tiêu chí | Single Agent (Day 08) | Supervisor-Worker (Day 09) |
|----------|----------------------|--------------------------|
| Debug khi sai | Khó — không rõ lỗi ở retrieval hay context | Dễ hơn — test từng worker độc lập (vd: mock `policy_result` dict) |
| Thêm capability mới | Phải sửa toàn prompt, dễ bị instruction bloat | Thêm worker/MCP tool riêng cho từng luồng phụ. |
| Routing visibility | Không có | Có hiển thị `route_reason` và `workers_called` trong trace. |

**Nhóm điền thêm quan sát từ thực tế lab:**
Sử dụng worker tách rời giúp team phân thân chia các task cho nhau (như một bạn code retrieval, một bạn code MCP) mà không bị conflict base code.

---

## 6. Giới hạn và điểm cần cải tiến

1. Logic Routing hiện tại dùng rule-based (Keyword matching), nếu người dùng typo sẽ rụng sang luồng default (`retrieval_worker`). Cần dùng 1 LLM classifier nhỏ để phân luồng (vd: dùng mô hình fast như claude-3-haiku).
2. Khi synthesize confidence tính chỉ dựa trên document distance (cosine) và trừ điểm thủ công, chưa sử dụng LLM as Judge nên confidence chưa mang ý nghĩa lý tưởng.
3. Fallback chưa đủ an toàn ở MCP nếu ChromaDB gắt gao timeout.
