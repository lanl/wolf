from typing import Literal, Optional 
from pydantic import BaseModel, Field
from pathlib import Path
from framework.workflows.base_agent_action import AgentAction
from framework.tooling.pdf_extraction_utils import extract_images_from_pdf, extract_tables_from_pdf
import json
import uuid

# ---------------------------
# PDF Document Actions
# ---------------------------

class ExtractImagesFromPDFArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to, e.g., 'local'")
    universe: str = Field(description="Name of the universe to operate in")
    pdf_path: str = Field(description="Path to the PDF file to extract images from")
    out_dir: Optional[str] = Field(default=None, description="Directory to store extracted images; defaults to a temporary directory")
    doc_source: str = Field(default="agent", description="Source label for generated documents")

class ExtractImagesFromPDFAction(AgentAction):
    action: Literal["extract_images_from_pdf"] = "extract_images_from_pdf"
    description: Literal["Extract images from a PDF using PyMuPDF"] = "Extract images from a PDF using PyMuPDF"
    payload: ExtractImagesFromPDFArgs
    payload_schema: str = """
    {"system": <string>, "universe": <string>, "pdf_path": <string>, "out_dir": <string|None>, "doc_source": <string>}
    """
    def execute(self, infra) -> None:
        # Resolve paths
        pdf_path = Path(self.payload.pdf_path).expanduser().resolve()
        out_dir = Path(self.payload.out_dir).expanduser().resolve() if self.payload.out_dir else Path("./extracted_images").resolve()
        manifest = extract_images_from_pdf(pdf_path, out_dir)
        # Store manifest as JSON file for downstream steps
        manifest_path = out_dir / "image_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        ctx_msg = f"[Universe: {self.payload.universe}] Extracted {len(manifest)} images from {pdf_path} into {out_dir}. Manifest saved at {manifest_path}"
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)

class ExtractTablesFromPDFArgs(BaseModel):
    system: str = Field(description="The system the universes are connected to, e.g., 'local'")
    universe: str = Field(description="Name of the universe to operate in")
    pdf_path: str = Field(description="Path to the PDF file to extract tables from")
    doc_source: str = Field(default="agent", description="Source label for generated documents")

class ExtractTablesFromPDFAction(AgentAction):
    action: Literal["extract_tables_from_pdf"] = "extract_tables_from_pdf"
    description: Literal["Extract tables from a PDF using PyMuPDF"] = "Extract tables from a PDF using PyMuPDF"
    payload: ExtractTablesFromPDFArgs
    payload_schema: str = """
    {"system": <string>, "universe": <string>, "pdf_path": <string>, "doc_source": <string>}
    """
    def execute(self, infra) -> None:
        pdf_path = Path(self.payload.pdf_path).expanduser().resolve()
        tables = extract_tables_from_pdf(pdf_path)
        # Write each table to a temporary CSV file and create a manifest with clean metadata
        manifest = []
        for idx, tbl in enumerate(tables):
            tmp_csv = Path(f"./extracted_tables/table_{idx}.csv").resolve()
            tmp_csv.parent.mkdir(parents=True, exist_ok=True)
            tmp_csv.write_text(tbl["table_text"], encoding="utf-8")
            # Metadata dict with only primitive types
            metadata = {"page": tbl["page"], "table_index": idx}
            manifest.append({
                "page": tbl["page"],
                "csv_path": str(tmp_csv),
                "metadata": metadata
            })
        manifest_path = Path("./extracted_tables/table_manifest.json").resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        ctx_msg = f"[Universe: {self.payload.universe}] Extracted {len(tables)} tables from {pdf_path}. Manifest saved at {manifest_path}"
        infra.append_chat_history(actor="system", content=ctx_msg, action={"action": "system_info"}, log_console=True)
