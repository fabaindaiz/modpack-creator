"""Web defences: same-origin check on writes and the security headers."""
from typing import Any, cast

import pytest

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


def test_sec_fetch_site_none_post_is_refused():
    assert _client().post("/x", headers={"Sec-Fetch-Site": "none"}).status_code == 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_cross_site_are_refused(method):
    r = _client().request(method, "/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_null_origin_is_refused():
    assert _client().post("/x", headers={"Origin": "null"}).status_code == 403


def test_refusal_carries_the_security_headers():
    # Same order as main.py: headers outside the origin check.
    stack = SecurityHeadersMiddleware(SameOriginMiddleware(_app()), exempt_prefixes=("/docs",))
    r = TestClient(stack, base_url="https://app.example").post(
        "/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.headers["Content-Security-Policy"] == CSP
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_exempt_prefix_matches_whole_segments_only():
    client = _csp_client()
    assert client.get("/docsfoo").headers["Content-Security-Policy"] == CSP
    assert "Content-Security-Policy" not in client.get("/docs/oauth2-redirect").headers


# The real app: the middlewares above only protect it if main.py adds them. Without `with`, so
# the lifespan (MongoDB, OIDC) does not run and /v1 is not mounted: the 403 can only come from the
# same-origin check, which runs before routing.

def test_main_app_refuses_a_cross_site_write_to_v1() -> None:
    import main

    client = TestClient(main.app)
    refused = client.post("/v1/modpack", headers={"Sec-Fetch-Site": "cross-site"}, json={})
    assert (refused.status_code, refused.json()) == (403, {"detail": "Cross-origin request refused"})
    assert client.post("/v1/modpack", json={}).status_code != 403


def test_main_app_sends_the_csp_on_the_panel_and_not_on_the_docs() -> None:
    import main

    client = TestClient(main.app)
    panel = client.get("/", follow_redirects=False)
    assert panel.status_code in (302, 303, 307) and panel.headers["Content-Security-Policy"] == CSP
    docs = client.get(main.DOCS_PATH)
    assert docs.status_code == 200 and "Content-Security-Policy" not in docs.headers
