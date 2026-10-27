from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from .api.routes import router
from .project_chat import router as project_chat_router
from .workspaces import router as workspace_router
from .compiler import router as compiler_router
from .attachments import router as attachments_router

app = FastAPI(
    title="AI Coding Assistant for Students",
    description="Local project conversations and bounded Python code analysis with static tools and interactive tutoring.",
    version="1.0.0",
)

# CORS configuration permitting local development frontends
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    if request.url.hostname not in allowed_hosts:
        return JSONResponse(status_code=403, content={"detail": "Local application access only."})
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid_origin = parsed.scheme in {"http", "https"} and parsed.hostname in allowed_hosts - {"testserver"} and not parsed.username and not parsed.password
        except ValueError:
            valid_origin = False
        if not valid_origin:
            return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin."})
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse(status_code=403, content={"detail": "Cross-site requests are not allowed."})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return JSONResponse(status_code=415, content={"detail": "Application/json is required."})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Format request validation errors consistently as {detail: string}."""
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors])
    return JSONResponse(status_code=422, content={"detail": f"Validation error: {msg}"})


app.include_router(router)
app.include_router(project_chat_router)
app.include_router(workspace_router)
app.include_router(compiler_router)
app.include_router(attachments_router)
