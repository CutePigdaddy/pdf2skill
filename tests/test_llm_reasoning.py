"""reasoning_effort passthrough tests (proxy + Python LLM client)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "frontend"))
sys.path.insert(0, str(PROJECT_ROOT))

import proxy_server as proxy  # noqa: E402
import utils.llm_client as llm_module  # noqa: E402


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload=None):
        self._payload = payload or {"choices": [{"message": {"content": "ok"}}]}

    def json(self):
        return self._payload


def test_proxy_forwards_reasoning_effort(monkeypatch):
    monkeypatch.setattr(
        proxy, "_resolve_provider", lambda provider: ("https://api.example.test/v1", "sk-test", {})
    )
    monkeypatch.setattr(proxy, "_load_settings", lambda: {"llm": {"timeout": 10, "max_retries": 1}})
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(proxy.requests, "post", fake_post)

    result = proxy.llm_chat(
        {
            "provider": "test",
            "model": "model-a",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
        }
    )

    assert result["ok"] is True
    assert captured["json"]["reasoning_effort"] == "high"


def test_proxy_omits_blank_reasoning_effort(monkeypatch):
    monkeypatch.setattr(
        proxy, "_resolve_provider", lambda provider: ("https://api.example.test/v1", "sk-test", {})
    )
    monkeypatch.setattr(proxy, "_load_settings", lambda: {"llm": {"timeout": 10, "max_retries": 1}})
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(proxy.requests, "post", fake_post)

    proxy.llm_chat(
        {
            "provider": "test",
            "model": "model-a",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "   ",
        }
    )

    assert "reasoning_effort" not in captured["json"]


@pytest.fixture
def llm_client_env(monkeypatch):
    config = {
        "llm": {
            "timeout": 10,
            "providers": {
                "test-provider": {
                    "base_url": "https://api.example.test/v1",
                    "api_key_env": "TEST_PROVIDER_API_KEY",
                    "peeling_model": "peel-model",
                    "peeling_reasoning_effort": "low",
                }
            },
            "routers": {"peeling_provider": "test-provider"},
        },
        "pdf": {"request_interval": 0},
    }
    monkeypatch.setattr(llm_module.config, "_config", config)
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "sk-test")
    for var in (
        "PEELING_MODEL",
        "PEELING_PROVIDER",
        "PEELING_MAX_TOKENS",
        "PEELING_REASONING_EFFORT",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)
    return config


def _capture_post(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(llm_module.RetrySession, "post", staticmethod(fake_post))
    return captured


def test_python_client_sends_stage_effort(llm_client_env, monkeypatch):
    captured = _capture_post(monkeypatch)

    client = llm_module.LLMClient(stage="peeling")
    assert client.chat("hi") == "ok"

    assert captured["json"]["reasoning_effort"] == "low"


def test_python_client_omits_unset_effort(llm_client_env, monkeypatch):
    llm_client_env["llm"]["providers"]["test-provider"]["peeling_reasoning_effort"] = ""
    captured = _capture_post(monkeypatch)

    client = llm_module.LLMClient(stage="peeling")
    client.chat("hi")

    assert "reasoning_effort" not in captured["json"]


def test_python_client_env_override(llm_client_env, monkeypatch):
    monkeypatch.setenv("PEELING_REASONING_EFFORT", "max")
    captured = _capture_post(monkeypatch)

    client = llm_module.LLMClient(stage="peeling")
    client.chat("hi")

    assert captured["json"]["reasoning_effort"] == "max"
