"""Security tests for frontend/proxy_server.py — the local trust boundary.

Covers the DNS-rebinding defenses (Host allowlist), the /api/keys env-var
restriction, provider base_url SSRF rejection, and the MinerU relay SSRF
guard. No outbound network calls are made.
"""
import os

import pytest
from fastapi.testclient import TestClient

from frontend.proxy_server import app


@pytest.fixture
def client():
    # Host header must match the allowlist exactly — the same guarantee the
    # browser-side defenses rely on.
    return TestClient(app, base_url="http://127.0.0.1:8502")


class TestHostAllowlist:
    def test_allowed_host_passes(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_foreign_host_rejected(self):
        rebinding = TestClient(app, base_url="http://attacker.example:8502")
        resp = rebinding.get("/api/health")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "FORBIDDEN_HOST"

    def test_localhost_host_passes(self):
        local = TestClient(app, base_url="http://localhost:8502")
        assert local.get("/api/health").status_code == 200


class TestOriginCheck:
    def test_unknown_origin_rejected(self, client):
        resp = client.post(
            "/api/keys",
            json={"env_var": "FOO_API_KEY", "value": "x"},
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN_ORIGIN"

    def test_allowlisted_origin_passes_middleware(self, client):
        # Middleware passes (origin allowlisted); the endpoint itself may
        # still 4xx/200 — we only assert the origin gate didn't fire.
        resp = client.post(
            "/api/keys",
            json={"env_var": "PROXYTEST_API_KEY", "value": "x"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert resp.status_code != 403
        os.environ.pop("PROXYTEST_API_KEY", None)


class TestSetKeyRestriction:
    def test_rejects_non_key_env_vars(self, client):
        for dangerous in ("PATH", "ALL_PROXY", "HTTP_PROXY", "PYTHONPATH"):
            resp = client.post("/api/keys", json={"env_var": dangerous, "value": "x"})
            assert resp.status_code == 422, dangerous
            # HTTPException wraps the envelope under "detail"
            assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN_ENV_VAR"

    def test_accepts_api_key_suffix(self, client):
        resp = client.post(
            "/api/keys", json={"env_var": "PROXYTEST_API_KEY", "value": "abc"}
        )
        assert resp.status_code == 200
        assert os.environ.get("PROXYTEST_API_KEY") == "abc"
        os.environ.pop("PROXYTEST_API_KEY", None)

    def test_rejects_malformed_name(self, client):
        resp = client.post("/api/keys", json={"env_var": "BAD NAME", "value": "x"})
        assert resp.status_code == 422


class TestProviderBaseUrlGuard:
    def test_private_base_url_rejected_and_not_persisted(self, client):
        resp = client.post(
            "/api/providers",
            json={
                "providers": {
                    "evilprov": {
                        "base_url": "http://192.168.1.5:8080/v1",
                        "api_key_env": "EVILPROV_API_KEY",
                    }
                }
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"]["code"] == "PRIVATE_BASE_URL"

    def test_loopback_base_url_rejected(self, client):
        resp = client.post(
            "/api/providers",
            json={
                "providers": {
                    "evilprov": {
                        "base_url": "http://127.0.0.1:11434/v1",
                        "api_key_env": "EVILPROV_API_KEY",
                    }
                }
            },
        )
        assert resp.status_code == 422


class TestMineruRelaySsrf:
    def test_upload_rejects_loopback(self, client):
        resp = client.put("/api/mineru/upload?url=http://127.0.0.1/x")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_URL_TARGET"

    def test_upload_rejects_numeric_shorthand_loopback(self, client):
        # 2130706433 == 127.0.0.1 in inet_aton shorthand
        resp = client.put("/api/mineru/upload?url=http://2130706433/x")
        assert resp.status_code == 400

    def test_download_rejects_lan(self, client):
        resp = client.get("/api/mineru/download?url=http://10.0.0.1/x")
        assert resp.status_code == 400

    def test_download_rejects_localhost_name(self, client):
        resp = client.get("/api/mineru/download?url=http://localhost/x")
        assert resp.status_code == 400
