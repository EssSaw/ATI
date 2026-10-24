import ipaddress
import os
import re
from typing import NamedTuple, Optional
from urllib.parse import urlparse
from dotenv import load_dotenv

from .schemas import Settings

load_dotenv()


class ResolvedConfig(NamedTuple):
    base_url: str
    api_key: str
    model: str


# Preset defaults
PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3-mini",
        "env_key": "XAI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "env_key": "OPENROUTER_API_KEY",
    },
    "custom": {
        "base_url": "",
        "model": "default",
        "env_key": "CUSTOM_API_KEY",
    },
}

ROUTER_DEFAULT_BASE_URL = os.getenv("ROUTER_BASE_URL", "http://localhost:20128/v1")
ROUTER_DEFAULT_MODEL = os.getenv("ROUTER_MODEL", "openai/gpt-4o-mini")
ROUTER_DEFAULT_KEY = os.getenv("ROUTER_API_KEY", "router-dummy-key")

METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}


def validate_base_url(url: str, is_local_router: bool = False) -> str:
    """Validate that custom base URL adheres to strict security constraints:

    - Scheme must be http or https
    - No credentials (user:pass)
    - No query string
    - No fragment
    - Reject cloud metadata endpoints (169.254.169.254, link-local, etc.)
    - Localhost/127.0.0.1 is permitted
    """
    if not url or not isinstance(url, str):
        raise ValueError("Base URL must be a non-empty string.")

    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    if parsed.username or parsed.password:
        raise ValueError("URL must not contain authentication credentials.")

    if parsed.query:
        raise ValueError("URL must not contain query parameters.")

    if parsed.fragment:
        raise ValueError("URL must not contain URL fragments.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL does not contain a valid hostname.")

    hostname_lower = hostname.lower()

    if hostname_lower in METADATA_HOSTNAMES:
        raise ValueError(f"Access to cloud metadata host '{hostname}' is forbidden.")

    # Check for IP address restrictions
    try:
        ip = ipaddress.ip_address(hostname_lower)
        # Check for link-local addresses (e.g. 169.254.169.254, fe80::)
        if ip.is_link_local:
            raise ValueError(f"Access to link-local address '{hostname}' is forbidden.")
        # Specific check for AWS IPv6 metadata
        if str(ip) == "fd00:ec2::254":
            raise ValueError("Access to AWS metadata endpoint is forbidden.")
    except ValueError as e:
        # Not a raw IP address; check hostname string patterns
        if "forbidden" in str(e):
            raise
        if hostname_lower.startswith("169.254."):
            raise ValueError(f"Access to link-local IP '{hostname}' is forbidden.")

    # Normalize trailing slash
    return url.rstrip("/")


def resolve_settings(settings: Settings) -> ResolvedConfig:
    """Resolve effective base_url, api_key, and model based on mode and provider settings.

    Keys are request-scoped and never stored or logged.
    """
    if settings.mode == "9router":
        raw_url = settings.router_base_url or ROUTER_DEFAULT_BASE_URL
        base_url = validate_base_url(raw_url, is_local_router=True)
        model = settings.router_model or ROUTER_DEFAULT_MODEL
        api_key = settings.router_api_key or ROUTER_DEFAULT_KEY
        if not api_key:
            api_key = "dummy-router-key"
        return ResolvedConfig(base_url=base_url, api_key=api_key, model=model)

    # mode == "direct"
    provider = settings.provider
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"Unsupported provider '{provider}'.")

    preset = PROVIDER_DEFAULTS[provider]

    if provider == "custom":
        raw_url = settings.base_url or os.getenv("CUSTOM_BASE_URL", "")
        if not raw_url:
            raise ValueError("Custom provider requires 'base_url' in settings or CUSTOM_BASE_URL env.")
        base_url = validate_base_url(raw_url)
        model = settings.model or os.getenv("CUSTOM_MODEL", "default")
        api_key = settings.api_key or os.getenv("CUSTOM_API_KEY", "")
        if not api_key:
            raise ValueError("Custom provider requires an API key in settings or CUSTOM_API_KEY env.")
    else:
        raw_url = settings.base_url or preset["base_url"]
        base_url = validate_base_url(raw_url)
        model = settings.model or preset["model"]
        env_key_name = preset["env_key"]
        api_key = settings.api_key or os.getenv(env_key_name, "")
        if not api_key:
            raise ValueError(
                f"Missing API key for provider '{provider}'. Provide 'api_key' in settings or set {env_key_name}."
            )

    return ResolvedConfig(base_url=base_url, api_key=api_key, model=model)
