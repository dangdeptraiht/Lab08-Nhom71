# Báo Cáo Nhóm — Lab Day 09: Multi-Agent Orchestration

**Tên nhóm:** Nhóm 71  
**Thành viên:**
| Tên | Vai trò | Email |
|-----|---------|-------|
| Nguyễn Hải Đăng | Supervisor Owner (Sprint 1) |  |
| Ngô Văn Long | Worker Owner (Sprint 2) |   |
| Nguyễn Mạnh Phú | MCP Owner (Sprint 3) + Retrieval Enhancement |   |
| Nguyễn Phương Linh | Trace & Docs Owner (Sprint 4) |   |

**Ngày nộp:** 14/04/2026  
**Repo:** Lab08-Nhom71  
**Độ dài khuyến nghị:** 600–1000 từ

---

> **Hướng dẫn nộp group report:**
> 
> - File này nộp tại: `reports/group_report.md`
> - Deadline: Được phép commit **sau 18:00** (xem SCORING.md)
> - Tập trung vào **quyết định kỹ thuật cấp nhóm** — không trùng lặp với individual reports
> - Phải có **bằng chứng từ code/trace** — không mô tả chung chung
> - Mỗi mục phải có ít nhất 1 ví dụ cụ thể từ code hoặc trace thực tế của nhóm

---

## 1. Kiến trúc nhóm đã xây dựng (150–200 từ)

> Mô tả ngắn gọn hệ thống nhóm: bao nhiêu workers, routing logic hoạt động thế nào,
> MCP tools nào được tích hợp. Dùng kết quả từ `docs/system_architecture.md`.

**Hệ thống tổng quan:**

Hệ thống được thiết kế với 1 node điều phối (Supervisor) chịu trách nhiệm định tuyến yêu cầu cho 3 workers chuyên dụng (Retrieval Worker, Policy Tool Worker và Synthesis Worker) cộng thêm 1 node Human Review. Mỗi worker có quy định input/output rõ ràng thông qua `AgentState`, hỗ trợ HTTP fallback client đến MCP server bằng FastAPI.

**Routing logic cốt lõi:**
> Mô tả logic supervisor dùng để quyết định route (keyword matching, LLM classifier, rule-based, v.v.)

Logic routing do Supervisor thực hiện qua *Keyword-based Routing kết hợp với Risk Flagging*. Các từ khóa như `refund`, `SLA`, `P1`, `err-` sẽ điều hướng request vào các ngách chuyên biệt (chẳng hạn `policy_tool_worker` hoặc chuyển vào node `human_review`). Việc không dùng LLM-classifier giúp giảm latency ~800ms-1.5s mỗi câu hỏi và tạo tính ổn định (deterministic).

**MCP tools đã tích hợp:**
> Liệt kê tools đã implement và 1 ví dụ trace có gọi MCP tool.

Dựa trên thiết kế HTTP server FastAPI, 3 tools đã được expose dưới REST endpoints:
- `validate_refund_eligibility`: Xác định điều kiện hoàn tiền cho các phiên bản policy.
- `check_policy_version`: Kiểm thử sự mâu thuẫn chính sách theo thời gian.
- `get_sla_rules`: Cập nhật logic SLA cho các ticket phức tạp. 

*Ví dụ Trace gọi MCP tool (gq02 - `policy_version_mismatch`):* MCP tool `validate_refund_eligibility` được gọi thành công, kích hoạt bypass hard-coded trả về abstain với confidence 0.2 nếu policy mismatch.

---

## 2. Quyết định kỹ thuật quan trọng nhất (200–250 từ)

> Chọn **1 quyết định thiết kế** mà nhóm thảo luận và đánh đổi nhiều nhất.
> Phải có: (a) vấn đề gặp phải, (b) các phương án cân nhắc, (c) lý do chọn phương án đã chọn.

**Quyết định:** Sử dụng **Reciprocal Rank Fusion (RRF)** để kết hợp Dense Retrieval và BM25 Sparse Retrieval (Hybrid Retrieval) cho Retrieval Worker.

**Bối cảnh vấn đề:**
Trong các câu hỏi có các từ khóa đặc biệt như "PagerDuty", "SLA P1", Dense-only search vô tình "trung bình hóa" mất các khái niệm kỹ thuật và khiến chunk chứa đúng thông tin tàng hình khỏi top-k results. Khiến kết quả tìm kiếm thiếu sót do từ đồng định thấp.

**Các phương án đã cân nhắc:**

| Phương án | Ưu điểm | Nhược điểm |
|-----------|---------|-----------|
| Dense-only embedding | Nhanh, không cần index riêng, code ngắn gọn | Bỏ sót các từ khóa kỹ thuật chuyên biệt (VD: PagerDuty) ở các câu như gq09. |
| Re-ranking qua Cross-Encoder | Khớp ngữ cảnh chính xác cao nhất | Thêm model cồng kềnh (tải model ~400MB) vào node, chậm hệ thống, dễ crash. |
| RRF Merge (Dense + BM25) | Bắt keyword chính xác, ko cần model | RRF score nhỏ, khác thang độ của confidence score, cần logic code xử lý tỉ mỉ hơn. |

**Phương án đã chọn và lý do:**
Nhóm lựa chọn phương án **RRF Merge (Dense + BM25)**. BM25 bù đắp chuẩn xác khả năng bắt keyword (TF-IDF cao) của tên các kênh thông báo/khái niệm chuyên ngành, rank đúng bài viết số 1. Trong khi Dense bù đắp sức mạnh tìm kiếm tương đồng về vector. Để giải quyết khác biệt RRF score, nhóm truyền biến ẩn `dense_score` riêng để tính log confidence.

**Bằng chứng từ trace/code:**
```python
# workers/retrieval.py — _rrf_merge() do Phú implement:
rrf_scores[key] = {
    "chunk": chunk,
    "rrf": 0.0,
    "dense_score": chunk.get("score", 0.0),  # preserve cosine score for logic
}
# Trace RRF với BM25 lên TOP #1:
# [1.0] sla-p1-2026.pdf — Phần 4: Công cụ và kênh thông báo [PagerDuty]
```

---

## 3. Kết quả grading questions (150–200 từ)

> Sau khi chạy pipeline với grading_questions.json (public lúc 17:00):
> - Nhóm đạt bao nhiêu điểm raw?
> - Câu nào pipeline xử lý tốt nhất?
> - Câu nào pipeline fail hoặc gặp khó khăn?

**Tổng điểm raw ước tính:** 82 / 96 

**Câu pipeline xử lý tốt nhất:**
- ID: gq02 — Lý do tốt: Sinh được abstain với confidence=0.2 vì Worker Worker xử lý `policy_version_mismatch` thông qua gọi MCP tool thành công (`validate_refund_eligibility`).

**Câu pipeline fail hoặc partial:**
- ID: gq12 — Fail ở đâu: Kết quả False Positive (thành `policy_applies = False`) vì rule-based pattern matching bắt dính từ "Flash Sale" trong chữ "không phải Flash Sale", Worker không hiểu negation.
  Root cause: Logic keyword trong `analyze_policy()` chỉ check `"flash sale" in text` làm exception kích hoạt sai lệch.
- ID: gq09 — Fail ở đâu: Trình độ Synthesis LLM kém, không mention channel thứ 3 là "PagerDuty" dù Retrieval dùng BM25 mang chunk chứa PagerDuty xếp #1.

**Câu gq07 (abstain):** Nhóm xử lý thế nào?
Nhóm xử lý bóc tách thành công ngoại lệ thông qua Worker Rule-based, trả logic `policy_applies = False`, trigger digital_product_exception và ghi đè confidence ở cuối. Trace run: `"confidence": 0.44`.

**Câu gq09 (multi-hop khó nhất):** Trace ghi được 2 workers không? Kết quả thế nào?
- BM25 retrieve thành công mục `Công cụ và kênh thông báo [PagerDuty]`. 
- Cả `retrieval_worker` và `synthesis_worker` đều chạy thành công. Song, Synthesis bị LLM bỏ sót do không ép LLM list riêng thông tin tools từ nội dung chunk trả về một cách triệt để.

---

## 4. So sánh Day 08 vs Day 09 — Điều nhóm quan sát được (150–200 từ)

> Dựa vào `docs/single_vs_multi_comparison.md` — trích kết quả thực tế.

**Metric thay đổi rõ nhất (có số liệu):**
Latency Routing. Thay vì dùng LLM Classification tốn 800 - 1500ms cho phân loại, Keyword-based Supervisor của Day 09 phân loại dưới ~10ms. Giúp quá trình chạy end-to-end hiệu quả. Hơn nữa, việc sử dụng local HTTP-Fallback của MCP giảm tải việc gọi external.

**Điều nhóm bất ngờ nhất khi chuyển từ single sang multi-agent:**
Tính Decoupling (Tách bạch hoá) của Graph cấu trúc. Day 08 Agent dễ bị confused khi thực hiện multi-tools trong 1 single prompt gọi GPT 4. Sang Day 09, từng Agent giải quyết 1 vấn đề (Rule-Based cho exception, RRF Dense+BM25 cho retrieval) làm lỗi được "Localize" dễ dàng để fix trong các component nhỏ hơn (ví dụ BM25 trailing space bugs được debug cực kì nhanh).

**Trường hợp multi-agent KHÔNG giúp ích hoặc làm chậm hệ thống:**
Sự cồng kềnh trong việc truyền Data. Ở Day 09 State Dictionary được chuyền tiếp xuyên suốt, khiến nếu có một logic worker sai sẽ đẩy luồng vào vực lỗi dây chuyền (Như Human_Review ban đầu khi chưa gán lại state route thì flow chết cứng ở Synthesis). Hơn nữa, những intent ngắn, sai ngữ nghĩa, routing Rule-based lại bị trượt vào Default.

---

## 5. Phân công và đánh giá nhóm (100–150 từ)

> Đánh giá trung thực về quá trình làm việc nhóm.

**Phân công thực tế:**

| Thành viên | Phần đã làm | Sprint |
|------------|-------------|--------|
| Nguyễn Hải Đăng | Xây dựng Supervisor Node (AgentState struct, Logic Routing Tốc độ cao), Human in the Loop (HITL) | Sprint 1 |
| Ngô Văn Long | Build 3 Worker Nodes (`retrieval`, `policy_tool`, `synthesis`): IO contracts, Exception Matching | Sprint 2 |
| Nguyễn Mạnh Phú | Cải tiến Hybrid Retrieval RRF (BM25+Dense), Build API MCP HTTP Server / Client kết nối | Sprint 3 |
| Nguyễn Phương Linh | Xây dựng Trace Logs Format, tổng hợp kiến trúc hệ thống, Test pipeline Integration | Sprint 4 |

**Điều nhóm làm tốt:**
Kiến trúc cực kì Modular: Hầu như việc tích hợp khi kết hợp bài của 3 Sprints diễn ra êm đẹp vì mọi người thống nhất trước Data Contract trong YAML và `AgentState` struct.

**Điều nhóm làm chưa tốt hoặc gặp vấn đề về phối hợp:**
LLM prompt trong Synthesis Worker vẫn chưa thể vắt hết sức mạnh của Retrieval do thiếu metadata chunking. Lỗi Rule-based (không hiểu negation) của Worker và thiếu Semantic Router ở Supervisor làm điểm của lab chưa tròn chĩnh.

**Nếu làm lại, nhóm sẽ thay đổi gì trong cách tổ chức?**
Kiểm soát Regex kĩ lưỡng hơn từ những file tokenization và tăng cường coverage test cases cho việc xử lý ngoại lệ (Negation sentences). Bắt chéo cho nhau check code từ đầu giờ thay vì gom cuối buổi.

---

## 6. Nếu có thêm 1 ngày, nhóm sẽ làm gì? (50–100 từ)

> 1–2 cải tiến cụ thể với lý do có bằng chứng từ trace/scorecard.

1. **Thay Keyword-Router thành Semantic-Router (Fast Vector Caching):**
Xử lý lỗi những câu không chứa chuẩn xác Keywords bị lệch route (người dùng thay vì gõ P1 thì gõ `Giao diện ứng dụng sập`). Sẽ sử dụng `semantic-router` để embedding so trùng intent tốc độ cao.
2. **Metadata Chunking ở Retrieval Worker:**
Thêm tag `section_type: "tools"` vào chunk lúc index. Cập nhật LLM Prompt ở Synthesis list đủ các tools để cứu vãn lỗi đánh rơi PagerDuty (gq09), hứa hẹn +10 đến +16 điểm trên Grading Rubric.

---

*File này lưu tại: `reports/group_report.md`*  
*Commit sau 18:00 được phép theo SCORING.md*
