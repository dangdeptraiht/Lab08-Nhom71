"""
index.py — Sprint 1: Build RAG Index
====================================
Mục tiêu Sprint 1 (60 phút):
  - Đọc và preprocess tài liệu từ data/docs/
  - Chunk tài liệu theo cấu trúc tự nhiên (heading/section)
  - Gắn metadata: source, section, department, effective_date, access
  - Embed và lưu vào vector store (ChromaDB)

Definition of Done Sprint 1:
  ✓ Script chạy được và index đủ docs
  ✓ Có ít nhất 3 metadata fields hữu ích cho retrieval
  ✓ Có thể kiểm tra chunk bằng list_chunks()
"""

import os
import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Đảm bảo in được tiếng Việt trên Console Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =============================================================================
# CẤU HÌNH
# =============================================================================

DOCS_DIR = Path(__file__).parent / "data" / "docs"
CHROMA_DB_DIR = Path(__file__).parent / "chroma_db"

CHUNK_SIZE = 320  # tokens (uoc luong bang so ky tu / 4)
CHUNK_OVERLAP = 100  # tokens overlap giua cac chunk



# =============================================================================
# STEP 1: PREPROCESS
# Làm sạch text trước khi chunk và embed
# =============================================================================


def preprocess_document(raw_text: str, filepath: str) -> Dict[str, Any]:
    """
    Preprocess một tài liệu: extract metadata từ header và làm sạch nội dung.

    Args:
        raw_text: Toàn bộ nội dung file text
        filepath: Đường dẫn file để làm source mặc định

    Returns:
        Dict chứa:
          - "text": nội dung đã clean
          - "metadata": dict với source, department, effective_date, access
    """
    lines = raw_text.strip().split("\n")
    metadata = {
        "source": filepath,
        "section": "",
        "title": "",
        "department": "unknown",
        "effective_date": "unknown",
        "access": "internal",
    }
    content_start = 0

    # 1) Tách title (nếu có) ra khỏi header metadata.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not re.match(r"^[A-Za-z ]+:", stripped):
            metadata["title"] = stripped
            content_start = i + 1
        else:
            content_start = i
        break

    # 2) Parse metadata theo key-value trước khi đi vào main content.
    i = content_start
    while i < len(lines):
        cleaned_line = lines[i].strip()

        if cleaned_line.startswith("Source:"):
            raw_src = cleaned_line.replace("Source:", "").strip()
            metadata["source"] = raw_src.split("/")[-1].split("\\")[-1]
            i += 1
            continue
        if cleaned_line.startswith("Department:"):
            metadata["department"] = cleaned_line.replace("Department:", "").strip()
            i += 1
            continue
        if cleaned_line.startswith("Effective Date:"):
            metadata["effective_date"] = cleaned_line.replace("Effective Date:", "").strip()
            i += 1
            continue
        if cleaned_line.startswith("Access:"):
            metadata["access"] = cleaned_line.replace("Access:", "").strip()
            i += 1
            continue

        # Bỏ qua dòng trống phân tách giữa header và body.
        if cleaned_line == "":
            i += 1
            continue

        break

    # 3) Toàn bộ phần còn lại là main content.
    main_content = "\n".join(lines[i:]).strip()

    # Normalize text: chuẩn hóa khoảng trắng thừa
    main_content = re.sub(r"\n{3,}", "\n\n", main_content)

    return {
        "text": main_content,
        "main_content": main_content,
        "metadata": metadata,
    }


# =============================================================================
# STEP 2: CHUNK
# Chia tài liệu thành các đoạn nhỏ theo cấu trúc tự nhiên
# =============================================================================


def recursive_split(
    text: str, separators: List[str], chunk_size: int, overlap_size: int
) -> List[str]:
    """
    Tùy chỉnh logic Recursive Character Text Splitting tương tự LangChain.
    Sẽ thử split theo separators (từ thô đến mịn) để tìm điểm ngắt đẹp nhất.
    """
    final_chunks = []

    # Base case: text đã đủ nhỏ
    if len(text) <= chunk_size:
        return [text]

    # Tìm separator phù hợp nhất
    separator = separators[-1]  # Mặc định là char cuối (thường là rỗng hoặc space)
    for s in separators:
        if s in text:
            separator = s
            break

    # Split và đệ quy
    parts = text.split(separator)
    current_chunk = ""

    for part in parts:
        # Nếu gộp part vào current mà vẫn < chunk_size
        if len(current_chunk) + len(separator) + len(part) <= chunk_size:
            if current_chunk:
                current_chunk += separator + part
            else:
                current_chunk = part
        else:
            # Lưu chunk hiện tại
            if current_chunk:
                final_chunks.append(current_chunk)

            # Xử lý overlap: lấy một phần cuối của current_chunk
            overlap = (
                current_chunk[-overlap_size:]
                if len(current_chunk) > overlap_size
                else ""
            )

            # Kiểm tra xem part đơn lẻ có quá lớn không? Nếu có thì đệ quy sâu hơn.
            if len(part) > chunk_size:
                sub_chunks = recursive_split(
                    part,
                    separators[separators.index(separator) + 1 :],
                    chunk_size,
                    overlap_size,
                )
                final_chunks.extend(sub_chunks)
                current_chunk = ""  # Sau khi explode part to, reset chunk
            else:
                current_chunk = overlap + separator + part if overlap else part

    if current_chunk:
        final_chunks.append(current_chunk)

    return final_chunks


def chunk_document(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Section-based chunking: moi section (=== ... ===) thanh 1 chunk.

    Ly do chon chien luoc nay:
    - Corpus nho (5 docs, ~31 sections, moi section 100-600 chars).
    - Sections la ranh gioi ngu nghia tu nhien (dieu khoan, quy trinh, FAQ topic).
    - Giu nguyen cau truc dong goc (bullet points, Q&A) de LLM doc tot hon.
    - Khong can semantic_split (tiet kiem API, tranh pha huy format).
    - Prefix "Tai lieu / Muc" giup retrieval biet context cua chunk.
    """
    text = doc.get("main_content", doc["text"])
    base_metadata = doc["metadata"].copy()
    doc_title = (
        base_metadata.get("title")
        or base_metadata.get("source", "")
        .split("/")[-1]
        .replace(".md", "")
        .replace(".pdf", "")
        .replace(".txt", "")
        .upper()
    )

    def split_sections(main_text: str) -> List[Dict[str, str]]:
        section_pattern = re.compile(r"^===\s*(.+?)\s*===\s*$")
        lines = main_text.split("\n")
        sections: List[Dict[str, str]] = []
        current_title = "general"
        current_lines: List[str] = []

        for line in lines:
            match = section_pattern.match(line.strip())
            if match:
                if current_lines:
                    sections.append(
                        {
                            "section": current_title,
                            "content": "\n".join(current_lines).strip(),
                        }
                    )
                current_title = match.group(1).strip()
                current_lines = []
                continue
            current_lines.append(line)

        if current_lines:
            sections.append(
                {"section": current_title, "content": "\n".join(current_lines).strip()}
            )

        return [s for s in sections if s["content"]]

    def split_list_aware(section_text: str) -> List[str]:
        """
        Split long policy sections by list-item boundaries first to keep rule chains intact.
        """
        lines = section_text.split("\n")
        blocks: List[str] = []
        current: List[str] = []
        bullet_re = re.compile(r"^\s*(?:-|\*|\d+[\.)])\s+")

        for line in lines:
            if bullet_re.match(line) and current:
                blocks.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)

        if current:
            blocks.append("\n".join(current).strip())

        # Merge tiny blocks to avoid over-fragmentation.
        merged: List[str] = []
        for block in blocks:
            if not block:
                continue
            if merged and len(merged[-1]) < 180:
                merged[-1] = f"{merged[-1]}\n{block}".strip()
            else:
                merged.append(block)
        return merged if merged else [section_text]

    separators = ["\n\n", "\n- ", "\n* ", "\n", ". ", "; ", " ", ""]
    chunk_chars = CHUNK_SIZE * 4
    overlap_chars = CHUNK_OVERLAP * 4

    chunks = []
    chunk_idx = 0
    for sec in split_sections(text):
        section_name = sec["section"]
        section_text = sec["content"]

        section_blocks = split_list_aware(section_text)
        parts: List[str] = []
        for block in section_blocks:
            if len(block) <= chunk_chars:
                parts.append(block)
            else:
                parts.extend(
                    recursive_split(
                        block, separators, chunk_chars, overlap_chars
                    )
                )

        for content in parts:
            full_text = (
                f"Tai lieu: {doc_title}\n"
                f"Muc: {section_name}\n"
                f"---\n"
                f"{content.strip()}"
            )
            chunks.append(
                {
                    "text": full_text,
                    "metadata": {
                        **base_metadata,
                        "section": section_name,
                        "index": chunk_idx,
                    },
                }
            )
            chunk_idx += 1

    return chunks


def _split_by_size(
    text: str,
    base_metadata: Dict,
    section: str,
    chunk_chars: int = CHUNK_SIZE * 4,
    overlap_chars: int = CHUNK_OVERLAP * 4,
) -> List[Dict[str, Any]]:
    """
    Helper: Split text dài thành chunks với overlap.

    TODO Sprint 1:
    Hiện tại dùng split đơn giản theo ký tự.
    Cải thiện: split theo paragraph (\n\n) trước, rồi mới ghép đến khi đủ size.
    """
    if len(text) <= chunk_chars:
        return [
            {
                "text": text,
                "metadata": {**base_metadata, "section": section},
            }
        ]

    # Chia theo paragraph trước
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk_text = ""

    for para in paragraphs:
        # Nếu cộng paragraph hiện tại vào mà vẫn nhỏ hơn chunk_chars
        if len(current_chunk_text) + len(para) + 2 <= chunk_chars:
            current_chunk_text += para + "\n\n"
        else:
            # Lưu chunk hiện tại
            if current_chunk_text:
                chunks.append(
                    {
                        "text": current_chunk_text.strip(),
                        "metadata": {**base_metadata, "section": section},
                    }
                )

            # Khởi tạo chunk mới với một phần overlap từ chunk cũ (nếu có thể)
            # Ở đây ta lấy paragraph cuối làm overlap nếu nó không quá dài
            overlap_text = (
                current_chunk_text[-overlap_chars:]
                if len(current_chunk_text) > overlap_chars
                else ""
            )
            current_chunk_text = overlap_text + para + "\n\n"

    # Lưu chunk cuối
    if current_chunk_text:
        chunks.append(
            {
                "text": current_chunk_text.strip(),
                "metadata": {**base_metadata, "section": section},
            }
        )

    return chunks


# =============================================================================
# STEP 3: EMBED + STORE
# Embed các chunk và lưu vào ChromaDB
# =============================================================================


def get_embedding(text: str) -> List[float]:
    """
    Tạo embedding vector bằng OpenAI.
    """
    response = client.embeddings.create(input=text, model="text-embedding-3-small")
    return response.data[0].embedding


def build_index(docs_dir: Path = DOCS_DIR, db_dir: Path = CHROMA_DB_DIR) -> None:
    """
    Pipeline hoàn chỉnh: đọc docs → preprocess → chunk → embed → store.
    """
    import chromadb

    print(f"Đang build index từ: {docs_dir}")
    db_dir.mkdir(parents=True, exist_ok=True)

    client_db = chromadb.PersistentClient(path=str(db_dir))
    try:
        client_db.delete_collection("rag_lab")
    except Exception:
        pass
    collection = client_db.get_or_create_collection(
        name="rag_lab", metadata={"hnsw:space": "cosine"}
    )

    total_chunks = 0
    doc_files = list(docs_dir.glob("*.txt"))
    all_chunks_debug = []

    if not doc_files:
        print(f"Không tìm thấy file .txt trong {docs_dir}")
        return

    for filepath in doc_files:
        print(f"  Processing: {filepath.name}")
        raw_text = filepath.read_text(encoding="utf-8")

        # Preprocess
        doc = preprocess_document(raw_text, str(filepath))

        # Chunk
        chunks = chunk_document(doc)
        all_chunks_debug.extend(chunks)

        # Embed và lưu từng chunk vào ChromaDB
        for i, chunk in enumerate(chunks):
            chunk_id = f"{filepath.stem}_{i}"
            embedding = get_embedding(chunk["text"])
            collection.upsert(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[chunk["text"]],
                metadatas=[chunk["metadata"]],
            )
        total_chunks += len(chunks)

    # Xuất file JSON để debug
    debug_file = Path(__file__).parent / "debug_chunks.json"
    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump(all_chunks_debug, f, ensure_ascii=False, indent=2)

    print(f"\nHoàn thành! Tổng số chunks: {total_chunks}")
    print(f"Bản debug đã được lưu tại: {debug_file.name}")


# =============================================================================
# STEP 4: INSPECT / KIỂM TRA
# Dùng để debug và kiểm tra chất lượng index
# =============================================================================


def list_chunks(db_dir: Path = CHROMA_DB_DIR, n: int = 0) -> None:
    """
    In ra n chunk đầu tiên trong ChromaDB để kiểm tra chất lượng index.

    TODO Sprint 1:
    Implement sau khi hoàn thành build_index().
    Kiểm tra:
    - Chunk có giữ đủ metadata không? (source, section, effective_date)
    - Chunk có bị cắt giữa điều khoản không?
    - Metadata effective_date có đúng không?
    """
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(db_dir))
        collection = client.get_collection("rag_lab")
        results = collection.get(limit=n, include=["documents", "metadatas"])

        # print(f"\n{'='*20} Top {n} chunks trong index {'='*20}")
        if not results["documents"]:
            print("Không tìm thấy dữ liệu trong 'documents'.")
            return

        for i in range(len(results["ids"])):
            doc = results["documents"][i]
            meta = results["metadatas"][i]
            print(f"\n[Chunk {i+1}]")
            print(f"  ID: {results['ids'][i]}")
            print(f"  Source: {meta.get('source', 'N/A')}")
            print(f"  Section: {meta.get('section', 'N/A')}")
            print(f"  Date: {meta.get('effective_date', 'N/A')}")
            print(f"  Text: {doc[:150]}...")
            print("-" * 50)
    except Exception as e:
        print(f"Lỗi khi đọc index: {e}")
        print("Hãy chạy build_index() trước.")


def inspect_metadata_coverage(db_dir: Path = CHROMA_DB_DIR) -> None:
    """
    Kiểm tra phân phối metadata trong toàn bộ index.

    Checklist Sprint 1:
    - Mọi chunk đều có source?
    - Có bao nhiêu chunk từ mỗi department?
    - Chunk nào thiếu effective_date?

    TODO: Implement sau khi build_index() hoàn thành.
    """
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(db_dir))
        collection = client.get_collection("rag_lab")
        results = collection.get(include=["metadatas"])

        print(f"\nTổng chunks: {len(results['metadatas'])}")

        # TODO: Phân tích metadata
        # Đếm theo department, kiểm tra effective_date missing, v.v.
        departments = {}
        missing_date = 0
        for meta in results["metadatas"]:
            dept = meta.get("department", "unknown")
            departments[dept] = departments.get(dept, 0) + 1
            if meta.get("effective_date") in ("unknown", "", None):
                missing_date += 1

        print("Phân bố theo department:")
        for dept, count in departments.items():
            print(f"  {dept}: {count} chunks")
        print(f"Chunks thiếu effective_date: {missing_date}")

    except Exception as e:
        print(f"Lỗi: {e}. Hãy chạy build_index() trước.")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    # Bước 1: Tìm tài liệu
    doc_files = list(DOCS_DIR.glob("*.txt"))
    print(f"\nFound {len(doc_files)} docs.")

    # Bước 2: Build index
    print("\n--- Starting Indexing ---")
    build_index()

    # Bước 3: Kiểm tra kết quả
    print("\n" + "=" * 60)
    list_chunks()
    inspect_metadata_coverage()
