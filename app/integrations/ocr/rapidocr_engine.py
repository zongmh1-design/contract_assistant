from importlib.metadata import version
from pathlib import Path

from rapidocr import RapidOCR

from app.integrations.ocr.engine import (
    OcrEngineError,
    OcrLine,
    OcrRecognition,
)


class RapidOcrEngine:
    """RapidOCR 的 CPU 适配器；模型延迟初始化，避免普通接口承担启动开销。"""

    def __init__(self) -> None:
        self._engine: RapidOCR | None = None

    @property
    def version(self) -> str:
        return f"rapidocr-{version('rapidocr')}"

    def recognize_image(self, image_path: Path) -> OcrRecognition:
        try:
            if self._engine is None:
                self._engine = RapidOCR()
            output = self._engine(image_path)
            texts = output.txts if output.txts is not None else ()
            scores = output.scores if output.scores is not None else ()
            boxes = output.boxes
            lines = []
            for index, text in enumerate(texts):
                bbox = None
                if boxes is not None and index < len(boxes):
                    bbox = tuple(
                        (float(point[0]), float(point[1])) for point in boxes[index]
                    )
                confidence = float(scores[index]) if index < len(scores) else None
                lines.append(
                    OcrLine(
                        text=str(text).strip(),
                        confidence=confidence,
                        bbox=bbox,
                    )
                )
            return OcrRecognition(lines=tuple(line for line in lines if line.text))
        except Exception as error:
            raise OcrEngineError(f"RapidOCR 执行失败: {error}") from error
