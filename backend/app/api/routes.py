import httpx
from fastapi import APIRouter, HTTPException

from ..config import PROVIDER_DEFAULTS, ROUTER_DEFAULT_BASE_URL, ROUTER_DEFAULT_MODEL, resolve_settings
from ..schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRunRequest,
    AgentRunResponse,
    HealthResponse,
    ProviderPreset,
    ProvidersResponse,
    RouterModelsRequest,
    RouterModelsResponse,
    Settings,
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


@router.post("/agent/models", response_model=RouterModelsResponse)
async def router_models(request: RouterModelsRequest) -> RouterModelsResponse:
    try:
        config = resolve_settings(Settings(mode="9router", **request.model_dump()))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid gateway base URL. Use an HTTP(S) URL without credentials or query parameters.")

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.get(
                f"{config.base_url}/models",
                headers={"Authorization": "Bearer " + config.api_key},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise HTTPException(
            status_code=status if status in (401, 403, 404, 429) else 502,
            detail=f"Gateway model listing returned HTTP {status}.",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gateway model listing timed out.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not connect to the gateway to load models.")

    try:
        data = response.json()["data"]
        if not isinstance(data, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip()
            for item in data
        ):
            raise ValueError()
        models = sorted({item["id"] for item in data})
        limits = {}
        for item in data:
            context = item.get("context_length")
            output = item.get("max_completion_tokens")
            if (type(context) is int and 8192 <= context <= 2000000
                    and type(output) is int and 512 <= output <= 200000
                    and output < context):
                limits[item["id"]] = {"context_length": context, "max_completion_tokens": output}
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=502, detail="Gateway returned an invalid model list.")
    return RouterModelsResponse(models=models, limits=limits)


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
