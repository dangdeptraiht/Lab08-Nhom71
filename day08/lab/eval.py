"""
eval.py — Sprint 4: Evaluation & Scorecard
==========================================
Mục tiêu Sprint 4 (60 phút):
  - Chạy 10 test questions qua pipeline
  - Chấm điểm theo 4 metrics: Faithfulness, Relevance, Context Recall, Completeness
  - So sánh baseline vs variant
  - Ghi kết quả ra scorecard

Definition of Done Sprint 4:
  ✓ Demo chạy end-to-end (index → retrieve → answer → score)
  ✓ Scorecard trước và sau tuning
  ✓ A/B comparison: baseline vs variant với giải thích vì sao variant tốt hơn

A/B Rule (từ slide):
  Chỉ đổi MỘT biến mỗi lần để biết điều gì thực sự tạo ra cải thiện.
  Đổi đồng thời chunking + hybrid + rerank + prompt = không biết biến nào có tác dụng.
"""

import json
import csv
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from rag_answer import rag_answer

# Đảm bảo in được tiếng Việt trên Console Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        # Fallback cho Python < 3.7
        pass

load_dotenv()
client_judge = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
JUDGE_MODEL = "gpt-4o-mini"

# =============================================================================
# CẤU HÌNH
# =============================================================================

TEST_QUESTIONS_PATH = Path(__file__).parent / "data" / "test_questions.json"
GRADING_QUESTIONS_PATH = Path(__file__).parent / "data" / "grading_questions.json"
RESULTS_DIR = Path(__file__).parent / "results"
LOGS_DIR = Path(__file__).parent / "logs"

# Cấu hình baseline (Sprint 2)
BASELINE_CONFIG = {
    "retrieval_mode": "dense",
    "top_k_search": 10,
    "top_k_select": 3,
    "use_rerank": False,
    "label": "baseline_dense",
}

# Cấu hình variant (Sprint 3 — Hybrid Search)
VARIANT_CONFIG = {
    "retrieval_mode": "hybrid",
    "top_k_search": 20,
    "top_k_select": 5,
    "use_rerank": True,  # KÍCH HOẠT: Sử dụng gpt-4o-mini để lọc nhiễu
    "label": "variant_smart_hybrid",
}


# =============================================================================
# SCORING FUNCTIONS
# 4 metrics từ slide: Faithfulness, Answer Relevance, Context Recall, Completeness
# =============================================================================


def score_with_llm(system_prompt: str, user_content: str) -> Dict[str, Any]:
    """Helper function to call LLM for scoring."""
    try:
        response = client_judge.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=45,
        )
        data = json.loads(response.choices[0].message.content)
        return {
            "score": int(data.get("score", 1)),
            "notes": data.get("reason", "No reason provided"),
        }
    except Exception as e:
        return {"score": 1, "notes": f"Error in judge: {e}"}


def score_faithfulness(
    query: str,
    answer: str,
    chunks_used: List[Dict[str, Any]],
    expected_sources: List[str],
    expected_answer: str,
) -> Dict[str, Any]:
    """
    Faithfulness: Answer must be supported by context.
    Abstain-aware: if the expected_sources is empty (insufficient-context question),
    a correct abstention should score high; fabricating specifics should score low.
    """
    context = "\n---\n".join([c["text"] for c in chunks_used])
    system_prompt = (
        "You are a strict RAG judge.\n"
        "Rate faithfulness on 1-5.\n"
        "Key rules:\n"
        "- Faithfulness measures whether the answer's factual claims are supported by CONTEXT.\n"
        "- If expected_sources is empty, the correct behavior is to abstain and NOT invent details.\n"
        "- In abstain cases, an abstention like 'tài liệu không đề cập/không đủ dữ liệu' is faithful.\n"
        "- Using vaguely related context to produce a confident explanation for a missing fact is NOT faithful.\n"
        "Output JSON: {'score': int, 'reason': str}"
    )
    user_content = (
        f"QUERY:\n{query}\n\n"
        f"EXPECTED_SOURCES_EMPTY:\n{str(not bool(expected_sources))}\n\n"
        f"EXPECTED_ANSWER:\n{expected_answer}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"ANSWER:\n{answer}"
    )
    return score_with_llm(system_prompt, user_content)


def score_answer_relevance(
    query: str,
    answer: str,
    expected_sources: List[str],
    expected_answer: str,
) -> Dict[str, Any]:
    """
    Answer Relevance: Does the answer address the user query?
    Abstain-aware: if expected_sources is empty, abstaining IS relevant.
    """
    system_prompt = (
        "Rate answer relevance on 1-5.\n"
        "5=best possible response for the query given the docs; 1=off-topic.\n"
        "Rules:\n"
        "- If expected_sources is empty (insufficient-context question), a correct abstention is highly relevant.\n"
        "- In abstain cases, do NOT penalize for not providing the missing fact.\n"
        "- If the answer invents a specific policy/number/process not in docs, relevance should be low even if it sounds helpful.\n"
        "Output JSON: {'score': int, 'reason': str}"
    )
    user_content = (
        f"QUERY:\n{query}\n\n"
        f"EXPECTED_SOURCES_EMPTY:\n{str(not bool(expected_sources))}\n\n"
        f"EXPECTED_ANSWER:\n{expected_answer}\n\n"
        f"ANSWER:\n{answer}"
    )
    return score_with_llm(system_prompt, user_content)


def score_context_recall(
    chunks_used: List[Dict[str, Any]],
    expected_sources: List[str],
) -> Dict[str, Any]:
    """
    Context Recall: Retriever có mang về đủ evidence cần thiết không?
    Câu hỏi: Expected source có nằm trong retrieved chunks không?

    Đây là metric đo retrieval quality, không phải generation quality.

    Cách tính đơn giản:
        recall = (số expected source được retrieve) / (tổng số expected sources)

    Ví dụ:
        expected_sources = ["policy/refund-v4.pdf", "sla-p1-2026.pdf"]
        retrieved_sources = ["policy/refund-v4.pdf", "helpdesk-faq.md"]
        recall = 1/2 = 0.5

    TODO Sprint 4:
    1. Lấy danh sách source từ chunks_used
    2. Kiểm tra xem expected_sources có trong retrieved sources không
    3. Tính recall score
    """
    if not expected_sources:
        # Câu hỏi không có expected source (ví dụ: "Không đủ dữ liệu" cases)
        return {"score": None, "recall": None, "notes": "No expected sources"}

    retrieved_sources = {c.get("metadata", {}).get("source", "") for c in chunks_used}

    # TODO: Kiểm tra matching theo partial path (vì source paths có thể khác format)
    found = 0
    missing = []
    for expected in expected_sources:
        # Kiểm tra partial match (tên file)
        expected_name = expected.split("/")[-1].replace(".pdf", "").replace(".md", "")
        matched = any(expected_name.lower() in r.lower() for r in retrieved_sources)
        if matched:
            found += 1
        else:
            missing.append(expected)

    recall = found / len(expected_sources) if expected_sources else 0

    return {
        "score": round(recall * 5),  # Convert to 1-5 scale
        "recall": recall,
        "found": found,
        "missing": missing,
        "notes": f"Retrieved: {found}/{len(expected_sources)} expected sources"
        + (f". Missing: {missing}" if missing else ""),
    }


def score_abstain_aware(
    query: str,
    answer: str,
    expected_sources: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Additional scoring signal for abstain cases.
    If expected_sources is empty, the 'correct' behavior is to abstain (no hallucination).
    Returns None for non-abstain test questions.
    """
    if expected_sources:
        return None
    a = (answer or "").lower()
    abstain_like = ("không" in a and "đề cập" in a) or ("không đủ dữ liệu" in a) or ("tài liệu" in a and "không" in a)
    # crude hallucination heuristic: if answer contains many numbers/dates but should abstain, flag risk
    has_numbers = bool(re.search(r"\d", a))
    if abstain_like and not has_numbers:
        return {"score": 5, "notes": "Abstain behavior detected for insufficient-context question."}
    if abstain_like and has_numbers:
        return {"score": 2, "notes": "Abstain-like but includes numbers; potential hallucination."}
    # not abstaining at all
    return {"score": 1, "notes": "Did not abstain on insufficient-context question (hallucination risk)."}


def run_grading_log(
    config: Dict[str, Any],
    output_path: Path = LOGS_DIR / "grading_run.json",
    timestamp_base: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Run grading_questions.json and write logs/grading_run.json in required format.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(GRADING_QUESTIONS_PATH, "r", encoding="utf-8") as f:
        questions = json.load(f)

    log_rows: List[Dict[str, Any]] = []
    base = timestamp_base or datetime.now()
    for i, q in enumerate(questions):
        qid = q["id"]
        query = q["question"]
        result = rag_answer(
            query=query,
            retrieval_mode=config.get("retrieval_mode", "hybrid"),
            top_k_search=config.get("top_k_search", 20),
            top_k_select=config.get("top_k_select", 5),
            use_rerank=config.get("use_rerank", True),
            verbose=False,
        )
        ts = (base.replace(second=0, microsecond=0) + (datetime.min - datetime.min) + (i * (base - base))).isoformat()
        # Note: keep timestamp string simple; external scripts may override later.
        log_rows.append(
            {
                "id": qid,
                "question": query,
                "answer": result["answer"],
                "sources": result["sources"],
                "chunks_retrieved": len(result["chunks_used"]),
                "retrieval_mode": result["config"]["retrieval_mode"],
                "timestamp": datetime.now().isoformat(),
            }
        )

    output_path.write_text(json.dumps(log_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_rows


def score_completeness(
    query: str,
    answer: str,
    expected_answer: str,
    expected_sources: List[str],
) -> Dict[str, Any]:
    """
    Completeness: Does it cover key points from expected answer (not verbosity).
    Abstain-aware: if expected_sources is empty, completeness = does it clearly abstain
    and (optionally) provide a safe next step like contacting the right team.
    """
    system_prompt = (
        "Compare MODEL ANSWER with EXPECTED ANSWER.\n"
        "Rate completeness 1-5 based on covering the key points, not length.\n"
        "Rules:\n"
        "- If expected_sources is empty, completeness is about a clear abstention + safe next-step guidance.\n"
        "- Do NOT reward invented specifics.\n"
        "Output JSON: {'score': int, 'reason': str}"
    )
    user_content = (
        f"QUERY:\n{query}\n\n"
        f"EXPECTED_SOURCES_EMPTY:\n{str(not bool(expected_sources))}\n\n"
        f"EXPECTED:\n{expected_answer}\n\n"
        f"MODEL ANSWER:\n{answer}"
    )
    return score_with_llm(system_prompt, user_content)


# =============================================================================
# SCORECARD RUNNER
# =============================================================================


def run_scorecard(
    config: Dict[str, Any],
    test_questions: Optional[List[Dict]] = None,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """
    Chạy toàn bộ test questions qua pipeline và chấm điểm.

    Args:
        config: Pipeline config (retrieval_mode, top_k, use_rerank, ...)
        test_questions: List câu hỏi (load từ JSON nếu None)
        verbose: In kết quả từng câu

    Returns:
        List scorecard results, mỗi item là một row

    TODO Sprint 4:
    1. Load test_questions từ data/test_questions.json
    2. Với mỗi câu hỏi:
       a. Gọi rag_answer() với config tương ứng
       b. Chấm 4 metrics
       c. Lưu kết quả
    3. Tính average scores
    4. In bảng kết quả
    """
    if test_questions is None:
        with open(TEST_QUESTIONS_PATH, "r", encoding="utf-8") as f:
            test_questions = json.load(f)

    results = []
    label = config.get("label", "unnamed")

    print(f"\n{'='*70}")
    print(f"Chạy scorecard: {label}")
    print(f"Config: {config}")
    print("=" * 70)

    for q in test_questions:
        question_id = q["id"]
        query = q["question"]
        expected_answer = q.get("expected_answer", "")
        expected_sources = q.get("expected_sources", [])
        category = q.get("category", "")

        if verbose:
            print(f"\n[{question_id}] {query}")

        # --- Gọi pipeline ---
        try:
            result = rag_answer(
                query=query,
                retrieval_mode=config.get("retrieval_mode", "dense"),
                top_k_search=config.get("top_k_search", 10),
                top_k_select=config.get("top_k_select", 3),
                use_rerank=config.get("use_rerank", False),
                verbose=False,
            )
            answer = result["answer"]
            chunks_used = result["chunks_used"]

        except NotImplementedError:
            answer = "PIPELINE_NOT_IMPLEMENTED"
            chunks_used = []
        except Exception as e:
            answer = f"ERROR: {e}"
            chunks_used = []

        # --- Chấm điểm ---
        faith = score_faithfulness(query, answer, chunks_used, expected_sources, expected_answer)
        relevance = score_answer_relevance(query, answer, expected_sources, expected_answer)
        recall = score_context_recall(chunks_used, expected_sources)
        complete = score_completeness(query, answer, expected_answer, expected_sources)

        row = {
            "id": question_id,
            "category": category,
            "query": query,
            "answer": answer,
            "expected_answer": expected_answer,
            "faithfulness": faith["score"],
            "faithfulness_notes": faith["notes"],
            "relevance": relevance["score"],
            "relevance_notes": relevance["notes"],
            "context_recall": recall["score"],
            "context_recall_notes": recall["notes"],
            "completeness": complete["score"],
            "completeness_notes": complete["notes"],
            "config_label": label,
        }
        results.append(row)

        if verbose:
            print(f"  Answer: {answer[:100]}...")
            print(
                f"  Faithful: {faith['score']} | Relevant: {relevance['score']} | "
                f"Recall: {recall['score']} | Complete: {complete['score']}"
            )

    # Tính averages (bỏ qua None)
    for metric in ["faithfulness", "relevance", "context_recall", "completeness"]:
        scores = [r[metric] for r in results if r[metric] is not None]
        avg = sum(scores) / len(scores) if scores else None
        print(
            f"\nAverage {metric}: {avg:.2f}"
            if avg
            else f"\nAverage {metric}: N/A (chưa chấm)"
        )

    return results


# =============================================================================
# A/B COMPARISON
# =============================================================================


def compare_ab(
    baseline_results: List[Dict],
    variant_results: List[Dict],
    output_csv: Optional[str] = None,
) -> None:
    """
    So sánh baseline vs variant theo từng câu hỏi và tổng thể.

    TODO Sprint 4:
    Điền vào bảng sau để trình bày trong báo cáo:

    | Metric          | Baseline | Variant | Delta |
    |-----------------|----------|---------|-------|
    | Faithfulness    |   ?/5    |   ?/5   |  +/?  |
    | Answer Relevance|   ?/5    |   ?/5   |  +/?  |
    | Context Recall  |   ?/5    |   ?/5   |  +/?  |
    | Completeness    |   ?/5    |   ?/5   |  +/?  |

    Câu hỏi cần trả lời:
    - Variant tốt hơn baseline ở câu nào? Vì sao?
    - Biến nào (chunking / hybrid / rerank) đóng góp nhiều nhất?
    - Có câu nào variant lại kém hơn baseline không? Tại sao?
    """
    metrics = ["faithfulness", "relevance", "context_recall", "completeness"]

    print(f"\n{'='*70}")
    print("A/B Comparison: Baseline vs Variant")
    print("=" * 70)
    print(f"{'Metric':<20} {'Baseline':>10} {'Variant':>10} {'Delta':>8}")
    print("-" * 55)

    for metric in metrics:
        b_scores = [r[metric] for r in baseline_results if r[metric] is not None]
        v_scores = [r[metric] for r in variant_results if r[metric] is not None]

        b_avg = sum(b_scores) / len(b_scores) if b_scores else None
        v_avg = sum(v_scores) / len(v_scores) if v_scores else None
        delta = (v_avg - b_avg) if (b_avg and v_avg) else None

        b_str = f"{b_avg:.2f}" if b_avg else "N/A"
        v_str = f"{v_avg:.2f}" if v_avg else "N/A"
        d_str = f"{delta:+.2f}" if delta else "N/A"

        print(f"{metric:<20} {b_str:>10} {v_str:>10} {d_str:>8}")

    # Per-question comparison
    print(
        f"\n{'Câu':<6} {'Baseline F/R/Rc/C':<22} {'Variant F/R/Rc/C':<22} {'Better?':<10}"
    )
    print("-" * 65)

    b_by_id = {r["id"]: r for r in baseline_results}
    for v_row in variant_results:
        qid = v_row["id"]
        b_row = b_by_id.get(qid, {})

        b_scores_str = "/".join([str(b_row.get(m, "?")) for m in metrics])
        v_scores_str = "/".join([str(v_row.get(m, "?")) for m in metrics])

        # So sánh đơn giản
        b_total = sum(b_row.get(m, 0) or 0 for m in metrics)
        v_total = sum(v_row.get(m, 0) or 0 for m in metrics)
        better = (
            "Variant"
            if v_total > b_total
            else ("Baseline" if b_total > v_total else "Tie")
        )

        print(f"{qid:<6} {b_scores_str:<22} {v_scores_str:<22} {better:<10}")

    # Export to CSV
    if output_csv:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = RESULTS_DIR / output_csv
        combined = baseline_results + variant_results
        if combined:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=combined[0].keys())
                writer.writeheader()
                writer.writerows(combined)
            print(f"\nKết quả đã lưu vào: {csv_path}")


# =============================================================================
# REPORT GENERATOR
# =============================================================================


def generate_scorecard_summary(results: List[Dict], label: str) -> str:
    """
    Tạo báo cáo tóm tắt scorecard dạng markdown.

    TODO Sprint 4: Cập nhật template này theo kết quả thực tế của nhóm.
    """
    metrics = ["faithfulness", "relevance", "context_recall", "completeness"]
    averages = {}
    for metric in metrics:
        scores = [r[metric] for r in results if r[metric] is not None]
        averages[metric] = sum(scores) / len(scores) if scores else None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    md = f"""# Scorecard: {label}
Generated: {timestamp}

## Summary

| Metric | Average Score |
|--------|--------------|
"""
    for metric, avg in averages.items():
        avg_str = f"{avg:.2f}/5" if avg else "N/A"
        md += f"| {metric.replace('_', ' ').title()} | {avg_str} |\n"

    md += "\n## Per-Question Results\n\n"
    md += "| ID | Category | Faithful | Relevant | Recall | Complete | Notes |\n"
    md += "|----|----------|----------|----------|--------|----------|-------|\n"

    for r in results:
        md += (
            f"| {r['id']} | {r['category']} | {r.get('faithfulness', 'N/A')} | "
            f"{r.get('relevance', 'N/A')} | {r.get('context_recall', 'N/A')} | "
            f"{r.get('completeness', 'N/A')} | {r.get('faithfulness_notes', '')[:50]} |\n"
        )

    return md


# =============================================================================
# MAIN — Chạy evaluation
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Sprint 4: Evaluation & Scorecard")
    print("=" * 60)

    # Kiểm tra test questions
    print(f"\nLoading test questions từ: {TEST_QUESTIONS_PATH}")
    try:
        with open(TEST_QUESTIONS_PATH, "r", encoding="utf-8") as f:
            test_questions = json.load(f)
        print(f"Tìm thấy {len(test_questions)} câu hỏi")

        # In preview
        for q in test_questions[:3]:
            print(f"  [{q['id']}] {q['question']} ({q['category']})")
        print("  ...")

    except FileNotFoundError:
        print("Không tìm thấy file test_questions.json!")
        test_questions = []

    # --- Chạy Baseline ---
    print("\n--- Chạy Baseline ---")
    print("Lưu ý: Cần hoàn thành Sprint 2 trước khi chạy scorecard!")
    try:
        baseline_results = run_scorecard(
            config=BASELINE_CONFIG,
            test_questions=test_questions,
            verbose=True,
        )

        # Save scorecard
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        baseline_md = generate_scorecard_summary(baseline_results, "baseline_dense")
        scorecard_path = RESULTS_DIR / "scorecard_baseline.md"
        scorecard_path.write_text(baseline_md, encoding="utf-8")
        print(f"\nScorecard lưu tại: {scorecard_path}")

    except NotImplementedError:
        print("Pipeline chưa implement. Hoàn thành Sprint 2 trước.")
        baseline_results = []

    # --- Chạy Variant (sau khi Sprint 3 hoàn thành) ---
    print("\n--- Chạy Variant (Hybrid) ---")
    variant_results = run_scorecard(
        config=VARIANT_CONFIG,
        test_questions=test_questions,
        verbose=True,
    )
    variant_md = generate_scorecard_summary(variant_results, VARIANT_CONFIG["label"])
    (RESULTS_DIR / "scorecard_variant.md").write_text(variant_md, encoding="utf-8")

    # --- A/B Comparison ---
    if baseline_results and variant_results:
        compare_ab(baseline_results, variant_results, output_csv="ab_comparison.csv")
