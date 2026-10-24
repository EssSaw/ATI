import os
import pytest

from backend.app.config import resolve_settings, validate_base_url
from backend.app.schemas import Settings


def test_validate_base_url_valid_http_https():
    assert validate_base_url("http://localhost:8000") == "http://localhost:8000"
    assert validate_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert validate_base_url("http://127.0.0.1:20128/v1") == "http://127.0.0.1:20128/v1"
    assert validate_base_url("https://example.com/api") == "https://example.com/api"


def test_validate_base_url_rejects_credentials():
    with pytest.raises(ValueError, match="authentication credentials"):
        validate_base_url("https://user:password@example.com/v1")


def test_validate_base_url_rejects_query_and_fragment():
    with pytest.raises(ValueError, match="query parameters"):
        validate_base_url("https://example.com/v1?token=secret")
    with pytest.raises(ValueError, match="fragments"):
        validate_base_url("https://example.com/v1#section")


def test_validate_base_url_rejects_metadata_targets():
    metadata_urls = [
        "http://169.254.169.254/latest/meta-data",
        "http://169.254.10.20/api",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://metadata.goog/v1",
        "http://instance-data/v1",
        "http://[fd00:ec2::254]/latest",
    ]
    for url in metadata_urls:
        with pytest.raises(ValueError, match="forbidden"):
            validate_base_url(url)


def test_validate_base_url_rejects_non_http_schemes():
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        validate_base_url("ftp://example.com/v1")
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        validate_base_url("file:///etc/passwd")


def test_resolve_settings_direct_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-123")
    settings = Settings(mode="direct", provider="openai")
    resolved = resolve_settings(settings)
    assert resolved.base_url == "https://api.openai.com/v1"
    assert resolved.model == "gpt-4o-mini"
    assert resolved.api_key == "env-key-123"


def test_resolve_settings_request_key_precedence(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    settings = Settings(mode="direct", provider="openai", api_key="request-key")
    resolved = resolve_settings(settings)
    assert resolved.api_key == "request-key"


def test_resolve_settings_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(mode="direct", provider="openai", api_key=None)
    with pytest.raises(ValueError, match="Missing API key"):
        resolve_settings(settings)


def test_resolve_settings_9router_profile():
    settings = Settings(
        mode="9router",
        router_base_url="http://127.0.0.1:20128/v1",
        router_model="custom/model-x",
        router_api_key="router-key-99",
    )
    resolved = resolve_settings(settings)
    assert resolved.base_url == "http://127.0.0.1:20128/v1"
    assert resolved.model == "custom/model-x"
    assert resolved.api_key == "router-key-99"


def test_resolve_settings_custom_provider():
    settings = Settings(
        mode="direct",
        provider="custom",
        base_url="https://custom.llm.local/v1",
        api_key="custom-key-42",
        model="llama-3.1",
    )
    resolved = resolve_settings(settings)
    assert resolved.base_url == "https://custom.llm.local/v1"
    assert resolved.api_key == "custom-key-42"
    assert resolved.model == "llama-3.1"
