# Báo Cáo Cá Nhân — Day 08: Full RAG Pipeline

**Họ và tên:** Nguyễn Hải Đăng  
**MSSV:** 2A202600157  
**Môn:** Lab08
**Ngày:** 2026-04-13  

---

## 1. Tôi đã làm gì trong lab này?

Trong lab Day 08, tôi đảm nhận vai trò Eval Owner, chịu trách nhiệm thiết kế và vận hành hệ thống đánh giá cho toàn bộ RAG pipeline.

Cụ thể, tôi tập trung chủ yếu ở Sprint 4 (Evaluation) với các công việc chính:
-Định nghĩa expected answer và expected sources để làm ground truth
-Implement pipeline LLM-as-Judge để chấm 3 metrics
-Chạy A/B testing giữa 2 config: baseline (dense) và variant (hybrid)

Công việc của tôi kết nối trực tiếp với:
-Retrieval (Sprint 2, 3) → để đánh giá quality của search
-Generation → để kiểm tra hallucination và groundedness

---

## 2. Điều tôi hiểu rõ hơn sau lab này

Sau lab này, tôi hiểu rõ hơn về evaluation trong RAG, đặc biệt là:
1. Faithfulness và Relevance
-Một câu trả lời có thể relevant nhưng không faithful (ví dụ q09, q10).
-Faithfulness yêu cầu tất cả thông tin phải xuất phát từ context, không được suy diễn thêm.
2. Hybrid retrieval không đảm bảo tốt hơn nếu không kiểm soát noise
-Trước lab, tôi nghĩ hybrid luôn tốt hơn dense.
-Nhưng kết quả cho thấy:
Faithfulness giảm (4.6 → 4.3)
Relevance giảm (4.8 → 4.4)

---

## 3. Điều tôi ngạc nhiên hoặc gặp khó khăn

1. Hybrid không outperform như kỳ vọng

Ban đầu tôi kỳ vọng hybrid sẽ tốt hơn rõ rệt, nhưng thực tế:

-Baseline (dense) lại có Faithfulness và Relevance cao hơn
-Hybrid chỉ nhỉnh hơn nhẹ về Completeness

→ Insight quan trọng:
Retrieval tốt hơn ≠ Answer tốt hơn
→ Vì còn phụ thuộc vào prompt + cách model dùng context

2. Lỗi lớn nhất: xử lý “insufficient context” (q09, q10)
q09:
-Dense: hallucinate → điểm thấp completeness
-Hybrid: abstain → nhưng relevance thấp
q10:
-Cả hai đều fail:
-Không bổ sung “standard process” như expected answer
→ completeness thấp

→ Khó nhất là:
Khi nào nên abstain
Khi nào nên trả lời + bổ sung context hợp lệ

---

## 4. Phân tích một câu hỏi trong scorecard
**Câu hỏi:** : ERR-403-AUTH là lỗi gì và cách xử lý?

**Phân tích:** :
Đây là một case rất điển hình để thấy sự khác biệt giữa baseline và hybrid.

Ở baseline (dense), hệ thống trả lời khá chi tiết về lỗi ERR-403-AUTH, đưa ra các bước xử lý như tạo access request, phê duyệt, cấp quyền… Tuy nhiên, câu trả lời này không có trong tài liệu → dẫn đến hallucination. Dù Faithfulness vẫn bị chấm cao (do judge chưa strict), nhưng thực tế đây là lỗi ở generation (model suy diễn) kết hợp với retrieval fail (không tìm đúng context).

Ở variant (hybrid), hệ thống trả lời rằng không có thông tin trong tài liệu. Đây là hành vi đúng hơn về mặt RAG (abstain), nhưng lại bị chấm Relevance thấp vì không đưa ra hướng xử lý thêm.

→ Điều này cho thấy:

-Hybrid giúp tránh hallucination
-Nhưng lại thiếu fallback answer (ví dụ: “có thể là lỗi auth, liên hệ IT Helpdesk”)

Kết luận: lỗi chính nằm ở prompt/generation logic, không phải retrieval. Hybrid cải thiện safety nhưng chưa tối ưu UX.
---

## 5. Nếu có thêm thời gian, tôi sẽ làm gì?
Tôi sẽ cải thiện prompt để xử lý insufficient context tốt hơn. Cụ thể, nếu không tìm thấy thông tin, model vẫn nên:

-Trả lời phần có thể suy luận hợp lý (ví dụ: lỗi auth → liên quan quyền truy cập)
-Và đồng thời nói rõ không có trong tài liệu
Lý do: kết quả eval (q09, q10) cho thấy hệ thống hiện tại hoặc hallucinate (dense) hoặc trả lời quá “trống” (hybrid), chưa đạt cân bằng giữa correctness và usefulness.

