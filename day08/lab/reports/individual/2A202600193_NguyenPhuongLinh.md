# Báo Cáo Cá Nhân — Lab Day 08: RAG Pipeline

**Họ và tên:** Nguyễn Phương Linh  
**MSSV:** 2A202600193  
**Vai trò trong nhóm:** Retrieval Owner  
**Ngày nộp:** 13/04/2026  
**Độ dài:** ~720 từ

---

## 1. Tôi đã làm gì trong lab này?

Trong lab này, tôi đảm nhận vai trò **Retrieval Owner**, chịu trách nhiệm chính ở Sprint 1 và Sprint 3. Cụ thể, tôi thiết kế toàn bộ pipeline từ bước xử lý tài liệu thô đến khi đưa context vào tay LLM.

Ở **Sprint 1**, tôi implement hàm `preprocess_document()` để tách phần metadata header (Source, Department, Effective Date, Access) ra khỏi nội dung chính trước khi chunk — thay vì để LangChain tự xử lý thô. Tôi cũng implement `chunk_document()` sử dụng chiến lược **Recursive Character Splitting** với `CHUNK_SIZE = 400 tokens` và `CHUNK_OVERLAP = 80 tokens`, ưu tiên tách tại `\n\n` (paragraph) trước, sau đó `\n` (line), rồi mới xuống cấp mịn hơn. Mỗi chunk được gắn đầy đủ metadata: `source`, `section`, `department`, `effective_date`, `access`.

Ngoài ra, tôi cũng tự implement hàm `semantic_split()` — một chiến lược chunking nâng cao không dùng separator cứng mà dùng **embedding similarity** để gộp các câu liên tiếp có ngữ nghĩa liên quan, với ngưỡng cosine = 0.85.

Ở **Sprint 3**, tôi implement toàn bộ retrieval pipeline: `retrieve_dense()` (ChromaDB vector search), `retrieve_sparse()` (BM25 keyword search với tokenizer tùy chỉnh xử lý ký tự kỹ thuật), `retrieve_hybrid()` (Reciprocal Rank Fusion kết hợp cả hai), và hàm `classify_query()` để tự động điều chỉnh trọng số dense/sparse tùy loại câu hỏi. Phần reranking được nâng cấp thành `rerank_with_llm()` — dùng `gpt-4o-mini` để chọn ra top-k chunk liên quan nhất thay vì cross-encoder.

Công việc của tôi kết nối trực tiếp với Tech Lead (chunking tốt thì embed mới hiệu quả) và Eval Owner (scorecard phản ánh chất lượng retrieval).

---

## 2. Điều tôi hiểu rõ hơn sau lab này

**Concept 1: Tại sao metadata phải được tách trước khi chunk**

Trước lab, tôi nghĩ metadata chỉ là thông tin phụ. Nhưng khi implement `preprocess_document()`, tôi nhận ra nếu không tách header ra trước, dòng "Source: policy/refund-v4.pdf" sẽ bị embed vào nội dung chunk và làm lệch embedding. Hơn nữa, nếu chunk đầu tiên chứa "Department: CS | Effective Date: 2026-01-01", cosine similarity của nó sẽ bị kéo về phía keyword metadata thay vì nội dung policy thực sự. Tách metadata sạch trước là điều kiện tiên quyết để retrieval hoạt động đúng. Đây không phải "clean code" mà là **yêu cầu kỹ thuật cốt lõi**.

**Concept 2: Hybrid retrieval không phải phép cộng đơn giản — trọng số động quan trọng hơn**

Khi implement `retrieve_hybrid()` với Reciprocal Rank Fusion, tôi tưởng 50/50 dense/sparse là đủ tốt. Nhưng khi chạy qua bộ test, tôi nhận ra câu hỏi về mã lỗi `ERR-403-AUTH` (q09) hoặc nhãn kỹ thuật như `SLA P1` cần BM25 nhiều hơn hẳn — còn câu hỏi về chính sách viết bằng ngôn ngữ tự nhiên lại cần dense. Tôi thêm hàm `classify_query()` để tự động phân loại và gán trọng số động: query kỹ thuật → 20% dense + 80% sparse. Đây là bài học về **adaptive retrieval**: không có một cấu hình duy nhất phù hợp với mọi loại query.

---

## 3. Điều tôi ngạc nhiên hoặc gặp khó khăn

Điều tôi không ngờ nhất là **semantic chunking lại không vượt trội hơn recursive chunking** trong bài toán này. Tôi đã implement `semantic_split()` với embedding similarity threshold = 0.85 và kỳ vọng nó sẽ tạo ra các chunk ngữ nghĩa mạch lạc hơn. Nhưng trên corpus policy rules ngắn và có cấu trúc rõ ràng (mỗi điều khoản một đoạn), recursive splitting thực ra mang lại chunk đủ tốt mà không mất thêm chi phí API call để embed từng câu.

Khó khăn kỹ thuật lớn nhất là BM25 tokenization cho text hỗn hợp Việt-Anh. Tokenizer mặc định `split()` sẽ không tách được chuỗi như `"ERR-403-AUTH"` thành các token có nghĩa. Tôi phải thêm regex `re.sub(r'[\(\)\-\:\/]', ' ', text.lower())` để tách các ký tự kỹ thuật trước khi đưa vào BM25Okapi, nếu không query `"ERR-403"` sẽ không match token `"err-403-auth"`.

---

## 4. Phân tích câu hỏi q09 trong scorecard

**Câu hỏi:** *"Lỗi ERR-403-AUTH khi đăng nhập hệ thống nội bộ nghĩa là gì?"*

Đây là câu hỏi thiết kế để kiểm tra khả năng **abstain** — mã lỗi này không tồn tại trong corpus. Đây là trường hợp thú vị nhất đối với Retrieval Owner vì nó thử thách trực tiếp strategy retrieval.

**Kết quả baseline (dense):** Faithfulness = 4/5, Relevance = 2/5, Context Recall = None, Completeness = 4/5.

Dense retrieval tìm được các chunk về "access control" và "IT helpdesk" bằng semantic similarity (từ "đăng nhập" và "lỗi" khớp ngữ nghĩa với các policy IT), nhưng không tìm được mã lỗi cụ thể vì `ERR-403-AUTH` không có trong tài liệu. Điểm Relevance thấp vì câu trả lời không relevant với thực tế.

**Kết quả variant (smart hybrid):** Faithfulness giảm về 2/5 — tệ hơn baseline.

Đây là insight quan trọng: BM25 với trọng số cao (80%) khi phát hiện pattern `err-\d+` đã match quá nhiều chunk liên quan đến "access control" cùng lúc, khiến context block chứa thông tin sát hơn khiến LLM "tự tin" hơn nhưng lại hallucinate nhiều hơn.

**Kết luận:** Lỗi nằm ở cả retrieval lẫn generation. Về retrieval, cần thêm bước kiểm tra xem query có keyword match với corpus không trước khi quyết định dùng hybrid. Về generation, prompt cần instruction cứng hơn: *"Nếu mã lỗi không xuất hiện trong context, trả lời ngay là không có thông tin."*

---

## 5. Nếu có thêm thời gian, tôi sẽ làm gì?

Tôi sẽ thử **pre-retrieval query classification nghiêm ngặt hơn**: thay vì chỉ dùng regex đơn giản trong `classify_query()`, tôi sẽ dùng LLM để phân loại query vào 3 nhóm: (1) factual policy query → dense, (2) technical keyword query → sparse-heavy hybrid, (3) out-of-scope query → trigger abstain ngay trước khi retrieval. Kết quả eval cho thấy q09 và q10 — hai câu out-of-scope — đều có điểm Faithfulness thấp nhất ở cả hai pipeline, và nguyên nhân gốc rễ bắt đầu từ bước retrieval đưa vào context nhiễu, không phải từ bước generation.

---

*File lưu tại: `reports/individual/2A202600193_NguyenPhuongLinh.md`*
