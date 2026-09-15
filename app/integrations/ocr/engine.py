from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class OcrEngineError(RuntimeError):
    """OCR Provider 执行失败时抛出的统一边界异常。"""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float | None = None
    bbox: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True)
class OcrRecognition:
    lines: tuple[OcrLine, ...]


class OcrEngine(Protocol):
    @property
    def version(self) -> str: ...

    def recognize_image(self, image_path: Path) -> OcrRecognition: ...
