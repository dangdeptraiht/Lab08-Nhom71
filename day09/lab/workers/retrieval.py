"""
workers/retrieval.py — Retrieval Worker
Sprint 2: Implement retrieval từ ChromaDB, trả về chunks + sources.

Input (từ AgentState):
    - task: câu hỏi cần retrieve
    - (optional) retrieved_chunks nếu đã có từ trước

Output (vào AgentState):
    - retrieved_chunks: list of {"text", "source", "score", "metadata"}
    - retrieved_sources: list of source filenames
    - worker_io_log: log input/output của worker này

Gọi độc lập để test:
    python workers/retrieval.py
"""

import os

# Load .env nếu có (cho standalone test)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────
# Worker Contract (xem contracts/worker_contracts.yaml)
# Input:  {"task": str, "top_k": int = 3}
# Output: {"retrieved_chunks": list, "retrieved_sources": list, "error": dict | None}
# ─────────────────────────────────────────────

WORKER_NAME = "retrieval_worker"
DEFAULT_TOP_K = 5


def _get_embedding_fn():
    """
    Trả về embedding function phù hợp với ChromaDB đã index.
    Ưu tiên OpenAI (text-embedding-3-small, 1536-dim) nếu có API key,
    vì collection hiện tại được index bằng OpenAI embeddings.
    Fallback sang Sentence Transformers nếu không có API key.
    """
    # Option A: OpenAI (text-embedding-3-small, 1536-dim — khớp với collection đã index)
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            def embed_openai(text: str) -> list:
                resp = client.embeddings.create(input=text, model="text-embedding-3-small")
                return resp.data[0].embedding
            return embed_openai
        except ImportError:
            pass

    # Option B: Sentence Transformers (offline, 384-dim)
    # Chú ý: nếu collection đã index bằng OpenAI, cần re-index trước khi dùng option này.
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        def embed_st(text: str) -> list:
            return model.encode([text])[0].tolist()
        return embed_st
    except ImportError:
        pass

    # Fallback: random embeddings (KHÔNG dùng production)
    import random
    def embed_random(_text: str) -> list:
        return [random.random() for _ in range(384)]
    print("WARNING: Using random embeddings (test only). Install openai or sentence-transformers.")
    return embed_random


def _get_collection():
    """
    Kết nối ChromaDB collection.
    TODO Sprint 2: Đảm bảo collection đã được build từ Step 3 trong README.
    """
    import chromadb
    client = chromadb.PersistentClient(path="./chroma_db")
    try:
        collection = client.get_collection("day09_docs")
    except Exception:
        # Auto-create nếu chưa có
        collection = client.get_or_create_collection(
            "day09_docs",
            metadata={"hnsw:space": "cosine"}
        )
        print(f"⚠️  Collection 'day09_docs' chưa có data. Chạy index script trong README trước.")
    return collection


def retrieve_dense(query: str, top_k: int = DEFAULT_TOP_K) -> list:
    """
    Dense retrieval: embed query → query ChromaDB → trả về top_k chunks.

    TODO Sprint 2: Implement phần này.
    - Dùng _get_embedding_fn() để embed query
    - Query collection với n_results=top_k
    - Format result thành list of dict

    Returns:
        list of {"text": str, "source": str, "score": float, "metadata": dict}
    """
    embed = _get_embedding_fn()
    query_embedding = embed(query)

    try:
        collection = _get_collection()
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "distances", "metadatas"]
        )

        chunks = []
        for i, (doc, dist, meta) in enumerate(zip(
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0]
        )):
            chunks.append({
                "text": doc,
                "source": meta.get("source", "unknown"),
                "score": round(1 - dist, 4),  # cosine similarity
                "metadata": meta,
            })
        return chunks

    except Exception as e:
        print(f"⚠️  ChromaDB query failed: {e}")
        return []


# ─────────────────────────────────────────────
# BM25 Index (lazy-loaded, module-level cache)
# ─────────────────────────────────────────────

_bm25_index = None       # BM25Okapi instance
_bm25_corpus: list = []  # list of {"text", "source", "metadata"}


def _tokenize(text: str) -> list:
    """
    Tokenizer đơn giản cho BM25: lowercase + split theo khoảng trắng và dấu câu.
    Phù hợp với tiếng Việt (syllable-based, space-separated) và tiếng Anh.
    """
    import re
    text = text.lower()
    # Split on whitespace and punctuation (NO trailing space bug)
    tokens = re.split(r'[\s\.,;:!?\(\)\[\]{}\-/\\|"\']+', text)
    return [t for t in tokens if len(t) > 1]


def _get_bm25_index():
    """
    Lazy-load toàn bộ docs từ ChromaDB và build BM25 index.
    Cache ở module-level để không rebuild mỗi query.
    """
    global _bm25_index, _bm25_corpus
    if _bm25_index is not None:
        return _bm25_index, _bm25_corpus

    try:
        from rank_bm25 import BM25Okapi
        collection = _get_collection()

        # Lấy tất cả docs từ collection
        all_docs = collection.get(include=["documents", "metadatas"])
        docs = all_docs.get("documents", [])
        metas = all_docs.get("metadatas", [])

        if not docs:
            return None, []

        _bm25_corpus = [
            {
                "text": doc,
                "source": meta.get("source", "unknown"),
                "metadata": meta,
            }
            for doc, meta in zip(docs, metas)
        ]

        tokenized = [_tokenize(doc) for doc in docs]
        _bm25_index = BM25Okapi(tokenized)
        return _bm25_index, _bm25_corpus

    except Exception as e:
        print(f"⚠️  BM25 index build failed: {e}")
        return None, []


def retrieve_bm25(query: str, top_k: int = DEFAULT_TOP_K) -> list:
    """
    Sparse BM25 retrieval: keyword matching trên toàn bộ corpus.

    Bổ sung dense retrieval bằng cách bắt chính xác từ khóa chuyên biệt
    (PagerDuty, Flash Sale, Level 3, ...) mà cosine similarity có thể bỏ sót.

    Returns:
        list of {"text", "source", "score", "metadata"}
    """
    bm25, corpus = _get_bm25_index()
    if bm25 is None or not corpus:
        return []

    query_tokens = _tokenize(query)
    scores = bm25.get_scores(query_tokens)

    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    # Normalize BM25 scores về [0, 1] để dễ đọc
    max_score = scores[top_indices[0]] if top_indices else 1.0
    max_score = max(max_score, 1e-6)

    return [
        {
            "text": corpus[i]["text"],
            "source": corpus[i]["source"],
            "score": round(float(scores[i]) / max_score, 4),
            "metadata": corpus[i]["metadata"],
        }
        for i in top_indices
        if scores[i] > 0  # bỏ docs không có token match nào
    ]


def _rrf_merge(dense_results: list, bm25_results: list,
               top_k: int, k: int = 60) -> list:
    """
    Reciprocal Rank Fusion: kết hợp dense và BM25 rankings.

    Score = 1/(k + rank_dense) + 1/(k + rank_bm25)
    k=60 là giá trị chuẩn theo paper gốc (Cormack et al., SIGIR 2009).

    Chunks chỉ xuất hiện trong 1 phương pháp vẫn được tính — RRF tự nhiên
    ưu tiên chunks được cả 2 phương pháp rank cao.
    """
    rrf_scores: dict = {}  # key → {"chunk": ..., "rrf": float, "dense_score": float}

    for rank, chunk in enumerate(dense_results):
        key = chunk["text"][:80]
        if key not in rrf_scores:
            rrf_scores[key] = {
                "chunk": chunk,
                "rrf": 0.0,
                "dense_score": chunk.get("score", 0.0),  # preserve cosine score
            }
        rrf_scores[key]["rrf"] += 1.0 / (k + rank + 1)

    for rank, chunk in enumerate(bm25_results):
        key = chunk["text"][:80]
        if key not in rrf_scores:
            rrf_scores[key] = {"chunk": chunk, "rrf": 0.0, "dense_score": 0.0}
        rrf_scores[key]["rrf"] += 1.0 / (k + rank + 1)

    ranked = sorted(rrf_scores.values(), key=lambda x: x["rrf"], reverse=True)

    # score = RRF (dùng để rank), dense_score = cosine similarity (dùng cho confidence)
    result = []
    for item in ranked[:top_k]:
        chunk = dict(item["chunk"])
        chunk["score"] = round(item["rrf"], 6)
        chunk["dense_score"] = round(item["dense_score"], 4)
        chunk["retrieval_method"] = "hybrid_rrf"
        result.append(chunk)

    return result


def retrieve_hybrid(query: str, top_k: int = DEFAULT_TOP_K) -> list:
    """
    Hybrid BM25 + Dense Retrieval với Reciprocal Rank Fusion.

    Pipeline:
        1. Dense retrieval (top_k*2 candidates) — bắt ngữ nghĩa
        2. BM25 retrieval (top_k*2 candidates) — bắt từ khóa chính xác
        3. RRF merge → top_k kết quả cuối

    Lợi ích so với dense-only:
        - Từ khóa chuyên biệt (PagerDuty, Level 3, Flash Sale) không bị bỏ sót
          khi embedding vector tổng hợp nhiều khái niệm cùng lúc
        - Multi-topic queries hưởng lợi: BM25 bắt exact terms từ cả hai topic

    Returns:
        list of {"text", "source", "score", "metadata", "retrieval_method"}
    """
    candidate_k = top_k * 2

    dense_results = retrieve_dense(query, top_k=candidate_k)
    bm25_results = retrieve_bm25(query, top_k=candidate_k)

    # Nếu BM25 fail (index chưa ready), fallback về dense
    if not bm25_results:
        return dense_results[:top_k]

    return _rrf_merge(dense_results, bm25_results, top_k=top_k)


def run(state: dict) -> dict:
    """
    Worker entry point — gọi từ graph.py.

    Args:
        state: AgentState dict

    Returns:
        Updated AgentState với retrieved_chunks và retrieved_sources
    """
    task = state.get("task", "")
    top_k = state.get("retrieval_top_k", DEFAULT_TOP_K)

    state.setdefault("workers_called", [])
    state.setdefault("history", [])

    state["workers_called"].append(WORKER_NAME)

    # Log worker IO (theo contract)
    worker_io = {
        "worker": WORKER_NAME,
        "input": {"task": task, "top_k": top_k},
        "output": None,
        "error": None,
    }

    try:
        chunks = retrieve_hybrid(task, top_k=top_k)

        sources = list({c["source"] for c in chunks})

        state["retrieved_chunks"] = chunks
        state["retrieved_sources"] = sources

        worker_io["output"] = {
            "chunks_count": len(chunks),
            "sources": sources,
        }
        state["history"].append(
            f"[{WORKER_NAME}] retrieved {len(chunks)} chunks from {sources}"
        )

    except Exception as e:
        worker_io["error"] = {"code": "RETRIEVAL_FAILED", "reason": str(e)}
        state["retrieved_chunks"] = []
        state["retrieved_sources"] = []
        state["history"].append(f"[{WORKER_NAME}] ERROR: {e}")

    # Ghi worker IO vào state để trace
    state.setdefault("worker_io_logs", []).append(worker_io)

    return state


# ─────────────────────────────────────────────
# Test độc lập
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Retrieval Worker — Standalone Test")
    print("=" * 50)

    test_queries = [
        "SLA ticket P1 là bao lâu?",
        "Điều kiện được hoàn tiền là gì?",
        "Ai phê duyệt cấp quyền Level 3?",
        # Multi-topic: gq09 type
        "SLA P1 notification kênh thông báo và điều kiện cấp Level 2 emergency access",
    ]

    for query in test_queries:
        print(f"\n▶ Query: {query}")
        result = run({"task": query})
        chunks = result.get("retrieved_chunks", [])
        print(f"  Retrieved: {len(chunks)} chunks (method: hybrid_rrf)")
        for c in chunks:
            method = c.get("retrieval_method", "dense")
            has_pg = "[PagerDuty]" if "pagerduty" in c["text"].lower() else ""
            print(f"    [{c['score']:.4f}] {c['source'][-25:]}: {c['text'][:60]}... {has_pg}")
        print(f"  Sources: {result.get('retrieved_sources', [])}")

    print("\n✅ retrieval_worker test done.")
