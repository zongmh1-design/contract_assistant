from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf


class PdfRenderError(RuntimeError):
    """扫描 PDF 页面渲染失败。"""


@dataclass(frozen=True)
class RenderedPdfPage:
    page_number: int
    image_path: Path


class PdfPageRenderer(Protocol):
    def render_pages(
        self, pdf_path: Path, output_directory: Path
    ) -> list[RenderedPdfPage]: ...


class PyMuPdfPageRenderer:
    def __init__(self, dpi: int = 200) -> None:
        self.dpi = dpi

    def render_pages(
        self, pdf_path: Path, output_directory: Path
    ) -> list[RenderedPdfPage]:
        try:
            with pymupdf.open(pdf_path) as document:
                rendered_pages = []
                for page_index, page in enumerate(document, start=1):
                    image_path = output_directory / f"page-{page_index}.png"
                    pixmap = page.get_pixmap(dpi=self.dpi, alpha=False)
                    pixmap.save(image_path)
                    rendered_pages.append(RenderedPdfPage(page_index, image_path))
                return rendered_pages
        except Exception as error:
            raise PdfRenderError(f"PDF 页面渲染失败: {error}") from error
