"""PDF text extraction with fallback chain: pdfplumber → PyMuPDF → image OCR."""
import io
import base64
from pathlib import Path
from typing import Optional

import pdfplumber
import fitz  # PyMuPDF


def parse_pdf(filepath: str, force_vision: bool = False) -> dict:
    """Extract text from a PDF. Returns {pages: [{page: int, text: str, is_scan: bool}], metadata: {...}}"""
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"PDF not found: {filepath}")

    result = {"filename": filepath.name, "pages": [], "metadata": {}, "total_pages": 0}
    
    # Try pdfplumber first (best for text PDFs)
    try:
        with pdfplumber.open(filepath) as pdf:
            result["total_pages"] = len(pdf.pages)
            result["metadata"] = pdf.metadata or {}
            
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                tables = page.extract_tables() or []
                
                # Also extract table data as structured text
                table_text = ""
                for table in tables:
                    for row in table:
                        if row:
                            cleaned = [str(c).strip() if c else "" for c in row]
                            table_text += " | ".join(cleaned) + "\n"
                
                combined = (text + "\n" + table_text).strip()
                is_scan = len(combined) < 50  # Likely a scanned page
                
                result["pages"].append({
                    "page": i + 1,
                    "text": combined,
                    "is_scan": is_scan,
                    "tables": tables,
                })
    except Exception as e:
        # Fallback to PyMuPDF
        result["pages"] = _extract_with_pymupdf(filepath)
        result["total_pages"] = len(result["pages"])
    
    # For scanned pages, render as images for Vision API
    scan_pages = [p for p in result["pages"] if p.get("is_scan")]
    if scan_pages or force_vision:
        _add_page_images(filepath, result, force_all=force_vision)
    
    return result


def _extract_with_pymupdf(filepath: Path) -> list:
    """Fallback extraction using PyMuPDF."""
    pages = []
    doc = fitz.open(str(filepath))
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        is_scan = len(text.strip()) < 50
        pages.append({"page": i + 1, "text": text, "is_scan": is_scan, "tables": []})
    doc.close()
    return pages


def _add_page_images(filepath: Path, result: dict, force_all: bool = False):
    """Render scanned pages as base64 images for Claude Vision."""
    doc = fitz.open(str(filepath))
    for page_data in result["pages"]:
        if force_all or page_data.get("is_scan"):
            idx = page_data["page"] - 1
            page = doc[idx]
            mat = fitz.Matrix(2, 2)  # 2x zoom for better quality
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            page_data["image_b64"] = base64.b64encode(img_bytes).decode()
            page_data["image_mime"] = "image/png"
    doc.close()


def get_full_text(parsed: dict) -> str:
    """Get all text from a parsed PDF as one string."""
    return "\n\n".join(
        f"--- Seite {p['page']} ---\n{p['text']}" 
        for p in parsed["pages"] if p.get("text")
    )


def get_scan_images(parsed: dict) -> list:
    """Get base64 images for scanned pages (for Claude Vision)."""
    return [
        {"page": p["page"], "b64": p["image_b64"], "mime": p["image_mime"]}
        for p in parsed["pages"] if p.get("image_b64")
    ]
