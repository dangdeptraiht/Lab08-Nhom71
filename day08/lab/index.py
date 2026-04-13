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
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =============================================================================
# CẤU HÌNH
# =============================================================================

DOCS_DIR = Path(__file__).parent / "data" / "docs"
CHROMA_DB_DIR = Path(__file__).parent / "chroma_db"

# TODO Sprint 1: Điều chỉnh chunk size và overlap theo quyết định của nhóm
# Gợi ý từ slide: chunk 300-500 tokens, overlap 50-80 tokens
CHUNK_SIZE = 400  # tokens (ước lượng bằng số ký tự / 4)
CHUNK_OVERLAP = 80  # tokens overlap giữa các chunk


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

    TODO Sprint 1:
    - Extract metadata từ dòng đầu file (Source, Department, Effective Date, Access)
    - Bỏ các dòng header metadata khỏi nội dung chính
    - Normalize khoảng trắng, xóa ký tự rác

    Gợi ý: dùng regex để parse dòng "Key: Value" ở đầu file.
    """
    lines = raw_text.strip().split("\n")
    metadata = {
        "source": filepath,
        "section": "",
        "department": "unknown",
        "effective_date": "unknown",
        "access": "internal",
    }
    content_lines = []
    header_done = False

    for line in lines:
        cleaned_line = line.strip()
        if not header_done:
            # Check if it's a known metadata key
            if cleaned_line.startswith("Source:"):
                metadata["source"] = cleaned_line.replace("Source:", "").strip()
                continue
            elif cleaned_line.startswith("Department:"):
                metadata["department"] = cleaned_line.replace("Department:", "").strip()
                continue
            elif cleaned_line.startswith("Effective Date:"):
                metadata["effective_date"] = cleaned_line.replace(
                    "Effective Date:", ""
                ).strip()
                continue
            elif cleaned_line.startswith("Access:"):
                metadata["access"] = cleaned_line.replace("Access:", "").strip()
                continue

            # Nếu gặp section hoặc một dòng text không phải trống/tên tài liệu -> Bắt đầu coi là content
            if cleaned_line.startswith("===") or (
                cleaned_line != "" and not cleaned_line.isupper()
            ):
                header_done = True
                content_lines.append(line)
        else:
            content_lines.append(line)

    cleaned_text = "\n".join(content_lines).strip()

    # Normalize text: chuẩn hóa khoảng trắng thừa
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

    return {
        "text": cleaned_text,
        "metadata": metadata,
    }


# =============================================================================
# STEP 2: CHUNK
# Chia tài liệu thành các đoạn nhỏ theo cấu trúc tự nhiên
# =============================================================================


def chunk_document(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Chunk một tài liệu đã preprocess thành danh sách các chunk nhỏ.

    Args:
        doc: Dict với "text" và "metadata" (output của preprocess_document)

    Returns:
        List các Dict, mỗi dict là một chunk với:
          - "text": nội dung chunk
          - "metadata": metadata gốc + "section" của chunk đó

    TODO Sprint 1:
    1. Split theo heading "=== Section ... ===" hoặc "=== Phần ... ===" trước
    2. Nếu section quá dài (> CHUNK_SIZE * 4 ký tự), split tiếp theo paragraph
    3. Thêm overlap: lấy đoạn cuối của chunk trước vào đầu chunk tiếp theo
    4. Mỗi chunk PHẢI giữ metadata đầy đủ từ tài liệu gốc

    Gợi ý: Ưu tiên cắt tại ranh giới tự nhiên (section, paragraph)
    thay vì cắt theo token count cứng.
    """
    text = doc["text"]
    base_metadata = doc["metadata"].copy()
    chunks = []

    # TODO: Implement chunking theo section heading
    # Bước 1: Split theo heading pattern "=== ... ==="
    sections = re.split(r"(===.*?===)", text)

    current_section = "General"
    current_section_text = ""

    for part in sections:
        if re.match(r"===.*?===", part):
            # Lưu section trước (nếu có nội dung)
            if current_section_text.strip():
                section_chunks = _split_by_size(
                    current_section_text.strip(),
                    base_metadata=base_metadata,
                    section=current_section,
                )
                chunks.extend(section_chunks)
            # Bắt đầu section mới
            current_section = part.strip("= ").strip()
            current_section_text = ""
        else:
            current_section_text += part

    # Lưu section cuối cùng
    if current_section_text.strip():
        section_chunks = _split_by_size(
            current_section_text.strip(),
            base_metadata=base_metadata,
            section=current_section,
        )
        chunks.extend(section_chunks)

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

    # Khởi tạo ChromaDB
    client_db = chromadb.PersistentClient(path=str(db_dir))
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
    print(f"\nTìm thấy {len(doc_files)} tài liệu.")

    # Bước 2: Build index
    print("\n--- Bắt đầu quá trình Indexing ---")
    build_index()

    # Bước 3: Kiểm tra kết quả
    print("\n" + "=" * 60)
    # print("KIỂM TRA CHẤT LƯỢNG INDEX")
    # print("="*60)
    list_chunks()  # Xem thử 3 chunk đầu
    inspect_metadata_coverage()