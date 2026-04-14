# Báo Cáo Cá Nhân — Lab Day 09: Multi-Agent Orchestration

**Họ và tên:** Nguyễn Mạnh Phú  
**Vai trò trong nhóm:** MCP Owner (Sprint 3) + Retrieval Enhancement  
**Ngày nộp:** 14/04/2026  
**Độ dài:** ~700 từ

---

## 1. Tôi phụ trách phần nào?

Tôi đảm nhận hai phần chính trong buổi lab:

**Sprint 3 — MCP HTTP Server:** Thiết kế và triển khai `mcp_server.py` như một HTTP server thực sự bằng FastAPI (thay vì mock class), expose các tools `validate_refund_eligibility`, `check_policy_version`, `get_sla_rules` qua REST endpoints. Đồng thời viết `mcp_client.py` với logic HTTP-first / local-fallback để các worker gọi MCP mà không phụ thuộc server luôn chạy.

**Retrieval Enhancement — Hybrid BM25 + Dense RRF:** Nâng cấp `workers/retrieval.py` từ dense-only lên hybrid retrieval bằng thư viện `rank-bm25`, kết hợp với Reciprocal Rank Fusion (RRF). Cập nhật `workers/synthesis.py` để tính confidence từ `dense_score` thay vì RRF score.

**File chính tôi chịu trách nhiệm:**
- `mcp_server.py`, `mcp_client.py`
- `workers/retrieval.py` — functions `_tokenize`, `_get_bm25_index`, `retrieve_bm25`, `_rrf_merge`, `retrieve_hybrid`
- `workers/synthesis.py` — `_estimate_confidence`, hard-bypass cho `policy_version_mismatch`

**Kết nối với thành viên khác:**  
`mcp_client.py` được `workers/policy_tool.py` (Worker Owner) gọi qua `_call_mcp_tool()`. Kết quả MCP (`policy_result`) do tôi thiết kế schema, Synthesis Worker (cùng do tôi cải tiến) đọc để quyết định abstain hay answer.

**Bằng chứng (commit hash):**
- `7cc5f6e implement sprint 3`
- `f2c6895 fix grading pipeline: PagerDuty retrieval, temporal policy abstain, negation fix`
- `42b56d8 feat: multi-query retrieval and comprehensive synthesis prompt`
- `aad5a66 feat: hybrid BM25+dense retrieval with RRF merge`

---

## 2. Tôi đã ra một quyết định kỹ thuật gì?

**Quyết định:** Dùng **Reciprocal Rank Fusion (RRF)** để kết hợp dense retrieval và BM25 sparse retrieval, thay vì chỉ dùng dense embedding.

**Các lựa chọn thay thế:**
- **Dense-only (cách cũ):** Nhanh, không cần index riêng, nhưng bỏ sót từ khóa chuyên biệt. Trace gq09 cho thấy dense query "SLA P1 notification" không đủ trọng số để kéo Section 4 (chứa PagerDuty) lên top — kết quả thiếu 1 trong 3 kênh thông báo.
- **Re-ranking bằng cross-encoder:** Chính xác hơn nhưng cần download thêm model (~400MB), không phù hợp môi trường lab.
- **RRF merge dense + BM25 (cách tôi chọn):** Không cần model bổ sung, chỉ cần `pip install rank-bm25`. BM25 bắt exact keyword "PagerDuty" và rank Section 4 lên #1, RRF hợp nhất với dense để giữ chunk đó trong top results.

**Lý do chọn:**  
BM25 bù đắp đúng điểm yếu của dense: các từ khóa tên riêng (PagerDuty, Flash Sale, Level 3) có TF-IDF cao → BM25 rank đúng, trong khi dense embedding "trung bình hóa" nhiều khái niệm nên bỏ sót.

**Trade-off đã chấp nhận:**  
RRF scores rất nhỏ (0.016–0.188) so với cosine similarity (0.4–0.7). Nếu dùng trực tiếp cho confidence, kết quả sẽ sai (tất cả câu ra confidence=0.1). Tôi giải quyết bằng cách lưu `dense_score` riêng trong mỗi chunk, `_estimate_confidence` đọc `dense_score` thay vì `score` (RRF).

**Bằng chứng từ code:**

```python
# workers/retrieval.py — _rrf_merge()
rrf_scores[key] = {
    "chunk": chunk,
    "rrf": 0.0,
    "dense_score": chunk.get("score", 0.0),  # preserve cosine score
}
# ...
chunk["score"] = round(item["rrf"], 6)          # dùng để rank
chunk["dense_score"] = round(item["dense_score"], 4)  # dùng cho confidence
```

BM25 test với query "SLA P1 notification PagerDuty":
```
BM25 rank #1: [1.0] sla-p1-2026.pdf — Phần 4: Công cụ và kênh thông báo [PagerDuty]
```

---

## 3. Tôi đã sửa một lỗi gì?

**Lỗi:** BM25 trả về 0 kết quả do regex `_tokenize` bị trailing space.

**Symptom:**  
Sau khi implement `retrieve_bm25()`, mọi query đều trả về danh sách rỗng:
```
BM25 returned: 0 results
```
Phần hybrid RRF fallback về dense-only, BM25 không có tác dụng.

**Root cause:**  
Trong hàm `_tokenize()`, pattern regex có dấu cách thừa ở cuối character class:

```python
# SAI: trailing space khiến split chỉ kích hoạt khi ký tự đặc biệt THEO SAU dấu cách
tokens = re.split(r'[\s\.,;:!?\(\)\[\]{}\-/\\|"\']+ ', text)
#                                                       ^ dấu cách thừa
```

Pattern này chỉ split khi có `<ký_tự_đặc_biệt><dấu_cách>`, nghĩa là các từ thông thường không bị split → mỗi document thành 1 token duy nhất → BM25 không match được với query tokens.

**Cách sửa:**  
Xóa dấu cách thừa:

```python
# ĐÚNG: split trực tiếp theo whitespace và dấu câu
tokens = re.split(r'[\s\.,;:!?\(\)\[\]{}\-/\\|"\']+', text)
```

**Bằng chứng trước/sau:**

*Trước khi sửa:*
```
BM25 returned: 0 results
```

*Sau khi sửa:*
```
BM25 returned: 10 results
[1.0]   sla-p1-2026.pdf — Phần 4: Công cụ kênh thông báo [PagerDuty]
[0.52]  sla-p1-2026.pdf — Phần 5: Lịch sử phiên bản
[0.49]  sla-p1-2026.pdf — Phần 2: SLA theo mức độ
```

---

## 4. Tôi tự đánh giá đóng góp của mình

**Tôi làm tốt nhất ở điểm nào?**  
Thiết kế hard-bypass trong `synthesis.py` khi phát hiện `policy_version_mismatch`: thay vì để LLM dùng v4 content trả lời đơn hàng v3, hàm `synthesize()` trả về abstain ngay mà không gọi LLM. Giải pháp này đảm bảo pipeline không hallucinate nội dung policy không có trong tài liệu — gq02 đạt điểm tối đa với confidence=0.2 và MCP tool `validate_refund_eligibility` được gọi đúng.

**Tôi làm chưa tốt ở điểm nào?**  
gq09 vẫn thiếu điểm vì LLM không mention PagerDuty dù BM25 rank chunk đó #1. Synthesis prompt chưa đủ mạnh để force LLM đọc "Phần 4: Công cụ" khi nó đã có đủ bước từ "Phần 3: Quy trình". Cần thêm instruction cụ thể về notification tools.

**Nhóm phụ thuộc vào tôi ở đâu?**  
`mcp_client.py` là cầu nối giữa `policy_tool_worker` và MCP server. Nếu client chưa có fallback, pipeline fail ngay khi server không chạy — block toàn bộ các câu hỏi policy.

**Phần tôi phụ thuộc vào thành viên khác:**  
Tôi cần `supervisor_route` đúng từ Supervisor Owner để `policy_tool_worker` được gọi. Nếu gq02 bị route sang `retrieval_worker`, MCP sẽ không được gọi và temporal scoping không hoạt động.

---

## 5. Nếu có thêm 2 giờ, tôi sẽ làm gì?

Tôi sẽ thêm **chunk-level section metadata** vào index — đánh dấu mỗi chunk với `section_type: "tools" | "steps" | "conditions"` khi ingest. Lý do: trace gq09 cho thấy LLM bỏ qua Section 4 (tools/channels) vì nó trình bày danh sách công cụ, không phải bước quy trình. Nếu retrieval trả về metadata `section_type=tools` kèm chunk, synthesis prompt có thể được điều chỉnh để yêu cầu LLM list tools riêng — tách biệt khỏi steps. Điều này ước tính thêm 4–6 điểm cho gq09 (16 pts) dựa trên grading criteria còn thiếu.

---

*File: `reports/individual/Nguyen_Manh_Phu.md`*
