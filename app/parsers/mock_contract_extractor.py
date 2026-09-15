from app.models import DocumentReadSnapshot
from app.schemas import StructuredContractExtraction


class MockContractExtractor:
    def __init__(
        self,
        extraction: StructuredContractExtraction | None = None,
        *,
        name: str = "mock_contract_extractor",
        version: str = "1.0",
        failure_message: str | None = None,
    ) -> None:
        self._extraction = extraction
        self._name = name
        self._version = version
        self.failure_message = failure_message
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    def extract(
        self, document_read: DocumentReadSnapshot
    ) -> StructuredContractExtraction:
        self.calls += 1
        if self.failure_message:
            raise RuntimeError(self.failure_message)
        if self._extraction is None:
            raise RuntimeError("MockContractExtractor 未配置提取结果")
        return self._extraction.model_copy(deep=True)
