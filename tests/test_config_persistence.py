"""Persistence tests for the web config panel.

API keys must survive a proxy restart (written to ``.env``), and the rest of
the config must survive a browser change (written to ``settings.yaml``).
Both paths are exercised against temp files; the real project config is
never touched.
"""

import os

import pytest
import yaml
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from frontend import proxy_server as proxy

BASE_SETTINGS = {
    "llm": {
        "providers": {
            "siliconflow": {
                "base_url": "https://api.example.com/v1",
                "api_key_env": "SILICONFLOW_API_KEY",
                "chunking_model": "old-model",
                "chunking_reasoning_effort": "",
            },
        },
        "routers": {"chunking": "siliconflow", "peeling": "siliconflow", "skillEngine": "siliconflow"},
    },
    "mineru": {
        "api_mode": "remote",
        "language": "ch",
        "local": {
            "base_url": "http://localhost:7860",
            "backend": "vlm-auto-engine",
            "parse_method": "auto",
            "formula_enable": True,
            "table_enable": True,
        },
        "remote": {
            "model_version": "vlm",
            "upload_mode": "file",
            "page_ranges": "",
            "is_ocr": False,
            "enable_formula": True,
            "enable_table": True,
            "extra_formats": [],
        },
    },
    "paths": {"outputs_dir": "outputs"},
    "pdf": {
        "chunk_merge_threshold": 5000,
        "chunk_min_threshold": 1000,
        "page_limit": 200,
        "request_interval": 0.3,
    },
}


@pytest.fixture
def client():
    return TestClient(proxy.app, base_url="http://127.0.0.1:8502")


@pytest.fixture
def files(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(BASE_SETTINGS, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "# keep this comment\nEXISTING_API_KEY=old-value\nOTHER_SETTING=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(proxy, "SETTINGS_PATH", settings)
    monkeypatch.setattr(proxy, "ENV_PATH", env, raising=False)
    return settings, env


def test_set_key_writes_env_and_process(monkeypatch, client, files):
    _, env = files
    monkeypatch.delenv("NEWPROV_API_KEY", raising=False)

    resp = client.post(
        "/api/keys", json={"env_var": "NEWPROV_API_KEY", "value": "sk-new-123"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "persisted": True}
    assert os.environ["NEWPROV_API_KEY"] == "sk-new-123"
    values = dotenv_values(env)
    assert values["NEWPROV_API_KEY"] == "sk-new-123"
    text = env.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "OTHER_SETTING=1" in text
    os.environ.pop("NEWPROV_API_KEY", None)


def test_set_key_creates_missing_env(monkeypatch, client, files):
    _, env = files
    env.unlink()
    monkeypatch.delenv("FRESH_API_KEY", raising=False)

    resp = client.post(
        "/api/keys", json={"env_var": "FRESH_API_KEY", "value": "fresh-value"}
    )

    assert resp.status_code == 200
    assert dotenv_values(env)["FRESH_API_KEY"] == "fresh-value"
    os.environ.pop("FRESH_API_KEY", None)


def test_set_key_replaces_without_duplicating(monkeypatch, client, files):
    _, env = files
    monkeypatch.delenv("EXISTING_API_KEY", raising=False)

    resp = client.post(
        "/api/keys", json={"env_var": "EXISTING_API_KEY", "value": "rotated"}
    )

    assert resp.status_code == 200
    text = env.read_text(encoding="utf-8")
    assert text.count("EXISTING_API_KEY=") == 1
    assert dotenv_values(env)["EXISTING_API_KEY"] == "rotated"
    os.environ.pop("EXISTING_API_KEY", None)


def test_set_key_round_trips_special_characters(monkeypatch, client, files):
    _, env = files
    value = 'sk-a b"c\\d'
    monkeypatch.delenv("SPECIAL_API_KEY", raising=False)

    resp = client.post(
        "/api/keys", json={"env_var": "SPECIAL_API_KEY", "value": value}
    )

    assert resp.status_code == 200
    assert dotenv_values(env)["SPECIAL_API_KEY"] == value
    os.environ.pop("SPECIAL_API_KEY", None)


def test_set_key_rejects_newlines(monkeypatch, client, files):
    _, env = files
    before = env.read_text(encoding="utf-8")
    monkeypatch.delenv("BROKEN_API_KEY", raising=False)

    resp = client.post(
        "/api/keys", json={"env_var": "BROKEN_API_KEY", "value": "a\nINJECTED=1"}
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "INVALID_KEY_VALUE"
    assert env.read_text(encoding="utf-8") == before
    assert "BROKEN_API_KEY" not in os.environ


def test_set_key_rejects_dotenv_interpolation(monkeypatch, client, files):
    _, env = files
    before = env.read_text(encoding="utf-8")
    monkeypatch.delenv("TRICKY_API_KEY", raising=False)

    resp = client.post(
        "/api/keys", json={"env_var": "TRICKY_API_KEY", "value": "a${HOME}b"}
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "INVALID_KEY_VALUE"
    assert env.read_text(encoding="utf-8") == before


def test_config_persists_all_sections(client, files):
    settings, _ = files
    payload = {
        "providers": {
            "siliconflow": {
                "base_url": "https://api.example.com/v2",
                "api_key_env": "SILICONFLOW_API_KEY",
                "chunking_model": "new-model",
                "chunking_reasoning_effort": "high",
            },
            "local": {
                "base_url": "https://local.example.com/v1",
                "api_key_env": "LOCAL_API_KEY",
                "peeling_model": "peel",
            },
        },
        "routers": {"chunking": "local", "peeling": "siliconflow", "skillEngine": "siliconflow"},
        "pdf": {
            "request_interval": 0.5,
            "page_limit": 120,
            "chunk_merge_threshold": 6000,
            "chunk_min_threshold": 1200,
        },
        "mineru": {
            "api_mode": "local",
            "language": "en",
            "local": {
                "base_url": "http://192.168.1.9:7860",
                "backend": "pipeline",
                "parse_method": "ocr",
                "formula_enable": False,
                "table_enable": True,
            },
            "remote": {
                "model_version": "pipeline",
                "page_ranges": "1-10",
                "is_ocr": True,
                "enable_formula": False,
                "enable_table": False,
            },
        },
    }

    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200, resp.text

    data = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert data["llm"]["providers"]["siliconflow"]["chunking_model"] == "new-model"
    assert data["llm"]["providers"]["siliconflow"]["chunking_reasoning_effort"] == "high"
    assert data["llm"]["providers"]["local"]["peeling_model"] == "peel"
    assert data["llm"]["routers"] == payload["routers"]
    assert data["pdf"] == payload["pdf"]
    assert data["mineru"]["api_mode"] == "local"
    assert data["mineru"]["language"] == "en"
    assert data["mineru"]["local"]["base_url"] == "http://192.168.1.9:7860"
    assert data["mineru"]["remote"]["upload_mode"] == "file"  # not exposed by UI
    assert data["paths"] == BASE_SETTINGS["paths"]

    got = client.get("/api/config").json()
    assert got["ok"] is True
    assert got["providers"]["siliconflow"]["chunking_model"] == "new-model"
    assert got["routers"] == payload["routers"]
    assert got["pdf"] == payload["pdf"]
    assert got["mineru"]["local"]["backend"] == "pipeline"


def test_config_validation_failure_writes_nothing(client, files):
    settings, _ = files
    before = settings.read_text(encoding="utf-8")

    resp = client.post(
        "/api/config",
        json={
            "providers": {
                "siliconflow": {
                    "base_url": "https://api.example.com/v1",
                    "api_key_env": "SILICONFLOW_API_KEY",
                }
            },
            "pdf": {"page_limit": 0},
        },
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "INVALID_PDF_CONFIG"
    assert settings.read_text(encoding="utf-8") == before


def test_config_reuses_provider_security_checks(client, files):
    settings, _ = files
    before = settings.read_text(encoding="utf-8")

    resp = client.post(
        "/api/config",
        json={
            "providers": {
                "evil": {
                    "base_url": "http://127.0.0.1:11434/v1",
                    "api_key_env": "EVIL_API_KEY",
                }
            }
        },
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "PRIVATE_BASE_URL"
    assert settings.read_text(encoding="utf-8") == before


def test_config_keeps_cli_router_aliases_in_sync(client, files):
    settings, _ = files
    data = yaml.safe_load(settings.read_text(encoding="utf-8"))
    data["llm"]["routers"]["chunking_provider"] = "stale"
    settings.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    resp = client.post(
        "/api/config",
        json={"routers": {"chunking": "siliconflow", "peeling": "siliconflow", "skillEngine": "siliconflow"}},
    )

    assert resp.status_code == 200
    routers = yaml.safe_load(settings.read_text(encoding="utf-8"))["llm"]["routers"]
    assert routers["chunking_provider"] == "siliconflow"


def test_config_deletes_providers(client, files):
    settings, _ = files
    payload = {
        "providers": {
            "local": {
                "base_url": "https://local.example.com/v1",
                "api_key_env": "LOCAL_API_KEY",
            }
        },
        "delete_providers": ["siliconflow"],
        "routers": {"chunking": "local", "peeling": "local", "skillEngine": "local"},
    }

    resp = client.post("/api/config", json=payload)

    assert resp.status_code == 200, resp.text
    data = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert "siliconflow" not in data["llm"]["providers"]
    assert "local" in data["llm"]["providers"]


def test_config_rejects_unknown_router_stage(client, files):
    settings, _ = files
    before = settings.read_text(encoding="utf-8")

    resp = client.post("/api/config", json={"routers": {"unknownStage": "siliconflow"}})

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "INVALID_ROUTERS"
    assert settings.read_text(encoding="utf-8") == before


def test_legacy_providers_endpoint_still_writes(client, files):
    settings, _ = files

    resp = client.post(
        "/api/providers",
        json={
            "providers": {
                "siliconflow": {
                    "base_url": "https://api.example.com/v3",
                    "api_key_env": "SILICONFLOW_API_KEY",
                }
            },
            "routers": {"chunking": "siliconflow", "peeling": "siliconflow", "skillEngine": "siliconflow"},
        },
    )

    assert resp.status_code == 200
    data = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert data["llm"]["providers"]["siliconflow"]["base_url"] == "https://api.example.com/v3"
