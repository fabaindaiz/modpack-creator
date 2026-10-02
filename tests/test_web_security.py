"""Web defences: same-origin check on writes and the security headers."""
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.library.web_security import CSP, SameOriginMiddleware, SecurityHeadersMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/x")
    async def post_x() -> dict:
        return {"ok": True}

    @app.get("/x")
    async def get_x() -> dict:
        return {"ok": True}

    @app.get("/docs-page")
    async def docs_page() -> dict:
        return {"ok": True}

    return app


def _client() -> TestClient:
    return TestClient(SameOriginMiddleware(_app()), base_url="https://app.example")


def test_cross_site_post_is_refused():
    r = _client().post("/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Cross-origin request refused"}


def test_same_site_post_is_refused():
    assert _client().post("/x", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403


def test_same_origin_post_passes():
    assert _client().post("/x", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_foreign_origin_without_fetch_metadata_is_refused():
    assert _client().post("/x", headers={"Origin": "https://evil.example"}).status_code == 403


def test_matching_origin_without_fetch_metadata_passes():
    assert _client().post("/x", headers={"Origin": "https://app.example"}).status_code == 200


def test_no_headers_passes():
    # A client that is not a browser.
    assert _client().post("/x").status_code == 200


def test_get_is_never_checked():
    assert _client().get("/x", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_forwarded_https_origin_passes():
    proxied = ProxyHeadersMiddleware(cast(Any, SameOriginMiddleware(_app())), trusted_hosts="*")
    client = TestClient(cast(Any, proxied), base_url="http://app.example")
    r = client.post("/x", headers={"X-Forwarded-Proto": "https", "Origin": "https://app.example"})
    assert r.status_code == 200


def _csp_client() -> TestClient:
    app = SecurityHeadersMiddleware(_app(), exempt_prefixes=("/docs", "/openapi.json"))
    return TestClient(app, base_url="https://app.example")


def test_csp_header_on_responses():
    r = _csp_client().get("/x")
    assert r.headers["Content-Security-Policy"] == CSP
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_csp_absent_on_exempt_prefix():
    r = _csp_client().get("/docs")
    assert "Content-Security-Policy" not in r.headers
