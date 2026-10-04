from pydantic import BaseModel, Field


class IndexedDocument(BaseModel):
    document_id: str
    title: str
    content: str
    document_type: str
    source_system: str
    source_record_id: str
    acl_roles: list[str]
    business_unit_ids: list[str] = Field(default_factory=list)
    effective_from: str | None = None
    effective_to: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    document_types: list[str] = Field(default_factory=list)
    source_systems: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    document_id: str
    title: str
    content: str
    document_type: str
    source_system: str
    source_record_id: str
    bm25_rank: int | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None
    rrf_score: float
    rrf_rank: int


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
