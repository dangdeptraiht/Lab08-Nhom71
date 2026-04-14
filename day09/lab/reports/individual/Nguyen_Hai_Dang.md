# Báo Cáo Cá Nhân — Lab Day 09: Multi-Agent Orchestration

**Họ và tên:** Nguyễn Hải Đăng  
**Vai trò trong nhóm:** Supervisor Owner  
**Ngày nộp:** 2026-04-14  

---

## 1. Tôi phụ trách phần nào? (100–150 từ)

Trong buổi Lab Day 09, tôi đảm nhận vai trò **Supervisor Owner**, chịu trách nhiệm chính trong việc thiết kế và triển khai kiến trúc điều phối (orchestration) cho hệ thống multi-agent. 

Cụ thể, tôi đã thực hiện các công việc sau:
- **Thiết kế AgentState:** Định nghĩa cấu trúc `TypedDict` để quản lý dữ liệu xuyên suốt các node trong graph, đảm bảo tính nhất quán giữa supervisor và các workers.
- **Implement Supervisor Node:** Xây dựng logic phân loại câu hỏi (routing) dựa trên từ khóa và mức độ rủi ro (risk flagging).
- **Xây dựng Orchestrator (Option A):** Triển khai luồng xử lý `Supervisor -> Worker -> Synthesis` bằng Python thuần để tối ưu tốc độ và sự đơn giản trong giai đoạn đầu.
- **Tích hợp Human-In-The-Loop (HITL):** Thiết kế node `human_review` để xử lý các trường hợp không xác định hoặc rủi ro cao, đảm bảo hệ thống có sự giám sát của con người khi cần thiết.

Công việc của tôi đóng vai trò là "bộ não" của hệ thống, giúp điều phối yêu cầu từ người dùng đến đúng Worker chuyên biệt (Retrieval hoặc Policy Tool) và tổng hợp kết quả cuối cùng.

**Bằng chứng:**
- File: `graph.py` (Các hàm `supervisor_node`, `build_graph`, `human_review_node`).
- Trace logs trong `artifacts/traces/` thể hiện đúng `supervisor_route` và `route_reason`.

---

## 2. Tôi đã ra một quyết định kỹ thuật gì? (150–200 từ)

**Quyết định:** Sử dụng **Keyword-based Routing kết hợp với Risk Flagging** trong `supervisor_node` thay vì sử dụng LLM Classifier ngay từ đầu.

**Lý do:**
1. **Hiệu năng:** Hệ thống cần phản hồi nhanh. Việc gọi LLM chỉ để phân loại câu hỏi vào 3 categories (`retrieval`, `policy`, `human_review`) sẽ tốn thêm khoảng 800ms - 1.5s mỗi request. Trong khi đó, keyword matching gần như tức thì (~0ms).
2. **Độ tin cậy:** Với tập dữ liệu và các tình huống đã định nghĩa trước trong `worker_contracts.yaml`, bộ từ khóa (`refund`, `SLA`, `P1`, `err-`) đủ bao quát và cho kết quả deterministic (dễ dự đoán), tránh trường hợp LLM phân loại sai do prompt sensitivity.
3. **Chi phí:** Tiết kiệm tokens cho các tác vụ phân loại đơn giản, dành tài nguyên LLM cho phần Synthesis phức tạp hơn.

**Trade-off đã chấp nhận:**
Hệ thống có thể gặp khó khăn với các câu hỏi có ngữ nghĩa phức tạp hoặc không chứa từ khóa đặc trưng. Tuy nhiên, tôi đã bù đắp bằng cách thiết lập **default route** về `retrieval_worker` và cơ chế **Human Review** cho các mã lỗi lạ (`err-`).

**Bằng chứng từ trace:**
Trong file `run_20260414_155326.json`, câu hỏi *"SLA xử lý ticket P1 là bao lâu?"* được phân loại ngay lập tức:
```json
"route_reason": "task contains P1, SLA, or escalation keywords | risk_high flagged",
"supervisor_route": "retrieval_worker"
```

---

## 3. Tôi đã sửa một lỗi gì? (150–200 từ)

**Lỗi:** Logic vòng lặp (Loop) sau khi Human Review được approve.

**Symptom:** Ban đầu, khi `human_review_node` được trigger, hệ thống sẽ dừng lại (pause) nhưng không biết đi đâu tiếp theo sau khi "Human Approved", dẫn đến kết quả trả về bị rỗng hoặc không gọi được worker tiếp theo để lấy bằng chứng.

**Root cause:** Thiếu việc cập nhật lại `supervisor_route` bên trong node `human_review`. Sau khi nhận approve, hệ thống cần được chỉ định quay lại node `retrieval_worker` để lấy context cho câu trả lời.

**Cách sửa:**
Tôi đã cập nhật `human_review_node` để sau khi log approve, nó sẽ ghi đè giá trị `supervisor_route` sang `retrieval_worker` và cập nhật `route_reason` để pipeline chạy tiếp vào luồng retrieval/synthesis.

**Bằng chứng:**
Đoạn code đã sửa trong `graph.py`:
```python
def human_review_node(state: AgentState) -> AgentState:
    # ... logic log approval ...
    state["supervisor_route"] = "retrieval_worker"
    state["route_reason"] += " | human approved -> proceeding to retrieval"
    return state
```
Kết quả trong trace cho thấy luồng đi đúng từ `human_review` sang `retrieval_worker`:
`"workers_called": ["human_review", "retrieval_worker", "synthesis_worker"]`

---

## 4. Tôi tự đánh giá đóng góp của mình (100–150 từ)

**Tôi làm tốt nhất ở điểm nào?**
Tôi đã thiết kế cấu trúc `AgentState` rất bao quát và tuân thủ chặt chẽ `worker_contracts.yaml`, giúp việc tích hợp các Real Workers từ đồng đội ở Sprint 2 diễn ra trơn tru, không gặp lỗi mismatch dữ liệu.

**Tôi làm chưa tốt hoặc còn yếu ở điểm nào?**
Logic routing hiện tại vẫn phụ thuộc nặng vào từ khóa tiếng Việt/Anh. Nếu người dùng nhập câu hỏi quá ngắn hoặc dùng từ lóng, Supervisor có thể route sai.

**Nhóm phụ thuộc vào tôi ở đâu?**
Nếu file `graph.py` và logic Supervisor chưa xong, toàn bộ pipeline sẽ bị block vì đây là entry point duy nhất điều phối dữ liệu qua lại giữa các Workers.

**Phần tôi phụ thuộc vào thành viên khác:**
Tôi phụ thuộc hoàn toàn vào output của `retrieval_worker` và `policy_tool_worker`. Nếu các worker này không trả về đúng schema (ví dụ thiếu `retrieved_chunks`), node Synthesis của tôi sẽ không có dữ liệu để sinh câu trả lời.

---

## 5. Nếu có thêm 2 giờ, tôi sẽ làm gì? (50–100 từ)

Tôi sẽ triển khai **Semantic Router** bằng cách sử dụng Embeddings và Vector Search (như thư viện `semantic-router`) thay vì keyword thuần túy. Lý do là trong trace của câu hỏi `"Cần giúp đỡ gấp"` (không chứa keywords P1/SLA), hệ thống hiện tại đang rơi vào default route. Việc dùng Semantic Router sẽ giúp nhận diện ý định "cần hỗ trợ khẩn cấp" chính xác hơn dựa trên vector distance.

---
*Lưu file này với tên: reports/individual/Nguyen_Hai_Dang.md*
