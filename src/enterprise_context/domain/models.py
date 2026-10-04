from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StringEnum(str, Enum):
    pass


class SupplierStatus(_StringEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    SUSPENDED = "SUSPENDED"
    UNDER_REVIEW = "UNDER_REVIEW"


class RequisitionState(_StringEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    CONVERTED = "CONVERTED"


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_system: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    observed_at: datetime


class SupplierSourceRecord(SourceRecord):
    supplier_name: str = Field(min_length=1)
    country_code: str = Field(min_length=2, max_length=2)
    postal_code: str | None = None
    tax_id: str | None = None
    website_domain: str | None = None
    category: str | None = None
    status: SupplierStatus | None = None


class ResolutionMethod(_StringEnum):
    EXACT_TAX_ID = "exact_tax_id"
    EXACT_DOMAIN = "exact_domain"
    NORMALIZED_NAME = "normalized_name"
    FUZZY_NAME = "fuzzy_name"
    NEW_ENTITY = "new_entity"


class ResolutionDecision(_StringEnum):
    AUTO_MERGE = "auto_merge"
    REVIEW = "review"
    SEPARATE = "separate"


class EntityResolutionResult(BaseModel):
    canonical_entity_id: str
    source_system: str
    source_record_id: str
    alias: str
    normalized_name: str
    resolution_method: ResolutionMethod
    confidence_score: float = Field(ge=0, le=1)
    decision: ResolutionDecision
    candidate_entity_id: str | None = None
    decided_at: datetime


class BusinessUnit(BaseModel):
    business_unit_id: str
    name: str
    source_system: str


class Buyer(BaseModel):
    buyer_id: str
    name: str
    business_unit_id: str
    approval_limit: Decimal = Field(ge=0)
    source_system: str


class Product(BaseModel):
    product_id: str
    name: str
    category: str
    source_system: str


class PurchaseRequisition(BaseModel):
    requisition_id: str
    supplier_id: str
    buyer_id: str
    business_unit_id: str
    state: RequisitionState
    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    product_ids: list[str]
    created_at: datetime
    source_system: str


class PurchaseOrder(BaseModel):
    purchase_order_id: str
    requisition_id: str | None = None
    supplier_id: str
    buyer_id: str
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: str
    source_system: str


class Contract(BaseModel):
    contract_id: str
    supplier_id: str
    start_date: date
    end_date: date
    permitted_categories: list[str]
    status: str
    source_system: str


class GoldenEntityPair(BaseModel):
    left_source_record_id: str
    right_source_record_id: str
    same_entity: bool
    source_system: Literal["golden_dataset"] = "golden_dataset"
