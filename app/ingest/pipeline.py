"""Document -> chunks, with provenance kept on every chunk.

Keeping {doc_id, page, bbox} on each chunk is what lets the workbench point at
the exact region of a scanned report that a claim came from. Cheap to build,
and it is the difference between "the AI says" and "page 3 says".
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.graphrag.extract import Chunk

MIN_CHARS = 200
MAX_CHARS = 1400


def _cid(doc_id: str, page: int, idx: int, text: str) -> str:
    h = hashlib.sha1(f"{doc_id}:{page}:{idx}:{text[:200]}".encode()).hexdigest()[:12]
    return f"{doc_id}-p{page}-{idx}-{h}"


def split_semantic(text: str) -> list[str]:
    """Split on structure (headings, numbered clauses, blank lines) before
    falling back to size. Fixed-size chunking severs SOP steps from their
    headings, which is exactly the context the graph extractor needs."""
    parts = re.split(r"\n(?=\s*(?:\d+\.\d*\s|[A-Z][A-Z \-]{6,}$|#{1,6}\s))|\n\s*\n", text)
    chunks, buf = [], ""
    for p in (p.strip() for p in parts if p and p.strip()):
        if len(buf) + len(p) < MAX_CHARS:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            while len(p) > MAX_CHARS:
                cut = p.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
                chunks.append(p[:cut])
                p = p[cut:].strip()
            buf = p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= 40]


def load_text_file(path: Path) -> list[tuple[int, str]]:
    return [(1, path.read_text(errors="replace"))]


def load_pdf(path: Path, ocr_if_empty: bool = True) -> list[tuple[int, str]]:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    pages = []
    for i in range(len(pdf)):
        page = pdf[i]
        text = page.get_textpage().get_text_range().strip()
        if not text and ocr_if_empty:
            text = _ocr_page(page)
        pages.append((i + 1, text))
    return pages


def _ocr_page(page, scale: float = 2.0) -> str:
    """Scanned page: rasterise and run ONNX OCR on CPU. The 32-core host makes
    this comfortably fast and leaves the whole GPU for the LLM."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        global _OCR
        try:
            _OCR
        except NameError:
            _OCR = RapidOCR()
        img = page.render(scale=scale).to_pil()
        res, _ = _OCR(np.array(img))
        return "\n".join(r[1] for r in (res or []))
    except Exception as e:
        return f"[OCR unavailable: {e}]"


def ingest_file(path: Path) -> list[Chunk]:
    doc_id = path.stem
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = load_pdf(path)
    elif suffix in {".txt", ".md"}:
        pages = load_text_file(path)
    else:
        return []
    out: list[Chunk] = []
    for page_no, text in pages:
        for i, c in enumerate(split_semantic(text)):
            out.append(Chunk(_cid(doc_id, page_no, i, c), doc_id, page_no, c))
    return out


def ingest_dir(d: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".pdf", ".txt", ".md"}:
            chunks.extend(ingest_file(p))
    return chunks
