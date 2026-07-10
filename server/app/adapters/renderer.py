"""Slide rendering: PDF pages -> PNG via PyMuPDF; PPTX -> PDF via LibreOffice.

All methods are synchronous (CPU / subprocess bound). Callers run them in a
thread (asyncio.to_thread) so the event loop — which also carries live voice
sessions in the single-process dev setup — never blocks.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

_MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def _find_soffice(explicit: str = "") -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which("soffice")
    if found:
        return found
    if Path(_MAC_SOFFICE).exists():
        return _MAC_SOFFICE
    return None


class PyMuPDFRenderer:
    def __init__(self, soffice_path: str = "", zoom: float = 2.0):
        self._soffice = _find_soffice(soffice_path)
        self._zoom = zoom

    @property
    def supports_pptx(self) -> bool:
        return self._soffice is not None

    def convert_to_pdf(self, source: Path, out_dir: Path) -> Path:
        if not self._soffice:
            raise RuntimeError("LibreOffice not found — cannot convert PPTX. Export to PDF instead.")
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [self._soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(source)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        out = out_dir / (source.stem + ".pdf")
        if not out.exists():
            raise RuntimeError("LibreOffice reported success but produced no PDF")
        return out

    def page_count(self, pdf: Path) -> int:
        with fitz.open(pdf) as doc:
            return doc.page_count

    def render_page(self, pdf: Path, index: int, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf) as doc:
            page = doc[index]
            pix = page.get_pixmap(matrix=fitz.Matrix(self._zoom, self._zoom))
            pix.save(out_path)

    def extract_text(self, pdf: Path, index: int) -> str:
        with fitz.open(pdf) as doc:
            text = doc[index].get_text("text").strip()
        # Cap what we feed the narration model; slides are not essays.
        return text[:4000]
