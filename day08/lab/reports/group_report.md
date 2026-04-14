# Báo Cáo Nhóm — Lab Day 08: Full RAG Pipeline

**Tên nhóm:** 71 
**Thành viên:**
| Tên | Vai trò | MSSV |
|-----|---------|------|
| Ngô Văn Long | Tech Lead | 2A202600129 |
| Nguyễn Phương Linh | Retrieval Owner | 2A202600193 |
| Nguyễn Hải Đăng | Eval Owner | 2A202600157 |
| Nguyễn Mạnh Phú | Documentation Owner | 2A202600178 |

**Ngày nộp:** 2026-04-13  
**Repo:** https://github.com/dangdeptraiht/Lab08-Nhom71 
**Độ dài:** ~820 từ

---

## 1. Pipeline nhóm đã xây dựng

**Chunking decision:**

Nhóm dùng **section-based chunking** — ranh giới chunk là các heading `=== Tên Section ===` có trong tài liệu gốc. Mỗi section thành một chunk độc lập, prefix `Tài liệu: [TÊN] / Mục: [SECTION]` được thêm vào đầu mỗi chunk để LLM nhận biết context khi đọc. Với section dài hơn 2000 ký tự (ngưỡng `CHUNK_SIZE=500 tokens × 4`), hàm `recursive_split()` sẽ chia tiếp theo thứ tự ưu tiên: `\n\n` → `\n` → `. ` → ` `, với `CHUNK_OVERLAP=50 tokens`. Chiến lược này được chọn sau khi nhóm phát hiện semantic chunking (cosine-based) phá hủy format bullet points của tài liệu policy khi flatten `\n` thành space.

**Embedding model:**

`text-embedding-3-small` (OpenAI) — chọn vì hỗ trợ văn bản tiếng Việt tốt, cost thấp hơn `text-embedding-3-large`, và đủ để phân biệt ngữ nghĩa các điều khoản policy ngắn. Vector store: ChromaDB với `hnsw:space=cosine`.

**Retrieval variant (Sprint 3):**

Nhóm implement **Smart Hybrid + LLM Rerank**: kết hợp dense (ChromaDB) và sparse (BM25) theo Reciprocal Rank Fusion, với trọng số động qua `classify_query()` — query kỹ thuật (chứa mã lỗi, tên Level, ký tự đặc biệt) được gán 20% dense / 80% sparse; query chính sách tự nhiên được gán 70% dense / 30% sparse. Top-10 sau RRF tiếp tục được rerank bởi `gpt-4o-mini` để chọn top-5 chunk trước khi generate.

---

## 2. Quyết định kỹ thuật quan trọng nhất

**Quyết định:** Chọn section-based chunking thay vì semantic chunking

**Bối cảnh vấn đề:**

Sprint 1 bắt đầu với hai candidate: (1) semantic chunking — dùng embedding similarity (cosine threshold 0.85) để tự động gộp các câu liên quan; (2) section-based — tách theo heading `=== ... ===` có sẵn trong tài liệu. Nhóm ban đầu thiên về semantic chunking vì kỳ vọng chunk sẽ mạch lạc ngữ nghĩa hơn.

**Các phương án đã cân nhắc:**

| Phương án | Ưu điểm | Nhược điểm |
|-----------|---------|-----------|
| Semantic chunking (cosine-based) | Chunk ngữ nghĩa mạch lạc, không phụ thuộc format gốc | Gọi embedding API nhiều lần; quan trọng hơn: flatten `\n` thành space, phá hủy bullet-point format của tài liệu policy |
| Section-based chunking | Giữ nguyên format gốc (bullets, Q&A, bảng); không cần API call; ranh giới tự nhiên đã có sẵn | Phụ thuộc vào cấu trúc tài liệu nhất quán; section dài cần fallback |

**Phương án đã chọn và lý do:**

Section-based chunking. Quyết định được đưa ra khi Documentation Owner kiểm tra `debug_chunks.json` và phát hiện semantic split tạo ra chunk như: `"Level 1 -- Read Only: Áp dụng cho:... Level 2 -- Standard Access:..."` — toàn bộ structure bị flatten. LLM đọc chunk này khó phân biệt Level 1 và Level 2, dẫn đến lỗi gán approver sai (gq05 ban đầu bị phân tích sai). Section-based giữ nguyên format, đặc biệt quan trọng với tài liệu SOP nhiều bước và bảng cấp quyền.

**Bằng chứng từ scorecard:**

Baseline dense (sau khi đã dùng section-based index): Context Recall = **5.00/5** toàn bộ test questions. Câu gq06 — câu multi-hop khó nhất (12 điểm) — pipeline trả lời đúng 3 bước escalation vì section "Quy trình escalation khẩn cấp" (~350 ký tự) nằm nguyên vẹn trong một chunk, không bị cắt.

---

## 3. Kết quả grading questions

**Ước tính điểm raw:** ~88 / 98

**Câu tốt nhất:** gq06 và gq07

- **gq06** (12 điểm, câu multi-hop khó nhất): Pipeline trả lời đúng toàn bộ quy trình escalation 2am — cấp quyền tạm thời 24h, Tech Lead phê duyệt lời, ghi log Security Audit. Thành công nhờ section-based chunk giữ nguyên 3 bước trong một đơn vị; hybrid retrieval tìm đúng `access_control_sop`.
- **gq07** (10 điểm, câu abstain): Pipeline trả lời `"Tài liệu hiện tại không đề cập đến mức phạt khi vendor vi phạm SLA P1."` — đây là abstain đúng, không hallucinate.

**Câu fail:** gq02 — Partial

Pipeline trả lời đúng phần remote requirements (VPN bắt buộc, camera bật) nhưng với sub-câu hỏi "VPN có giới hạn số thiết bị không?" trả lời `"tài liệu không đề cập"` — thực ra thông tin này có trong corpus nhưng nằm ở document khác. Root cause: retrieval không thực hiện multi-document synthesis cho query gộp hai câu hỏi vào một vector. Phần sources log ghi `"Không có"`.

**Câu gq07 (abstain):** Xử lý đúng — pipeline abstain và không penalty. Tuy nhiên LLM-as-Judge trong `eval.py` đã chấm sai câu abstain q09 trong test set (internal) vì judge không nhận ra "nói không biết mã lỗi này" khác với "hallucinate". Issue đã được ghi nhận trong tuning-log.

---

## 4. A/B Comparison — Baseline vs Variant

**Biến đã thay đổi:** `retrieval_mode` — từ `dense` sang `smart_hybrid` (RRF dense + BM25, dynamic weights, LLM rerank)

| Metric | Baseline (dense) | Variant (smart_hybrid) | Delta |
|--------|-----------------|----------------------|-------|
| Faithfulness | 4.60 / 5 | 4.30 / 5 | −0.30 |
| Relevance | 4.80 / 5 | 4.40 / 5 | −0.40 |
| Context Recall | 5.00 / 5 | 5.00 / 5 | 0.00 |
| Completeness | 4.40 / 5 | 4.20 / 5 | −0.20 |

**Kết luận:**

Variant kém hơn baseline trên average scorecard — nhưng đây là kết luận misleading. Phần lớn delta âm đến từ câu q09 (ERR-403-AUTH): baseline hallucinate nhưng dùng context thật nên judge chấm Faithfulness = 5/5; variant abstain đúng nhưng judge chấm Relevance = 1/5. Nếu loại q09 khỏi tính toán, hai variant gần như tương đương. Theo thang chấm thực tế của grading (abstain đúng = full marks, hallucinate = penalty −50%), **variant là lựa chọn đúng** cho production. Nhóm dùng variant `smart_hybrid` để chạy `grading_questions.json`.

---

## 5. Phân công và đánh giá nhóm

**Phân công thực tế:**

| Thành viên | Phần đã làm | Sprint |
|------------|-------------|--------|
| Ngô Văn Long | `preprocess_document()`, `chunk_document()`, `recursive_split()`, `build_index()` | 1 |
| Nguyễn Phương Linh | `retrieve_dense()`, `retrieve_sparse()` (BM25), `retrieve_hybrid()` (RRF), `classify_query()`, `rerank_with_llm()` | 2, 3 |
| Nguyễn Hải Đăng | `score_faithfulness()`, `score_relevance()`, `score_context_recall()`, LLM-as-Judge pipeline, A/B runner | 4 |
| Nguyễn Mạnh Phú | `docs/architecture.md`, `docs/tuning-log.md`, phân tích `debug_chunks.json`, per-question scorecard review | 4 |

**Điều nhóm làm tốt:**

Quyết định chuyển từ semantic sang section-based chunking sớm trong Sprint 1 — nhờ inspect `debug_chunks.json` thực tế, không giả định. Pipeline cuối giữ Context Recall = 5.00/5, và câu multi-hop khó nhất (gq06) đạt Full. Bộ grading được nộp đúng deadline với đủ 10 câu và timestamp hợp lệ.

**Điều nhóm làm chưa tốt:**

LLM-as-Judge trong `eval.py` chấm sai câu abstain — không có logic riêng cho trường hợp `expected_sources: []`. Điều này làm average scorecard không phản ánh đúng chất lượng thực, khiến nhóm mất thời gian phân tích nhầm hướng trước khi đọc từng câu trả lời thực tế. Ngoài ra, gq02 bị partial vì không có query decomposition cho câu hỏi gộp nhiều sub-questions.

---

## 6. Nếu có thêm 1 ngày, nhóm sẽ làm gì?

**Ưu tiên 1 — Query decomposition cho multi-question queries:**  
Evidence: gq02 partial vì pipeline dùng một vector cho câu hỏi gộp "remote requirements + VPN device limit" → chỉ retrieve được một nửa. Sẽ thêm bước tách query thành sub-queries, retrieve riêng từng phần, merge context trước khi generate.

**Ưu tiên 2 — Abstain-aware scoring trong `eval.py`:**  
Evidence: q09 trong test set bị chấm sai (variant đúng nhưng điểm thấp hơn baseline). Sẽ thêm nhánh logic: nếu `expected_sources: []` thì pipeline abstain = score cao, pipeline có answer cụ thể = kiểm tra penalty.
