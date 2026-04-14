# Single Agent vs Multi-Agent Comparison — Lab Day 09

**Nhóm:** Nhóm 71 (AI Vinuni)  
**Ngày:** 14/04/2026

---

## 1. Metrics Comparison

| Metric | Day 08 (Single Agent) | Day 09 (Multi-Agent) | Delta | Ghi chú |
|--------|----------------------|---------------------|-------|---------|
| Avg confidence | 0.65 | 0.81 | +0.16 | Agent nay có tool/exception handler chắc chắn hơn |
| Avg latency (ms) | 800 ms | 1500 ms | +700ms | Day 09 độ trễ do gọi MCP and thêm hops logic |
| Abstain rate (%) | 5% | 15% | +10% | Đánh giá chính sách chặt chẽ hơn, dễ bắt abstain hơn |
| Multi-hop accuracy | 60% | 85% | +25% | MCP và Graph hỗ trợ ghép evidence từ nhiều luồng |
| Routing visibility | ✗ Không có | ✓ Có route_reason | N/A | Dễ đọc trace JSON |
| Debug time (estimate) | 20 phút | 5 phút | -15 phút | Khoanh vùng chính xác thành phần gây lỗi do tách workers |

---

## 2. Phân tích theo loại câu hỏi

### 2.1 Câu hỏi đơn giản (single-document)

| Nhận xét | Day 08 | Day 09 |
|---------|--------|--------|
| Accuracy | 95% | 95% |
| Latency | ~700ms | ~900ms |
| Observation | Dễ trúng do semantic search RAG truyền thống | Chạy luồng Default `retrieval_worker`, hơi tốn kém extra node hopping nhưng accuracy ngang nhau |

**Kết luận:** Multi-agent có bị overhead trên những tasks quá nhỏ, nhưng không đáng bận tâm lắm.

### 2.2 Câu hỏi multi-hop (cross-document)

| Nhận xét | Day 08 | Day 09 |
|---------|--------|--------|
| Accuracy | 60% | 85% |
| Routing visible? | ✗ | ✓ |
| Observation | LLM prompt hay quên điều kiện policy exception | Policy Worker bắt các exception (Flash Sale) rất triệt để. |

**Kết luận:** Multi-agent tối ưu xuất sắc và giảm tỉ lệ suy luận lỏng lẻo.

### 2.3 Câu hỏi cần abstain

| Nhận xét | Day 08 | Day 09 |
|---------|--------|--------|
| Abstain rate | 5% | 15% |
| Hallucination cases | 3 | 0 |
| Observation | Single RAG Model hay chém nếu context rỗng | Synthesis bị áp rules confidence, worker return 0 chunks -> force abstain tốt |

**Kết luận:** Cải thiện safety score (Low Hallucination).

---

## 3. Debuggability Analysis

### Day 08 — Debug workflow
```
Khi answer sai → phải đọc toàn bộ RAG pipeline prompt/gen code → tìm xem lỗi ở indexing, ở retrieval_k hay ở system prompt context mâu thuẫn.
Không có trace chuẩn. Lần mò.
Thời gian ước tính: 20 phút
```

### Day 09 — Debug workflow
```
Khi answer sai → đọc trace.jsonl → xem supervisor_route + route_reason
  → Nếu route sai (chưa nhảy vào Policy) → Fix keyword rules supervisor.
  → Nếu nhảy vào policy mà sai KQ → chạy func policy_worker_node riêng biệt.
  → Rất chia tách & dễ chịu.
Thời gian ước tính: 5 phút
```

---

## 4. Extensibility Analysis

| Scenario | Day 08 | Day 09 |
|---------|--------|--------|
| Thêm 1 tool/API mới | Cực kì khó, phải update base code prompt & parsers | Chỉ việc define MCP tool schemas `create_ticket` và đưa vào Tool Worker |
| Thêm 1 domain mới | Sửa RAG prompt tổng | Thêm Worker mới (ví dụ HR Worker) |
| Thay đổi retrieval strategy | Sửa trực tiếp trong hàm pipeline bự | Sửa độc lập `workers/retrieval.py` |

**Nhận xét:** Day 09 scalable hơn rất nhiều, thiết kế pattern này lý tưởng cho microservices.

---

## 5. Cost & Latency Trade-off

| Scenario | Day 08 calls | Day 09 calls |
|---------|-------------|-------------|
| Simple query | 1 LLM call | 1 LLM call (nếu route là default retrieval) |
| Complex query | 1 LLM call | 2-3 calls (Ví dụ gọi Tool có calls, Synthesis gọi GPT4) |
| MCP tool call | N/A | >=1 (Tùy theo needs_tool = True) |

**Nhận xét về cost-benefit:** Token cost có cao hơn khoảng x1.5 tới x2 cho complex calls, latency tăng nhẹ, nhưng bù lại tính ổn định (Reliability) và Safety Guard cao gấp nhiều lần. Xứng đáng.

---

## 6. Kết luận

> **Multi-agent tốt hơn single agent ở điểm nào?**
1. Separation of concerns (Phân chia quyền/trách nhiệm, worker riêng biệt).
2. Dễ scale tool (MCP), dễ viết UT (Unit Test) cho từng component.

> **Multi-agent kém hơn hoặc không khác biệt ở điểm nào?**
1. Setup code graph phức tạp ban đầu.
2. Token consumption & latency cao hơn nhẹ.

> **Khi nào KHÔNG nên dùng multi-agent?**
Dự án POC ngắn ngày hỏi đáp file đơn giản, hoặc budget LLM calls cực kỳ eo hẹp, hoặc latency API yêu cầu < 500ms.
