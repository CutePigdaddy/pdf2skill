"""
Unit tests for POST /api/config provider management and POST /api/apikey.

Calls the endpoint functions directly.  The real ``config/settings.yaml``
is never touched: ``SETTINGS_PATH`` is monkeypatched to a temp copy.
"""

import sys
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "frontend"))

import server as fe_server  # noqa: E402

BASE_SETTINGS = {
    "llm": {
        "providers": {
            "alpha": {"base_url": "https://api.alpha.com/v1", "api_key_env": "ALPHA_API_KEY", "chunking_model": "m1"},
            "beta": {"base_url": "https://api.beta.com/v1", "api_key_env": "BETA_API_KEY"},
        },
        "routers": {
            "chunking_provider": "alpha",
            "peeling_provider": "beta",
            "skill_engine_provider": "alpha",
        },
    },
    "mineru": {"api_mode": "local"},
    "pdf": {"request_interval": 1},
}


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Point the server at a temp settings.yaml seeded with BASE_SETTINGS."""
    path = tmp_path / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(BASE_SETTINGS, f, allow_unicode=True)
    monkeypatch.setattr(fe_server, "SETTINGS_PATH", path)
    return path


def read_settings(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def error_code(exc_info):
    return exc_info.value.detail["error"]["code"]


# ── Provider add / update ───────────────────────────────────────────

def test_add_provider(settings_file):
    """A new provider is appended to settings.yaml."""
    res = fe_server.update_config({"providers": {
        "gamma": {
            "base_url": "https://api.gamma.com/v1",
            "api_key_env": "GAMMA_API_KEY",
            "chunking_model": "g1",
            "peeling_model": "g2",
            "skill_engine_model": "g3",
        },
    }})
    assert res == {"ok": True}
    saved = read_settings(settings_file)
    assert saved["llm"]["providers"]["gamma"]["base_url"] == "https://api.gamma.com/v1"
    assert saved["llm"]["providers"]["gamma"]["skill_engine_model"] == "g3"


def test_update_existing_provider(settings_file):
    """Existing provider fields are merged, others untouched."""
    fe_server.update_config({"providers": {"alpha": {"base_url": "https://new.alpha.com/v1"}}})
    saved = read_settings(settings_file)
    assert saved["llm"]["providers"]["alpha"]["base_url"] == "https://new.alpha.com/v1"
    assert saved["llm"]["providers"]["alpha"]["chunking_model"] == "m1"  # unchanged


def test_invalid_provider_name_rejected(settings_file):
    """Bad name -> INVALID_PROVIDER_NAME, file untouched."""
    with pytest.raises(HTTPException) as ei:
        fe_server.update_config({"providers": {"Bad Name!": {"base_url": "https://x.com/v1"}}})
    assert error_code(ei) == "INVALID_PROVIDER_NAME"
    assert read_settings(settings_file) == BASE_SETTINGS


def test_invalid_base_url_rejected(settings_file):
    """base_url without http(s) scheme -> INVALID_BASE_URL, file untouched."""
    with pytest.raises(HTTPException) as ei:
        fe_server.update_config({"providers": {"gamma": {"base_url": "ftp://x.com"}}})
    assert error_code(ei) == "INVALID_BASE_URL"
    assert read_settings(settings_file) == BASE_SETTINGS


def test_invalid_env_var_rejected(settings_file):
    """api_key_env not matching env-var pattern -> INVALID_ENV_VAR."""
    with pytest.raises(HTTPException) as ei:
        fe_server.update_config({"providers": {"gamma": {"base_url": "https://x.com/v1", "api_key_env": "1BAD NAME"}}})
    assert error_code(ei) == "INVALID_ENV_VAR"
    assert read_settings(settings_file) == BASE_SETTINGS


# ── Provider delete ─────────────────────────────────────────────────

def test_delete_provider_in_use_blocked(settings_file):
    """Provider referenced by a router -> PROVIDER_IN_USE, file untouched."""
    with pytest.raises(HTTPException) as ei:
        fe_server.update_config({"delete_providers": ["alpha"]})
    assert error_code(ei) == "PROVIDER_IN_USE"
    assert read_settings(settings_file) == BASE_SETTINGS


def test_delete_last_provider_blocked(settings_file):
    """Cannot delete when only one provider remains -> LAST_PROVIDER."""
    solo = {
        "llm": {
            "providers": {"solo": {"base_url": "https://solo.com/v1", "api_key_env": "SOLO_API_KEY"}},
            "routers": {},
        },
        "mineru": {},
        "pdf": {},
    }
    with open(settings_file, "w", encoding="utf-8") as f:
        yaml.dump(solo, f, allow_unicode=True)

    with pytest.raises(HTTPException) as ei:
        fe_server.update_config({"delete_providers": ["solo"]})
    assert error_code(ei) == "LAST_PROVIDER"


def test_delete_unused_provider(settings_file):
    """A provider not referenced by any router is removed."""
    fe_server.update_config({"providers": {
        "gamma": {"base_url": "https://api.gamma.com/v1", "api_key_env": "GAMMA_API_KEY"},
    }})
    res = fe_server.update_config({"delete_providers": ["gamma"]})
    assert res == {"ok": True}
    saved = read_settings(settings_file)
    assert "gamma" not in saved["llm"]["providers"]
    assert "alpha" in saved["llm"]["providers"]


# ── Routers ─────────────────────────────────────────────────────────

def test_router_unknown_provider_rejected(settings_file):
    """Router pointing at a missing provider -> ROUTER_UNKNOWN_PROVIDER."""
    with pytest.raises(HTTPException) as ei:
        fe_server.update_config({"routers": {"chunking_provider": "ghost"}})
    assert error_code(ei) == "ROUTER_UNKNOWN_PROVIDER"
    assert read_settings(settings_file) == BASE_SETTINGS


# ── API key env var validation ──────────────────────────────────────

def test_set_apikey_rejects_bad_env_var():
    """POST /api/apikey rejects non-env-var names with structured error."""
    with pytest.raises(HTTPException) as ei:
        fe_server.set_apikey({"env_var": "BAD NAME!", "value": "sk-x"})
    assert error_code(ei) == "INVALID_ENV_VAR"


# ── Path traversal ──────────────────────────────────────────────────

def test_delete_input_rejects_traversal(tmp_path, monkeypatch):
    """A filename escaping inputs/ via a sibling-prefix dir must be 403'd.

    Regression test: the old startswith() check let
    ``..\\inputs_evil\\victim.txt`` pass (string prefix matched ``inputs``)
    and delete a file outside the inputs/ directory.
    """
    import os

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    sibling = tmp_path / "inputs_evil"
    sibling.mkdir()
    victim = sibling / "victim.txt"
    victim.write_text("do not delete", encoding="utf-8")
    monkeypatch.setattr(fe_server, "INPUTS_DIR", inputs_dir)

    traversal = ".." + os.sep + "inputs_evil" + os.sep + "victim.txt"
    with pytest.raises(HTTPException) as ei:
        fe_server.delete_input(traversal)
    assert ei.value.status_code == 403
    assert victim.exists()  # file untouched

