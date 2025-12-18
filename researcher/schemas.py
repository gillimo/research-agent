from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RequestSettings(BaseModel):
    k: int = Field(5, ge=1, description="Top-k results to return")
    cloud_allowed: bool = Field(False, description="Permit cloud hop")


class RequestContext(BaseModel):
    tags: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)


class Request(BaseModel):
    id: str
    query: str
    mode: str = Field("ask", pattern="^(ask|ingest)$")
    context: RequestContext = Field(default_factory=RequestContext)
    settings: RequestSettings = Field(default_factory=RequestSettings)


class ProvenanceEntry(BaseModel):
    source: str
    score: float
    text: str
    meta: Dict[str, Any] = Field(default_factory=dict)


class Confidence(BaseModel):
    local: float = 0.0
    cloud: float = 0.0


class Response(BaseModel):
    id: str
    answer: str
    provenance: Dict[str, List[ProvenanceEntry]] = Field(default_factory=dict)
    confidence: Confidence = Field(default_factory=Confidence)
    logs_ref: Optional[str] = None


class IngestedDoc(BaseModel):
    path: str
    chunks: int


class IngestResult(BaseModel):
    id: str
    ingested: List[IngestedDoc] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
