from pathlib import Path

from app.integrations.ocr.engine import (
    OcrEngineError,
    OcrLine,
    OcrRecognition,
)


class MockOcrEngine:
    """按调用顺序返回固定页结果，用于稳定测试成功、空结果和异常。"""

    def __init__(
        self,
        page_lines: list[list[str]] | None = None,
        *,
        version: str = "mock-ocr-1",
        failure_message: str | None = None,
    ) -> None:
        self.page_lines = page_lines or [["Mock OCR contract text"]]
        self._version = version
        self.failure_message = failure_message
        self.calls: list[Path] = []

    @property
    def version(self) -> str:
        return self._version

    def recognize_image(self, image_path: Path) -> OcrRecognition:
        self.calls.append(image_path)
        if self.failure_message:
            raise OcrEngineError(self.failure_message)
        page_index = min(len(self.calls) - 1, len(self.page_lines) - 1)
        lines = tuple(OcrLine(text=text) for text in self.page_lines[page_index])
        return OcrRecognition(lines=lines)
