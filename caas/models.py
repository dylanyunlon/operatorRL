"""
Data models for Context-as-a-Service.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class ContentTier(str, Enum):
    """Content importance tiers for structure-aware indexing."""
    TIER_1_HIGH = "tier_1_high"  # High Value: Titles, Headers, Class Definitions, API Contracts
    TIER_2_MEDIUM = "tier_2_medium"  # Medium Value: Body text, Function logic
    TIER_3_LOW = "tier_3_low"  # Low Value: Footnotes, Comments, Disclaimers


class ContextLayer(str, Enum):
    """Context triad layers defined by intimacy and access policy."""
    HOT = "hot"  # L1: The Situation - current conversation, active data (overrides everything)
    WARM = "warm"  # L2: The Persona - user profile, preferences (always-on filter)
    COLD = "cold"  # L3: The Archive - historical data (on-demand only)


class DocumentType(str, Enum):
    """Detected document types."""
    LEGAL_CONTRACT = "legal_contract"
    TECHNICAL_DOCUMENTATION = "technical_documentation"
    SOURCE_CODE = "source_code"
    RESEARCH_PAPER = "research_paper"
    ARTICLE = "article"
    TUTORIAL = "tutorial"
    API_DOCUMENTATION = "api_documentation"
    UNKNOWN = "unknown"


class ContentFormat(str, Enum):
    """Supported content formats."""
    PDF = "pdf"
    HTML = "html"
    CODE = "code"
    MARKDOWN = "markdown"
    TEXT = "text"


class Section(BaseModel):
    """Represents a section of a document."""
    title: str
    content: str
    weight: float = 1.0
    tier: Optional['ContentTier'] = None
    importance_score: float = 0.0
    start_pos: int = 0
    end_pos: int = 0
    parent_section: Optional[str] = None  # Parent section title for hierarchical context
    chapter: Optional[str] = None  # Chapter or major section name


class Document(BaseModel):
    """Represents a processed document."""
    id: str
    title: str
    content: str
    format: ContentFormat
    detected_type: DocumentType
    sections: List[Section] = []
    metadata: Dict[str, Any] = {}
    weights: Dict[str, float] = {}
    ingestion_timestamp: Optional[str] = None


class ContextRequest(BaseModel):
    """Request for context extraction."""
    document_id: Optional[str] = None
    query: str
    max_tokens: int = Field(default=2000, gt=0, le=10000)
    include_metadata: bool = True
    enable_time_decay: bool = Field(
        default=True, 
        description="Apply time-based decay to prioritize recent content (default: True)"
    )
    decay_rate: float = Field(
        default=1.0, 
        ge=0.01, 
        le=10.0,
        description="Rate of time decay. Higher = faster decay (default: 1.0)"
    )


class ContextResponse(BaseModel):
    """Response containing extracted context."""
    document_id: str
    document_type: DocumentType
    context: str
    sections_used: List[str] = []
    total_tokens: int
    weights_applied: Dict[str, float] = {}
    metadata: Dict[str, Any] = {}


class ContextTriadItem(BaseModel):
    """Represents an item in a context layer."""
    id: str
    layer: ContextLayer
    content: str
    metadata: Dict[str, Any] = {}
    timestamp: Optional[str] = None
    priority: float = 1.0


class ContextTriadState(BaseModel):
    """Represents the complete context triad state."""
    hot_context: List[ContextTriadItem] = []  # Current situation (conversation, errors)
    warm_context: List[ContextTriadItem] = []  # User persona (profile, preferences)
    cold_context: List[ContextTriadItem] = []  # Historical archive (old tickets, PRs)
    
    
class ContextTriadRequest(BaseModel):
    """Request for context triad retrieval."""
    include_hot: bool = Field(default=True, description="Include hot context (current situation)")
    include_warm: bool = Field(default=True, description="Include warm context (user persona)")
    include_cold: bool = Field(default=False, description="Include cold context (historical archive) - on-demand only")
    max_tokens_per_layer: Dict[str, int] = Field(
        default={"hot": 1000, "warm": 500, "cold": 1000},
        description="Token limits per layer"
    )
    query: Optional[str] = Field(default=None, description="Optional query for cold context retrieval")


class AddContextRequest(BaseModel):
    """Request for adding context to a layer."""
    content: str = Field(description="The context content to add")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata")
    priority: float = Field(default=1.0, ge=0.0, le=10.0, description="Priority level (0-10)")


class ContextTriadResponse(BaseModel):
    """Response containing context triad data."""
    hot_context: str = ""
    warm_context: str = ""
    cold_context: str = ""
    total_tokens: int
    layers_included: List[str]
    metadata: Dict[str, Any] = {}
