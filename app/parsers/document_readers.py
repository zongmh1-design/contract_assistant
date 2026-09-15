from pathlib import Path
from typing import Protocol

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.schemas import (
    DocumentReadResult,
    DocumentReadStatus,
    DocumentTextBlock,
)


# 只统计字母、数字和汉字等字母数字字符，过滤空白和纯标点。
MIN_MEANINGFUL_TEXT_CHARACTERS = 10


def has_meaningful_text(text: str) -> bool:
    meaningful_count = sum(character.isalnum() for character in text)
    return meaningful_count >= MIN_MEANINGFUL_TEXT_CHARACTERS


def failed_result(
    document_id: int,
    file_type: str,
    error_code: str,
    error_message: str,
    *,
    page_count: int | None = None,
    requires_ocr: bool = False,
    status: DocumentReadStatus = DocumentReadStatus.FAILED,
) -> DocumentReadResult:
    return DocumentReadResult(
        document_id=document_id,
        file_type=file_type,
        text="",
        blocks=[],
        page_count=page_count,
        requires_ocr=requires_ocr,
        read_status=status,
        error_code=error_code,
        error_message=error_message,
    )


class DocumentReader(Protocol):
    def read(
        self, document_id: int, path: Path, file_type: str
    ) -> DocumentReadResult: ...


class PdfDocumentReader:
    def read(
        self, document_id: int, path: Path, file_type: str
    ) -> DocumentReadResult:
        try:
            pdf = PdfReader(str(path))
            blocks: list[DocumentTextBlock] = []
            page_texts: list[str] = []
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = (page.extract_text() or "").strip()
                if not page_text:
                    continue
                page_texts.append(page_text)
                blocks.append(
                    DocumentTextBlock(
                        text=page_text,
                        page_number=page_number,
                        block_index=len(blocks),
                    )
                )
        except Exception as error:
            return failed_result(
                document_id,
                file_type,
                "DOCUMENT_READ_FAILED",
                f"PDF 读取失败: {error}",
            )

        text = "\n\n".join(page_texts)
        if not has_meaningful_text(text):
            return failed_result(
                document_id,
                file_type,
                "OCR_REQUIRED",
                "PDF 可提取文本不足，需要 OCR",
                page_count=len(pdf.pages),
                requires_ocr=True,
                status=DocumentReadStatus.OCR_REQUIRED,
            )

        return DocumentReadResult(
            document_id=document_id,
            file_type=file_type,
            text=text,
            blocks=blocks,
            page_count=len(pdf.pages),
            requires_ocr=False,
            read_status=DocumentReadStatus.SUCCESS,
            error_code=None,
            error_message=None,
        )


class DocxDocumentReader:
    def read(
        self, document_id: int, path: Path, file_type: str
    ) -> DocumentReadResult:
        try:
            document = Document(str(path))
            blocks: list[DocumentTextBlock] = []
            for item in document.iter_inner_content():
                if isinstance(item, Paragraph):
                    block_text = item.text.strip()
                elif isinstance(item, Table):
                    rows = [
                        "\t".join(cell.text.strip() for cell in row.cells)
                        for row in item.rows
                    ]
                    block_text = "\n".join(row for row in rows if row.strip())
                else:
                    continue

                if block_text:
                    blocks.append(
                        DocumentTextBlock(
                            text=block_text,
                            page_number=None,
                            block_index=len(blocks),
                        )
                    )
        except Exception as error:
            return failed_result(
                document_id,
                file_type,
                "DOCUMENT_READ_FAILED",
                f"DOCX 读取失败: {error}",
            )

        text = "\n\n".join(block.text for block in blocks)
        if not has_meaningful_text(text):
            return failed_result(
                document_id,
                file_type,
                "DOCUMENT_CONTENT_EMPTY",
                "DOCX 没有有效正文",
                page_count=None,
            )

        return DocumentReadResult(
            document_id=document_id,
            file_type=file_type,
            text=text,
            blocks=blocks,
            page_count=None,
            requires_ocr=False,
            read_status=DocumentReadStatus.SUCCESS,
            error_code=None,
            error_message=None,
        )


class ImageDocumentReader:
    def read(
        self, document_id: int, path: Path, file_type: str
    ) -> DocumentReadResult:
        return failed_result(
            document_id,
            file_type,
            "OCR_REQUIRED",
            "图片合同需要 OCR，本阶段不执行 OCR",
            page_count=1,
            requires_ocr=True,
            status=DocumentReadStatus.OCR_REQUIRED,
        )


class DocumentReaderRouter:
    """简单静态路由，不使用动态插件或自动扫描。"""

    def __init__(self) -> None:
        pdf_reader = PdfDocumentReader()
        docx_reader = DocxDocumentReader()
        image_reader = ImageDocumentReader()
        self._readers: dict[str, DocumentReader] = {
            "pdf": pdf_reader,
            "docx": docx_reader,
            "jpg": image_reader,
            "jpeg": image_reader,
            "png": image_reader,
        }

    def reader_for(self, file_type: str) -> DocumentReader | None:
        return self._readers.get(file_type.lower().lstrip("."))

    def read(
        self,
        document_id: int,
        path: Path,
        file_type: str,
    ) -> DocumentReadResult:
        normalized_type = file_type.lower().lstrip(".")
        reader = self.reader_for(normalized_type)
        if reader is None:
            return failed_result(
                document_id,
                normalized_type,
                "DOCUMENT_READ_FAILED",
                f"没有可用的文档读取器: {normalized_type}",
            )
        return reader.read(document_id, path, normalized_type)
