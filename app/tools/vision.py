"""Multimodal document understanding - rubric point 4.

Two complementary paths, both fully local:
  * OCR (RapidOCR, ONNX, CPU)  - fast, exact for printed text
  * VLM (qwen2.5vl via Ollama) - handwriting, annotations, engineering drawings,
                                 and anything needing layout comprehension

OCR runs on CPU so the GPU stays free for the language model.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from app.core.config import get_settings

_OCR = None
SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}


def _resolve(path: str) -> Path:
    """Accept a path relative to the workspace or the corpus."""
    s = get_settings()
    for base in (s.workspace_dir, s.corpus_dir, Path.cwd()):
        p = (base / path).resolve()
        if p.is_file():
            return p
    p = Path(path).resolve()
    if p.is_file():
        return p
    raise FileNotFoundError(f"no such document: {path}")


def _pages_as_png(p: Path, max_pages: int = 4, scale: float = 2.0) -> list[bytes]:
    if p.suffix.lower() != ".pdf":
        return [p.read_bytes()]
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(p))
    out = []
    for i in range(min(len(pdf), max_pages)):
        buf = io.BytesIO()
        pdf[i].render(scale=scale).to_pil().save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def ocr_document(path: str, max_pages: int = 4) -> dict:
    """Exact text extraction via ONNX OCR on CPU."""
    global _OCR
    p = _resolve(path)
    if p.suffix.lower() not in SUPPORTED:
        raise ValueError(f"unsupported type {p.suffix}; expected {sorted(SUPPORTED)}")
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()

    import numpy as np
    from PIL import Image

    pages = []
    for i, png in enumerate(_pages_as_png(p, max_pages)):
        img = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        res, _ = _OCR(img)
        lines = [{"text": r[1], "bbox": r[0], "conf": float(r[2])} for r in (res or [])]
        pages.append({"page": i + 1, "lines": lines,
                      "text": "\n".join(l["text"] for l in lines)})
    return {"file": str(p), "engine": "rapidocr-onnx-cpu",
            "pages": pages, "text": "\n\n".join(pg["text"] for pg in pages)}


def describe_document(path: str, question: str = "", manager=None, max_pages: int = 2) -> dict:
    """Vision-language understanding: handwriting, P&ID symbols, annotations,
    table layout. Routed through the 'vision' capability so the ModelManager
    swaps to the VLM and evicts the reasoning model first."""
    from app.router.manager import get_manager
    mgr = manager or get_manager()
    p = _resolve(path)
    prompt = question or (
        "Describe this engineering document. List every equipment tag, instrument "
        "tag and line number you can read, and describe how they are connected. "
        "Report only what is legible; do not guess.")

    images = [base64.b64encode(png).decode() for png in _pages_as_png(p, max_pages)]
    spec, decision = mgr.select("vision")
    text = mgr.client.chat(spec.tag, [{"role": "user", "content": prompt}],
                           images=images, num_ctx=spec.ctx)
    return {"file": str(p), "model": spec.tag, "pages_read": len(images),
            "question": prompt, "description": text,
            "routing": {"chosen": decision.chosen, "reason": decision.reason}}
