# Báo Cáo Cá Nhân -- Lab Day 08: RAG Pipeline

**Họ và tên:** Nguyễn Mạnh Phú
**Vai trò:** Documentation Owner
**Ngày nộp:** 13-04-2026

---

## 1. Tôi đã làm gì trong lab này?

Tôi phụ trách viết tài liệu kỹ thuật cho nhóm, cụ thể là `docs/architecture.md` và `docs/tuning-log.md`.

Với architecture.md, phần mất thời gian nhất là mô tả quyết định chunking. Nhóm đã thử hai chiến lược: semantic split (dùng embedding để tách câu theo cosine similarity) và section-based (tách theo heading `===` trong tài liệu gốc). Tôi kiểm tra `debug_chunks.json` và phát hiện semantic split gọi `.replace("\n", " ")` trước khi xử lý, làm mất toàn bộ bullet points. Ví dụ chunk về cấp quyền bị flatten thành một dòng dài: "Level 1 -- Read Only: Áp dụng cho:... Level 2 -- Standard Access:..." thay vì giữ nguyên format từng mục. Phát hiện này dẫn đến quyết định chuyển sang section-based chunking.

Với tuning-log.md, tôi ghi lại kết quả A/B test từ `results/scorecard_baseline.md` và `scorecard_variant.md`, phân tích per-question để tìm điểm yếu cụ thể thay vì chỉ nhìn average.

---

## 2. Phân tích câu gq05

**Câu hỏi:** "Contractor từ bên ngoài công ty có thể được cấp quyền Admin Access không? Nếu có, cần bao nhiêu ngày và có yêu cầu đặc biệt gì?"

**Expected:** Contractor được cấp. Admin Access (Level 4) cần IT Manager + CISO phê duyệt, 5 ngày làm việc, training bắt buộc về security policy.

**Pipeline trả lời:** Contractor được cấp (đúng), nhưng nêu approver là Line Manager (sai -- đó là Level 2/3, không phải Level 4), thời gian 1 ngày (sai -- đó là thời gian Line Manager duyệt ở bước chung, không phải tổng thời gian Level 4), và không đề cập training bắt buộc.

**Root cause -- Retrieval:** Thông tin nằm ở hai section khác nhau trong access_control_sop.txt. Section 1 nói về phạm vi áp dụng (contractor, vendor), Section 2 nói chi tiết Level 4 (IT Manager + CISO, 5 ngày, training). Pipeline retrieve được Section 1 và section quy trình chung (bước tạo ticket, bước Line Manager duyệt) nhưng bỏ lỡ section chi tiết Level 4. Kết quả là LLM chỉ thấy thông tin về quy trình chung và suy diễn approver là Line Manager -- đúng cho Level 2 nhưng sai hoàn toàn cho Level 4.

Đây đúng là failure mode mà `grading_questions.json` đã cảnh báo: "Lẫn lộn Level 3 và Level 4 approvers" và "Bỏ qua yêu cầu training bắt buộc". Vấn đề không phải ở generation mà ở retrieval -- LLM rerank chọn sai chunk vì snippet 500 ký tự gửi cho reranker không đủ để phân biệt giữa quy trình chung và quy trình Level 4 cụ thể.

**Nếu được fix:** Tăng snippet length trong `rerank_with_llm()` hoặc thêm query decomposition -- tách "contractor + Admin Access + điều kiện" thành sub-queries để retrieve riêng từng phần.

---

## 3. Điều tôi học được

Bài học lớn nhất đến từ câu q09 trong bộ test questions (ERR-403-AUTH). Khi nhìn scorecard, baseline đạt F=5/R=5/C=4 cho câu này -- trông hoàn hảo. Variant chỉ đạt F=5/R=1/C=2 -- trông tệ hơn hẳn. Trung bình overall, baseline "thắng" variant ở relevance (4.80 vs 4.40).

Nhưng khi đọc câu trả lời thực tế, baseline bịa ra hướng dẫn 3 bước xử lý ERR-403-AUTH bằng cách ghép thông tin từ chunks về access control và helpdesk FAQ -- mã lỗi này không hề tồn tại trong tài liệu. Variant đơn giản nói "tài liệu không đề cập" -- đúng theo SCORING.md (abstain = full marks, bịa = penalty -50%).

Vấn đề nằm ở LLM-as-Judge: `score_faithfulness` kiểm tra "answer có bắt nguồn từ context không" -- baseline dùng context thật nên được 5/5, dù dùng sai context. `score_answer_relevance` kiểm tra "answer có trả lời câu hỏi không" -- abstain bị coi là "không trả lời" nên chỉ được 1/5. Nếu loại q09, hai configs bằng nhau hoàn toàn.

Bài học: **không bao giờ chỉ nhìn average score để kết luận**. Phải đọc từng câu trả lời, đặc biệt các câu abstain.

---

## 4. Nếu có thêm thời gian

1. **Cải thiện rerank cho multi-section queries:** gq05 fail vì reranker không đủ context để phân biệt Level 3 vs Level 4. Có thể tăng snippet từ 500 lên 800 ký tự, hoặc thêm metadata (section name) vào prompt rerank để LLM biết chunk nào thuộc section nào.

2. **Thêm abstain-aware scoring trong eval.py:** Với câu có `expected_sources: []`, cần logic riêng: pipeline abstain thì score cao, pipeline bịa thì phạt. Hiện tại eval.py không có logic này nên gq07/q09 bị chấm sai.
