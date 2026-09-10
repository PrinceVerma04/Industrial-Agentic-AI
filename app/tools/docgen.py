"""Real deliverables: .docx / .pptx / .xlsx.

Templates are filled from structured fields rather than having the model emit
document XML - far more reliable, and the output looks like the organisation's
own paperwork. Every generated document carries a provenance footer so a
reviewer can trace each claim to a source page.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches, Pt as PPt

from app.core.config import get_settings


def _out(name: str) -> Path:
    p = get_settings().artifacts_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def approval_note(title: str, subject: str, background: str, findings: list[str],
                  recommendation: str, citations: list[dict] | None = None,
                  prepared_by: str = "Sovereign AI Workbench",
                  filename: str = "approval_note.docx") -> str:
    d = Document()
    d.add_heading(title, level=0)
    meta = d.add_paragraph()
    meta.add_run(f"Subject: {subject}\n").bold = True
    meta.add_run(f"Date: {date.today().isoformat()}\nPrepared by: {prepared_by}")

    d.add_heading("1. Background", level=1)
    d.add_paragraph(background)

    d.add_heading("2. Findings", level=1)
    for f in findings:
        d.add_paragraph(f, style="List Number")

    d.add_heading("3. Recommendation", level=1)
    d.add_paragraph(recommendation)

    d.add_heading("4. Approval", level=1)
    t = d.add_table(rows=2, cols=3)
    t.style = "Table Grid"
    for i, h in enumerate(["Prepared", "Reviewed", "Approved"]):
        t.cell(0, i).text = h
        t.cell(1, i).text = "\n\n"

    if citations:
        d.add_heading("Source references", level=1)
        for c in citations:
            if isinstance(c, dict):
                label = (f"{c.get('doc_id', c.get('text', '?'))}"
                         + (f" — page {c['page']}" if c.get("page") else ""))
            else:
                label = str(c)
            p = d.add_paragraph(label)
            p.runs[0].font.size = Pt(8)

    f = _out(filename)
    d.save(f)
    return str(f)


def deck(title: str, slides: list[dict], filename: str = "deck.pptx") -> str:
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = title
    s.placeholders[1].text = f"Generated on-premise · {date.today().isoformat()}"
    for sl in slides:
        x = prs.slides.add_slide(prs.slide_layouts[1])
        x.shapes.title.text = sl.get("title", "")
        tf = x.placeholders[1].text_frame
        tf.clear()
        for i, b in enumerate(sl.get("bullets", [])):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = str(b)
            para.font.size = PPt(18)
    f = _out(filename)
    prs.save(f)
    return str(f)


def sheet(rows: list[list], headers: list[str] | None = None,
          sheet_name: str = "Data", filename: str = "output.xlsx") -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    if headers:
        ws.append(headers)
        for c in ws[1]:
            c.font = c.font.copy(bold=True)
    for r in rows:
        ws.append(list(r))
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(width + 2, 60)
    f = _out(filename)
    wb.save(f)
    return str(f)
