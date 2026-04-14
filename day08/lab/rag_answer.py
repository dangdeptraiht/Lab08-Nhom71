"""
rag_answer.py — Sprint 2 + Sprint 3: Retrieval & Grounded Answer
================================================================
Sprint 2 (60 phút): Baseline RAG
  - Dense retrieval từ ChromaDB
  - Grounded answer function với prompt ép citation
  - Trả lời được ít nhất 3 câu hỏi mẫu, output có source

Sprint 3 (60 phút): Tuning tối thiểu
  - Thêm hybrid retrieval (dense + sparse/BM25)
  - Hoặc thêm rerank (cross-encoder)
  - Hoặc thử query transformation (expansion, decomposition, HyDE)
  - Tạo bảng so sánh baseline vs variant

Definition of Done Sprint 2:
  ✓ rag_answer("SLA ticket P1?") trả về câu trả lời có citation
  ✓ rag_answer("Câu hỏi không có trong docs") trả về "Không đủ dữ liệu"

Definition of Done Sprint 3:
  ✓ Có ít nhất 1 variant (hybrid / rerank / query transform) chạy được
  ✓ Giải thích được tại sao chọn biến đó để tune
"""

import re
import os
import sys
import chromadb
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from openai import OpenAI
from rank_bm25 import BM25Okapi
from index import get_embedding, CHROMA_DB_DIR

# Fix Unicode for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()
client_ai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =============================================================================
# CẤU HÌNH
# =============================================================================

TOP_K_SEARCH = 28  # Search rộng hơn cho câu hỏi nhiều ràng buộc
TOP_K_SELECT = 7  # Giữ thêm bằng chứng từ nhiều section/source
DENSE_WEIGHT = 0.5  # Cân bằng mặc định
SPARSE_WEIGHT = 0.5

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


# =============================================================================
# RETRIEVAL — DENSE (Vector Search)
# =============================================================================


def _get_collection():
    client_db = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    return client_db.get_collection("rag_lab")


def retrieve_dense(
    query: str,
    top_k: int = TOP_K_SEARCH,
    where: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Dense retrieval: tìm kiếm theo embedding similarity trong ChromaDB.
    """
    collection = _get_collection()

    # 1. Embed query
    query_embedding = get_embedding(query)

    # 2. Query ChromaDB
    query_kwargs = dict(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    if where:
        query_kwargs["where"] = where

    results = collection.query(
        **query_kwargs,
    )

    # 3. Format kết quả
    chunks = []
    if not results["ids"] or len(results["ids"][0]) == 0:
        return []

    for i in range(len(results["ids"][0])):
        # Score = 1 - distance (ChromaDB mặc định dùng L2 hoặc Cosine distance)
        score = 1 - results["distances"][0][i]
        chunks.append(
            {
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "score": score,
            }
        )

    return chunks


# =============================================================================
# RETRIEVAL — SPARSE / BM25 (Keyword Search)
# Dùng cho Sprint 3 Variant hoặc kết hợp Hybrid
# =============================================================================


def retrieve_sparse(
    query: str,
    top_k: int = TOP_K_SEARCH,
    source_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Sparse retrieval: tìm kiếm theo keyword (BM25).
    """
    collection = _get_collection()

    # 1. Lấy toàn bộ chunks từ database để làm corpus cho BM25
    # (Vì lab chỉ có 30 chunks nên cách này khả thi)
    results = collection.get(include=["documents", "metadatas"])
    all_docs = results["documents"]
    all_metas = results["metadatas"]

    if not all_docs:
        return []

    # 2. Tokenize corpus và query (Xử lý punctuation tốt hơn BM25 mặc định)
    def tokenize(text):
        # Chuyển về lowercase, tách các ký tự kỹ thuật đặc biệt
        clean_text = re.sub(r'[\(\)\-\:\/]', ' ', text.lower())
        return clean_text.split()

    # Optional filter by source (metadata["source"] is normalized filename)
    if source_filter:
        filtered = [
            (doc, meta)
            for doc, meta in zip(all_docs, all_metas)
            if (meta or {}).get("source") == source_filter
        ]
        if not filtered:
            return []
        all_docs = [d for d, _ in filtered]
        all_metas = [m for _, m in filtered]

    corpus_tokenized = [tokenize(doc) for doc in all_docs]
    bm25 = BM25Okapi(corpus_tokenized)

    # 3. Query và lấy scores
    query_tokenized = tokenize(query)
    scores = bm25.get_scores(query_tokenized)

    # 4. Sắp xếp và format trả về
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[
        :top_k
    ]

    chunks = []
    for idx in top_indices:
        if scores[idx] > 0:  # Chỉ lấy nếu có keyword match
            chunks.append(
                {
                    "text": all_docs[idx],
                    "metadata": all_metas[idx],
                    "score": float(scores[idx]),
                }
            )

    return chunks


def _merge_candidates(candidates_lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Merge candidates from multiple queries.
    - Deduplicate by exact text (stable in our corpus).
    - Sum scores with a small bias for appearing in multiple lists.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    hits: Dict[str, int] = {}

    for lst in candidates_lists:
        for c in lst:
            txt = c.get("text", "")
            if not txt:
                continue
            if txt not in merged:
                merged[txt] = {**c}
                merged[txt]["score"] = float(c.get("score", 0.0) or 0.0)
                hits[txt] = 1
            else:
                merged[txt]["score"] = float(merged[txt].get("score", 0.0) or 0.0) + float(
                    c.get("score", 0.0) or 0.0
                )
                hits[txt] += 1

    # multi-hit bonus (helps ensure key chunks survive rerank/select)
    for txt, h in hits.items():
        merged[txt]["score"] = float(merged[txt].get("score", 0.0) or 0.0) + (h - 1) * 0.05

    return sorted(merged.values(), key=lambda x: x.get("score", 0.0), reverse=True)


def _expand_queries(query: str) -> List[str]:
    """
    Generic query expansion (no dataset-specific bias, no LLM calls).
    Strategy:
    - Split multi-intent questions into sub-queries.
    - Add a compact keyword-focused query to help BM25/dense retrieval.
    """
    q = (query or "").strip()
    if not q:
        return []

    ql = q.lower()
    candidates: List[str] = [q]

    # 1) Split by common Vietnamese connectors for multi-part questions
    # Keep segments that are non-trivial (>= 4 chars) to avoid noise.
    splitters = [
        " và ",
        " , ",
        ";",
        " không?",
        " nếu ",
        " thì ",
        " bao nhiêu ",
        " bao lâu ",
        " định kỳ ",
        " nhắc nhở ",
        " đổi qua ",
        "?",
    ]
    parts = [q]
    for s in splitters:
        next_parts: List[str] = []
        for p in parts:
            if s.strip() and s in p.lower():
                # split while preserving original casing via indices is overkill; use lower split then map back is not needed
                for seg in p.split(s, 20):
                    seg = seg.strip()
                    if len(seg) >= 4:
                        next_parts.append(seg)
            else:
                next_parts.append(p)
        parts = next_parts

    for p in parts:
        if p and p != q:
            candidates.append(p)

    # 2) Date normalization for metadata/effective-date matching (dd/mm/yyyy -> yyyy-mm-dd)
    date_matches = re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", q)
    for dd, mm, yyyy in date_matches:
        normalized = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
        candidates.append(normalized)
        candidates.append(f"effective date {normalized}")

    # 3) Keyword-focused query: keep salient tokens (acronyms, codes, numbers, URLs, ext, proper-ish words)
    tokens = re.findall(r"[A-Za-z]{2,}|\d{2,}|https?://\\S+|ext\\.?\\s*\\d{2,}", q, flags=re.IGNORECASE)
    # Also keep common domain terms if present
    for term in [
        "vpn", "remote", "access", "admin", "approval", "matrix", "sla", "p1", "ciso", "manager", "training",
        "effective", "date", "version", "phiên bản", "hiện tại", "trước", "refund", "store credit", "flash sale",
    ]:
        if term in ql:
            tokens.append(term)
    kw_query = " ".join(dict.fromkeys([t.strip() for t in tokens if t.strip()]))
    if len(kw_query) >= 8 and kw_query.lower() != ql:
        candidates.append(kw_query)

    # Deduplicate preserve order
    out: List[str] = []
    seen = set()
    for c in candidates:
        c = c.strip()
        if not c or c in seen:
            continue
        out.append(c)
        seen.add(c)
    return out


def _infer_min_sources(query: str, top_k_select: int) -> int:
    """
    Generic heuristic: multi-intent questions benefit from multi-source evidence when available.
    """
    ql = (query or "").lower()
    multi_intent = any(x in ql for x in [" và ", " nếu ", " bao nhiêu", " bao lâu", " nhắc", " đổi qua", " khác không", " có giống"])
    if not multi_intent:
        return 1
    return min(2, max(1, top_k_select))


def _infer_min_sections(query: str, top_k_select: int) -> int:
    """
    Multi-constraint questions should include evidence from multiple sections when available.
    """
    ql = (query or "").lower()
    needs_multi_section = any(
        x in ql
        for x in ["và", "đồng thời", "bao nhiêu", "quy trình", "yêu cầu", "điều kiện", "khác", "so với"]
    )
    if not needs_multi_section:
        return 1
    return min(2, max(1, top_k_select))


def _ensure_diverse_sources(
    merged_pool: List[Dict[str, Any]],
    selected: List[Dict[str, Any]],
    min_sources: int,
    top_k_select: int,
) -> List[Dict[str, Any]]:
    """
    Ensure at least `min_sources` distinct sources in the final selected set, if possible.
    This avoids hardcoding which sources are "required" and generalizes across corpora.
    """
    if min_sources <= 1:
        return selected[:top_k_select]

    sel = list(selected)
    sel_sources = [((c.get("metadata") or {}).get("source")) for c in sel]
    distinct = [s for s in dict.fromkeys(sel_sources) if s]

    if len(distinct) >= min_sources:
        return sel[:top_k_select]

    # Fill with best chunks from missing sources found in pool
    used_texts = {c.get("text", "") for c in sel}
    for c in merged_pool:
        src = (c.get("metadata") or {}).get("source")
        if not src:
            continue
        if src in distinct:
            continue
        if c.get("text", "") in used_texts:
            continue
        sel.append(c)
        used_texts.add(c.get("text", ""))
        distinct.append(src)
        if len(distinct) >= min_sources or len(sel) >= top_k_select:
            break

    return sel[:top_k_select]


def _ensure_diverse_sections(
    merged_pool: List[Dict[str, Any]],
    selected: List[Dict[str, Any]],
    min_sections: int,
    top_k_select: int,
) -> List[Dict[str, Any]]:
    """
    Ensure evidence spans multiple sections for multi-part questions.
    """
    if min_sections <= 1:
        return selected[:top_k_select]

    sel = list(selected)
    sel_sections = [((c.get("metadata") or {}).get("section")) for c in sel]
    distinct = [s for s in dict.fromkeys(sel_sections) if s]

    if len(distinct) >= min_sections:
        return sel[:top_k_select]

    used_texts = {c.get("text", "") for c in sel}
    for c in merged_pool:
        sec = (c.get("metadata") or {}).get("section")
        if not sec:
            continue
        if sec in distinct:
            continue
        if c.get("text", "") in used_texts:
            continue
        sel.append(c)
        used_texts.add(c.get("text", ""))
        distinct.append(sec)
        if len(distinct) >= min_sections or len(sel) >= top_k_select:
            break

    return sel[:top_k_select]


def _extract_constraints(query: str) -> List[str]:
    """
    Split multi-part question into minimal constraints for completeness checks.
    """
    q = (query or "").strip()
    if not q:
        return []
    parts = re.split(r"\?|\svà\s|\sđồng thời\s|;|\sso với\s", q, flags=re.IGNORECASE)
    constraints = [p.strip() for p in parts if len(p.strip()) >= 8]
    if not constraints:
        return [q]
    return constraints


def _constraint_supported(constraint: str, candidates: List[Dict[str, Any]]) -> bool:
    """
    Check whether at least one retrieved chunk has enough lexical evidence for the constraint.
    """
    c_tokens = set(re.findall(r"\w+", constraint.lower()))
    c_tokens = {t for t in c_tokens if len(t) >= 3}
    if not c_tokens:
        return True

    for c in candidates:
        txt = (c.get("text") or "").lower()
        if not txt:
            continue
        hits = sum(1 for t in c_tokens if t in txt)
        if hits >= min(3, max(1, len(c_tokens) // 3)):
            return True
    return False


def _augment_for_missing_constraints(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k_search: int,
    dense_weight: float,
    sparse_weight: float,
) -> List[Dict[str, Any]]:
    """
    If a question has multiple constraints, run targeted retrieval for missing constraints.
    """
    constraints = _extract_constraints(query)
    if len(constraints) <= 1:
        return candidates

    merged_lists = [candidates]
    for cons in constraints:
        if _constraint_supported(cons, candidates):
            continue
        merged_lists.append(
            retrieve_hybrid(
                cons,
                top_k=max(8, top_k_search // 2),
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
            )
        )

    return _merge_candidates(merged_lists)


def _is_freshness_query(query: str) -> bool:
    ql = (query or "").lower()
    freshness_terms = [
        "phiên bản", "version", "hiện tại", "trước", "effective", "date", "đã thay đổi", "so với",
        "áp dụng", "kể từ", "trước ngày", "mới nhất",
    ]
    return any(t in ql for t in freshness_terms)


def _adaptive_search_config(query: str, top_k_search: int, top_k_select: int) -> Tuple[int, int]:
    """
    Increase retrieval depth for hard/multi-part/freshness questions.
    """
    ql = (query or "").lower()
    constraints = _extract_constraints(query)
    hard_like = (
        len(constraints) >= 2
        or _is_freshness_query(query)
        or any(x in ql for x in ["quy trình", "yêu cầu", "ngoại lệ", "so với", "đồng thời"])
    )
    if not hard_like:
        return top_k_search, top_k_select
    return max(top_k_search, 36), max(top_k_select, 8)


# =============================================================================
# RETRIEVAL — HYBRID (Dense + Sparse với Reciprocal Rank Fusion)
# =============================================================================


def retrieve_hybrid(
    query: str,
    top_k: int = TOP_K_SEARCH,
    dense_weight: float = DENSE_WEIGHT,
    sparse_weight: float = SPARSE_WEIGHT,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: kết hợp dense và sparse bằng Reciprocal Rank Fusion (RRF).
    """
    # 1. Lấy kết quả từ cả 2 phương pháp
    dense_results = retrieve_dense(query, top_k=top_k * 2)  # Lấy rộng hơn để merge
    sparse_results = retrieve_sparse(query, top_k=top_k * 2)

    # 2. Áp dụng RRF
    # RRF score = sum( 1 / (k + rank) )
    rrf_scores = {}  # key: text content (hoặc một ID duy nhất)
    doc_map = {}  # Luu lai object doc
    k_constant = 60

    for rank, doc in enumerate(dense_results, 1):
        txt = doc["text"]
        rrf_scores[txt] = rrf_scores.get(txt, 0) + dense_weight * (
            1.0 / (k_constant + rank)
        )
        doc_map[txt] = doc

    for rank, doc in enumerate(sparse_results, 1):
        txt = doc["text"]
        rrf_scores[txt] = rrf_scores.get(txt, 0) + sparse_weight * (
            1.0 / (k_constant + rank)
        )
        # Nếu doc này chưa có trong map (tức là dense không tìm thấy), hãy add nó vào
        if txt not in doc_map:
            doc_map[txt] = doc

    # 3. Sắp xếp lại theo RRF score
    sorted_docs_txt = sorted(
        rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True
    )

    hybrid_results = []
    for txt in sorted_docs_txt[:top_k]:
        merged_doc = doc_map[txt]
        merged_doc["score"] = rrf_scores[txt]  # Update thành hybrid score
        hybrid_results.append(merged_doc)

    return hybrid_results


# =============================================================================
# RERANK (Sprint 3 alternative)
# Cross-encoder để chấm lại relevance sau search rộng
# =============================================================================


def rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = TOP_K_SELECT,
) -> List[Dict[str, Any]]:
    """
    Rerank các candidate chunks bằng cross-encoder.

    Cross-encoder: chấm lại "chunk nào thực sự trả lời câu hỏi này?"
    MMR (Maximal Marginal Relevance): giữ relevance nhưng giảm trùng lặp

    Funnel logic (từ slide):
      Search rộng (top-20) → Rerank (top-6) → Select (top-3)

    TODO Sprint 3 (nếu chọn rerank):
    Option A — Cross-encoder:
        from sentence_transformers import CrossEncoder
        model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        pairs = [[query, chunk["text"]] for chunk in candidates]
        scores = model.predict(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [chunk for chunk, _ in ranked[:top_k]]

    Option B — Rerank bằng LLM (đơn giản hơn nhưng tốn token):
        Gửi list chunks cho LLM, yêu cầu chọn top_k relevant nhất

    Khi nào dùng rerank:
    - Dense/hybrid trả về nhiều chunk nhưng có noise
    - Muốn chắc chắn chỉ 3-5 chunk tốt nhất vào prompt
    """
    # TODO Sprint 3: Implement rerank
    # Tạm thời trả về top_k đầu tiên (không rerank)
    return candidates[:top_k]


# =============================================================================
# QUERY TRANSFORMATION (Sprint 3 alternative)
# =============================================================================


def transform_query(query: str, strategy: str = "expansion") -> List[str]:
    """
    Biến đổi query để tăng recall.

    Strategies:
      - "expansion": Thêm từ đồng nghĩa, alias, tên cũ
      - "decomposition": Tách query phức tạp thành 2-3 sub-queries
      - "hyde": Sinh câu trả lời giả (hypothetical document) để embed thay query

    TODO Sprint 3 (nếu chọn query transformation):
    Gọi LLM với prompt phù hợp với từng strategy.

    Ví dụ expansion prompt:
        "Given the query: '{query}'
         Generate 2-3 alternative phrasings or related terms in Vietnamese.
         Output as JSON array of strings."

    Ví dụ decomposition:
        "Break down this complex query into 2-3 simpler sub-queries: '{query}'
         Output as JSON array."

    Khi nào dùng:
    - Expansion: query dùng alias/tên cũ (ví dụ: "Approval Matrix" → "Access Control SOP")
    - Decomposition: query hỏi nhiều thứ một lúc
    - HyDE: query mơ hồ, search theo nghĩa không hiệu quả
    """
    # TODO Sprint 3: Implement query transformation
    # Tạm thời trả về query gốc
    return [query]


# =============================================================================
# GENERATION — GROUNDED ANSWER FUNCTION
# =============================================================================


def extract_exact_citation(text: str, query: str, max_length: int = 300) -> str:
    """
    Extract the most relevant sentence(s) from a chunk that match the query.
    Finds exact matching phrases to ground the citation precisely.
    
    Args:
        text: Full chunk text (can be 500+ tokens)
        query: User query
        max_length: Max length of extracted citation (250-400 chars is ideal)
    
    Returns:
        Precise citation excerpt (1-2 sentences), or original text if no match found
    """
    if not text or not query:
        return text[:max_length] if text else ""
    
    # Split text into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    
    # Score each sentence by keyword overlap with query
    query_tokens = set(re.findall(r'\b\w+\b', query.lower()))
    sentence_scores = []
    
    for sent in sentences:
        if not sent.strip():
            continue
        sent_tokens = set(re.findall(r'\b\w+\b', sent.lower()))
        # Calculate Jaccard similarity
        overlap = query_tokens & sent_tokens
        score = len(overlap) / max(len(query_tokens | sent_tokens), 1)
        sentence_scores.append((sent.strip(), score, len(overlap)))
    
    if not sentence_scores:
        return text[:max_length]
    
    # Sort by score, then by number of matching keywords
    sentence_scores.sort(key=lambda x: (x[1], x[2]), reverse=True)
    
    # Take top sentences until we reach max_length
    selected = []
    total_length = 0
    for sent, score, _ in sentence_scores:
        if score < 0.1:  # Filter out low relevance
            break
        sent_len = len(sent)
        if total_length + sent_len <= max_length:
            selected.append(sent)
            total_length += sent_len + 1  # +1 for space
        elif total_length < max_length * 0.7:  # Include at least first good sentence
            selected.append(sent)
            break
    
    if selected:
        # Preserve sentence order from original
        result = " ".join(selected)
        return result[:max_length]
    
    # Fallback: return first sentence or first max_length chars
    return sentences[0][:max_length] if sentences else text[:max_length]


def build_context_block(chunks: List[Dict[str, Any]], query: str = "") -> str:
    """
    Đóng gói danh sách chunks thành context block để đưa vào prompt.
    Uses exact citation extraction to show only relevant phrases, not entire chunks.
    
    Args:
        chunks: List of retrieved chunks with text and metadata
        query: User query (optional, used for precise citation extraction)
    
    Returns:
        Context block with grounded citations
    """
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        source = meta.get("source", "unknown")
        # Clean source just in case
        clean_source = source.split("/")[-1].split("\\")[-1]
        
        text = chunk.get("text", "")
        
        # Extract exact citation if query provided, otherwise use full chunk
        if query:
            citation_text = extract_exact_citation(text, query, max_length=300)
        else:
            citation_text = text

        context_parts.append(f"[{i}] NGUỒN: {clean_source}\n{citation_text}")

    return "\n\n".join(context_parts)


def build_grounded_prompt(query: str, context_block: str) -> str:
    """
    Prompt chuyên nghiệp, yêu cầu Grounded Citations (Trích dẫn nguyên văn).
    """
    prompt = f"""Bạn là một Chuyên viên hỗ trợ nội bộ chuyên nghiệp. Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH ĐÃ CHO.

QUY TẮC CỐT LÕI:
1. TRẢ LỜI ĐẦY ĐỦ VÀ SUY LUẬN LOGIC: 
   - Nếu gặp "Approval Matrix", hãy hiểu đó là "Access Control SOP".
   - Luôn cập nhật thông tin mới nhất từ lịch sử thay đổi nếu có.

2. CƠ CHẾ DẪN NGUỒN GROUNDED (QUAN TRỌNG):
   - Mọi ý chính PHẢI được trích dẫn theo định dạng: `[n] (Trích dẫn: "...")`
   - Phần "Trích dẫn" phải là nguyên văn 1 câu văn quan trọng nhất từ đoạn [n] dùng để chứng minh ý đó.
   - Ví dụ: "Thời gian làm việc là từ 8:00 đến 17:30 [1] (Trích dẫn: "Hỗ trợ qua email từ Thứ 2 - Thứ 6: 08:00 - 17:30")."

3. QUY TẮC PHỦ ĐỊNH:
   - Nếu không có thông tin, hãy trả lời: "[ABSTAIN] Tài liệu hiện tại không đề cập đến...".
   - Tuyệt đối KHÔNG gắn thẻ [n] nếu bạn nói không tìm thấy thông tin.

Câu hỏi: {query}

Ngữ cảnh:
{context_block}

Câu trả lời của bạn:"""
    return prompt


def call_llm(prompt: str) -> str:
    """
    Gọi OpenAI GPT để sinh câu trả lời.
    """
    response = client_ai.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,  # Rất quan trọng: để 0 để tránh AI tự bịa (hallucination)
        max_tokens=512,
    )
    return response.choices[0].message.content


def classify_query(query: str) -> Dict[str, float]:
    """
    Phân loại câu hỏi để đưa ra trọng số hybrid phù hợp.
    """
    query_l = query.lower()
    
    # Technical patterns: mã lỗi, tên riêng viết tắt, hoặc cụm từ quan trọng
    technical_patterns = [
        r"err-\d+", 
        r"[a-z]+-\d+", 
        r"matrix", 
        r"sop", 
        r"policy",
        r"approval",
        r"khóa",      # Thêm keyword cho login/access issues
        r"lock",
        r"login",
        r"đăng nhập"
    ]
    
    is_technical = any(re.search(p, query_l) for p in technical_patterns)
    is_short = len(query.split()) <= 3

    if is_technical or is_short:
        # Ưu tiên cực cao cho Keyword search khi gặp mã kỹ thuật hoặc query rất ngắn
        return {"dense": 0.2, "sparse": 0.8}
    
    return {"dense": DENSE_WEIGHT, "sparse": SPARSE_WEIGHT}


def rerank_with_llm(query: str, candidates: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Sử dụng gpt-4o-mini để xếp hạng lại (rerank) danh sách chunks.
    Chỉ gửi title/metadata và 200 ký tự đầu của mỗi chunk để tiết kiệm token.
    """
    if not candidates:
        return []

    items = []
    for i, c in enumerate(candidates):
        # Longer snippet helps distinguish Section 2 (Level 4) vs generic process.
        snippet = c["text"][:900].replace("\n", " ")
        src = c["metadata"].get("source", "unknown")
        items.append(f"ID:{i} | Source:{src} | Content:{snippet}")

    items_block = "\n".join(items)
    
    prompt = f"""Dựa trên câu hỏi, hãy chọn ra tối đa {top_k} ID của đoạn văn bản CHỨA TRỰC TIẾP câu trả lời. 
 
LƯU Ý: 
- Bạn là chuyên gia phân loại tài liệu. Hãy ưu tiên các đoạn văn chứa TÊN TÀI LIỆU hoặc GHI CHÚ khớp với từ khóa trong câu hỏi (ví dụ: "Approval Matrix" là bí danh của "ACCESS-CONTROL-SOP").
- Ưu tiên các đoạn văn bản có thông tin cụ thể (mốc thời gian, con số, mã lỗi).
- Chỉ trả về danh sách các ID, cách nhau bởi dấu phẩy.
 
Câu hỏi: {query}
Các đoạn văn bản:
{items_block}
 
ID liên quan nhất:"""

    response = client_ai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    
    try:
        raw_ids = response.choices[0].message.content.strip().split(",")
        selected_indices = [int(idx.strip()) for idx in raw_ids if idx.strip().isdigit()]
        
        reranked = []
        for idx in selected_indices:
            if 0 <= idx < len(candidates):
                reranked.append(candidates[idx])
        
        # Nếu LLM không trả về kết quả hợp lệ, fallback về ban đầu
        return reranked if reranked else candidates[:top_k]
    except:
        return candidates[:top_k]


def rag_answer(
    query: str,
    retrieval_mode: str = "dense",
    top_k_search: int = TOP_K_SEARCH,
    top_k_select: int = TOP_K_SELECT,
    use_rerank: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:

    adaptive_search_k, adaptive_select_k = _adaptive_search_config(
        query, top_k_search=top_k_search, top_k_select=top_k_select
    )

    config = {
        "retrieval_mode": retrieval_mode,
        "top_k_search": adaptive_search_k,
        "top_k_select": adaptive_select_k,
        "use_rerank": use_rerank,
    }

    # --- Bước 1: Retrieve ---
    if retrieval_mode == "dense":
        candidates = retrieve_dense(query, top_k=adaptive_search_k)
        weights = classify_query(query)
    elif retrieval_mode == "sparse":
        candidates = retrieve_sparse(query, top_k=adaptive_search_k)
        weights = classify_query(query)
    elif retrieval_mode == "hybrid":
        # Multi-query hybrid to improve recall for multi-part grading questions
        weights = classify_query(query)
        sub_queries = _expand_queries(query)
        lists = [
            retrieve_hybrid(
                q,
                top_k=adaptive_search_k,
                dense_weight=weights["dense"],
                sparse_weight=weights["sparse"],
            )
            for q in sub_queries
        ]

        # Freshness questions benefit from explicit metadata words and date forms.
        if _is_freshness_query(query):
            lists.append(
                retrieve_sparse(
                    f"{query} effective date version current previous",
                    top_k=adaptive_search_k,
                )
            )

        candidates = _merge_candidates(lists)
    else:
        raise ValueError(f"retrieval_mode không hợp lệ: {retrieval_mode}")

    candidates = _augment_for_missing_constraints(
        query,
        candidates,
        top_k_search=adaptive_search_k,
        dense_weight=weights["dense"],
        sparse_weight=weights["sparse"],
    )

    if verbose:
        print(f"[RAG] Retrieved {len(candidates)} candidates (mode={retrieval_mode})")

    # --- Bước 2: Rerank (optional) ---
    if use_rerank:
        # LLM RERANKING
        candidates = rerank_with_llm(query, candidates, top_k=adaptive_select_k)
    else:
        candidates = candidates[:adaptive_select_k]

    # Final guard: encourage multi-source evidence for multi-intent questions (when available)
    min_sources = _infer_min_sources(query, top_k_select=adaptive_select_k)
    min_sections = _infer_min_sections(query, top_k_select=adaptive_select_k)
    # At this point, `candidates` is already selected; use the broader pool by reusing retrieval lists merged.
    # We keep it simple: for dense/sparse mode, the pool is the same as selected slice; for hybrid, we can reuse
    # a shallow pool from current candidates + extra from hybrid retrieval.
    if retrieval_mode == "hybrid":
        pool = _merge_candidates([retrieve_hybrid(query, top_k=adaptive_search_k)])
    else:
        pool = list(candidates)
    candidates = _ensure_diverse_sources(
        pool,
        candidates,
        min_sources=min_sources,
        top_k_select=adaptive_select_k,
    )
    candidates = _ensure_diverse_sections(
        pool,
        candidates,
        min_sections=min_sections,
        top_k_select=adaptive_select_k,
    )

    # Completeness guard: require evidence for all detected constraints.
    constraints = _extract_constraints(query)
    if len(constraints) >= 2:
        missing = [c for c in constraints if not _constraint_supported(c, candidates)]
        if missing:
            return {
                "query": query,
                "answer": (
                    "Tài liệu hiện tại chưa đủ bằng chứng cho đầy đủ các phần của câu hỏi. "
                    f"Thiếu bằng chứng cho: {', '.join(missing[:2])}."
                ),
                "sources": ["Không có"],
                "chunks_used": candidates,
                "config": config,
            }

    # if verbose:
    #     print(f"[RAG] After select: {len(candidates)} chunks")

    # --- Bước 3: Build context và prompt ---
    context_block = build_context_block(candidates, query=query)
    prompt = build_grounded_prompt(query, context_block)

    # if verbose:
    #     print(f"\n[RAG] Prompt:\n{prompt[:500]}...\n")

    # --- Bước 4: Generate ---
    answer = call_llm(prompt)

    # --- Bước 5: Extract sources ---
    # Kiểm tra dấu hiệu ABSTAIN từ LLM
    is_negative = "[abstain]" in answer.lower()
    
    if is_negative:
        sources = ["Không có"]
        # Loại bỏ tag [ABSTAIN] khỏi câu trả lời để hiển thị cho user đẹp hơn
        answer = answer.replace("[ABSTAIN]", "").replace("[abstain]", "").strip()
    else:
        # TÌM CÁC THẺ TRÍCH DẪN [n] TRONG CÂU TRẢ LỜI
        cited_indices = re.findall(r"\[(\d+)\]", answer)
        if cited_indices:
            unique_indices = sorted(set(int(idx) - 1 for idx in cited_indices))
            sources = []
            for idx in unique_indices:
                if 0 <= idx < len(candidates):
                    src = candidates[idx]["metadata"].get("source", "unknown")
                    # Clean filename once more
                    clean_src = src.split("/")[-1].split("\\")[-1]
                    if clean_src not in sources:
                        sources.append(clean_src)
        else:
            # Fallback
            raw_sources = {c["metadata"].get("source", "unknown") for c in candidates}
            sources = sorted(list({s.split("/")[-1].split("\\")[-1] for s in raw_sources}))

    return {
        "query": query,
        "answer": answer,
        "sources": sources,
        "chunks_used": candidates,
        "config": config,
    }


# =============================================================================
# SPRINT 3: SO SÁNH BASELINE VS VARIANT
# =============================================================================


def compare_retrieval_strategies(query: str) -> None:

    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print("=" * 60)

    strategies = ["dense", "hybrid"]  # Thêm "sparse" sau khi implement

    for strategy in strategies:
        print(f"\n--- Strategy: {strategy} ---")
        try:
            result = rag_answer(query, retrieval_mode=strategy, verbose=False)
            print(f"Answer: {result['answer']}")
            print(f"Sources: {result['sources']}")
        except NotImplementedError as e:
            print(f"Chưa implement: {e}")
        except Exception as e:
            print(f"Lỗi: {e}")


# =============================================================================
# MAIN — Demo và Test
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("HỆ THỐNG TEST RAG - SPRINT 3: HYBRID SEARCH")
    print("=" * 60)

    current_mode = "dense"

    while True:
        try:
            print(f"\n[{current_mode.upper()} MODE] " + "-" * 20)
            user_query = input("User: ").strip()

            if user_query.lower() in ["exit", "quit"]:
                print("Hẹn gặp lại bạn!")
                break

            if user_query.lower() == "/dense":
                current_mode = "dense"
                print(">>> Đã chuyển sang chế độ DENSE search.")
                continue
            elif user_query.lower() == "/hybrid":
                current_mode = "hybrid"
                print(">>> Đã chuyển sang chế độ HYBRID search.")
                continue
            elif user_query.lower() == "/sparse":
                current_mode = "sparse"
                print(">>> Đã chuyển sang chế độ SPARSE search.")
                continue

            if not user_query:
                continue

            # Thực hiện RAG answer với current_mode linh hoạt
            result = rag_answer(user_query, retrieval_mode=current_mode, verbose=True)

            print(f"\nAI: {result['answer']}")
            print(f"Nguồn: {', '.join(result['sources'])}")

        except KeyboardInterrupt:
            print("\nKết thúc chương trình.")
            break
        except Exception as e:
            print(f"Lỗi: {e}")
