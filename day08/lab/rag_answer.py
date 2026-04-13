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

TOP_K_SEARCH = 20  # Tăng lên để Reranker có đủ dữ liệu lọt lưới
TOP_K_SELECT = 5  # Số chunk gửi vào prompt sau rerank/select
DENSE_WEIGHT = 0.5  # Cân bằng mặc định
SPARSE_WEIGHT = 0.5

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


# =============================================================================
# RETRIEVAL — DENSE (Vector Search)
# =============================================================================


def retrieve_dense(query: str, top_k: int = TOP_K_SEARCH) -> List[Dict[str, Any]]:
    """
    Dense retrieval: tìm kiếm theo embedding similarity trong ChromaDB.
    """
    client_db = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    collection = client_db.get_collection("rag_lab")

    # 1. Embed query
    query_embedding = get_embedding(query)

    # 2. Query ChromaDB
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
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


def retrieve_sparse(query: str, top_k: int = TOP_K_SEARCH) -> List[Dict[str, Any]]:
    """
    Sparse retrieval: tìm kiếm theo keyword (BM25).
    """
    client_db = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    collection = client_db.get_collection("rag_lab")

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


def build_context_block(chunks: List[Dict[str, Any]]) -> str:
    """
    Đóng gói danh sách chunks thành context block để đưa vào prompt.
    """
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        source = meta.get("source", "unknown")
        # Clean source just in case
        clean_source = source.split("/")[-1].split("\\")[-1]
        
        text = chunk.get("text", "")

        context_parts.append(f"[{i}] NGUỒN: {clean_source}\n{text}")

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
        # TĂNG snipet length lên 500 để LLM thấy đủ thông tin
        snippet = c["text"][:500].replace("\n", " ")
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

    config = {
        "retrieval_mode": retrieval_mode,
        "top_k_search": top_k_search,
        "top_k_select": top_k_select,
        "use_rerank": use_rerank,
    }

    # --- Bước 1: Retrieve ---
    if retrieval_mode == "dense":
        candidates = retrieve_dense(query, top_k=top_k_search)
    elif retrieval_mode == "sparse":
        candidates = retrieve_sparse(query, top_k=top_k_search)
    elif retrieval_mode == "hybrid":
        # DYNAMIC WEIGHTS
        weights = classify_query(query)
        candidates = retrieve_hybrid(
            query, 
            top_k=top_k_search, 
            dense_weight=weights["dense"], 
            sparse_weight=weights["sparse"]
        )
    else:
        raise ValueError(f"retrieval_mode không hợp lệ: {retrieval_mode}")

    if verbose:
        print(f"[RAG] Retrieved {len(candidates)} candidates (mode={retrieval_mode})")

    # --- Bước 2: Rerank (optional) ---
    if use_rerank:
        # LLM RERANKING
        candidates = rerank_with_llm(query, candidates, top_k=top_k_select)
    else:
        candidates = candidates[:top_k_select]

    # if verbose:
    #     print(f"[RAG] After select: {len(candidates)} chunks")

    # --- Bước 3: Build context và prompt ---
    context_block = build_context_block(candidates)
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
