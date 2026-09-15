from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import ContractParseStatus


class ExtractStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


class EvidencePosition(BaseModel):
    block_start: int | None
    block_end: int | None
    page_start: int | None
    page_end: int | None


class ExtractedFact(BaseModel):
    value: str | None
    source_text: str | None
    position: EvidencePosition | None
    extract_status: ExtractStatus
    extract_method: str


class ContractBasicInfo(BaseModel):
    contract_title: ExtractedFact
    contract_number: ExtractedFact
    signing_party: ExtractedFact
    counterparty: ExtractedFact
    amount: ExtractedFact
    currency: ExtractedFact
    effective_date: ExtractedFact
    expiry_date: ExtractedFact


class ContractClauses(BaseModel):
    payment_clause: ExtractedFact
    delivery_clause: ExtractedFact
    acceptance_clause: ExtractedFact
    breach_clause: ExtractedFact
    confidentiality_clause: ExtractedFact
    data_clause: ExtractedFact
    intellectual_property_clause: ExtractedFact
    dispute_resolution_clause: ExtractedFact


class StructuredContractExtraction(BaseModel):
    basic_info: ContractBasicInfo
    clauses: ContractClauses


class ContractParseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_read_snapshot_id: int
    basic_info_json: ContractBasicInfo
    clause_info_json: ContractClauses
    parse_status: ContractParseStatus
    parse_error: str | None
    extractor_name: str
    extractor_version: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_sqlite_datetime(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
