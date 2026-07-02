import fitz
from pathlib import Path
from typing import List, Dict

def extract_images_from_pdf(pdf_path: Path, out_dir: Path) -> List[Dict]:
    """Extract every image object from *pdf_path* into *out_dir*.
    Returns a list of dicts: {"page": int, "image_path": str}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    manifest = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha < 4:  # RGB or Gray
                pix = fitz.Pixmap(fitz.csRGB, pix)
            img_name = f"page_{page_num+1}_img_{img_index+1}.png"
            img_path = out_dir / img_name
            pix.save(str(img_path))
            pix = None
            manifest.append({"page": page_num + 1, "image_path": str(img_path)})
    return manifest

def _rows_to_csv(rows: List[List[str]]) -> str:
    return "\n".join([",".join(str(cell) for cell in r) for r in rows])

def extract_tables_from_pdf(pdf_path: Path) -> List[Dict]:
    """Extract tables from *pdf_path*.
    Returns a list of dicts: {"page": int, "table_text": str} where the table
    is represented as CSV text.
    """
    doc = fitz.open(str(pdf_path))
    tables = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        if hasattr(page, "find_tables"):
            for tbl in page.find_tables():
                rows = tbl.extract()
                if rows:
                    tables.append({"page": page_num + 1, "table_text": _rows_to_csv(rows)})
    return tables
