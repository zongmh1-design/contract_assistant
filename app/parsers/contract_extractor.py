from typing import Protocol

from app.models import DocumentReadSnapshot
from app.schemas import StructuredContractExtraction


class ContractExtractor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def extract(
        self, document_read: DocumentReadSnapshot
    ) -> StructuredContractExtraction: ...
