from fastapi import APIRouter, HTTPException

from ..config import PROVIDER_DEFAULTS, ROUTER_DEFAULT_BASE_URL, ROUTER_DEFAULT_MODEL
from ..schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRunRequest,
    AgentRunResponse,
    HealthResponse,
    ProviderPreset,
    ProvidersResponse,
)
from ..sessions import session_store
from ..agent import run_agent, run_chat

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Service health check endpoint."""
    return HealthResponse(status="ok")


@router.post("/agent/run", response_model=AgentRunResponse)
async def agent_run(request: AgentRunRequest) -> AgentRunResponse:
    """Run the multi-step function calling agent to review student code."""
    try:
        return await run_agent(request, session_store)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected agent error: {type(exc).__name__}")


@router.post("/agent/chat", response_model=AgentChatResponse)
async def agent_chat(request: AgentChatRequest) -> AgentChatResponse:
    """Follow-up Q&A chat grounded in the previous review session."""
    try:
        return await run_chat(
            session_id=request.session_id,
            message=request.message,
            settings=request.settings,
            session_store=session_store,
        )
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected chat error: {type(exc).__name__}")


@router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """List available direct provider presets and 9router configuration."""
    presets = []
    for name, config in PROVIDER_DEFAULTS.items():
        presets.append(
            ProviderPreset(
                name=name,
                mode="direct",
                default_base_url=config["base_url"],
                default_model=config["model"],
                requires_key=(name != "custom"),
            )
        )
    presets.append(
        ProviderPreset(
            name="9router",
            mode="9router",
            default_base_url=ROUTER_DEFAULT_BASE_URL,
            default_model=ROUTER_DEFAULT_MODEL,
            requires_key=False,
        )
    )
    return ProvidersResponse(providers=presets)
