from typing import List, Literal, Optional
from pydantic import BaseModel, Field


BugType = Literal["syntax", "style", "runtime", "logic"]
ToolStatus = Literal["success", "error", "skipped"]
AgentMode = Literal["direct", "9router"]
DirectProvider = Literal["openai", "groq", "xai", "openrouter", "custom"]


class BugItem(BaseModel):
    type: BugType
    line: Optional[int] = None
    message: str
    suggestion: str


class Report(BaseModel):
    summary: str
    bugs: List[BugItem] = Field(default_factory=list)
    explanation: str
    fixed_code: str


class TraceItem(BaseModel):
    tool: str
    status: ToolStatus
    detail: str
    duration_ms: float


class Settings(BaseModel):
    mode: AgentMode = "direct"
    provider: DirectProvider = "openai"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    router_base_url: Optional[str] = None
    router_api_key: Optional[str] = None
    router_model: Optional[str] = None
    enable_code_execution: bool = False


class AgentRunRequest(BaseModel):
    code: str = Field(..., max_length=50000, description="Python code submitted for analysis")
    question: str = Field(default="", max_length=5000, description="Optional student question or prompt")
    settings: Settings = Field(default_factory=Settings)


class AgentRunResponse(BaseModel):
    session_id: str
    report: Report
    trace: List[TraceItem]
    tools_used: List[str]
    latency_ms: float


class AgentChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=10000)
    settings: Settings = Field(default_factory=Settings)


class AgentChatResponse(BaseModel):
    reply: str


class HealthResponse(BaseModel):
    status: str = "ok"


class ProviderPreset(BaseModel):
    name: str
    mode: str
    default_base_url: str
    default_model: str
    requires_key: bool


class ProvidersResponse(BaseModel):
    providers: List[ProviderPreset]
