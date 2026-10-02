"""Web defences: a same-origin check on unsafe methods (CSRF) and the security headers (CSP)."""
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' https://cdn.modrinth.com data:; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

class SameOriginMiddleware:
    """Refuses a browser's unsafe request that does not come from this origin. `Sec-Fetch-Site`
    decides when present; otherwise `Origin` is compared with the app's own. A request with
    neither header is not from a browser and passes: the session cookie is what authenticates."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        fetch_site = headers.get("sec-fetch-site")
        origin = headers.get("origin")
        if fetch_site is not None:
            allowed = fetch_site in ("same-origin", "none")
        elif origin is not None:
            allowed = origin == f"{scope['scheme']}://{headers.get('host', '')}"
        else:
            allowed = True
        if allowed:
            return await self.app(scope, receive, send)
        response = JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        await response(scope, receive, send)

class SecurityHeadersMiddleware:
    """Adds the CSP and two companion headers to every response, except on paths under
    `exempt_prefixes` (the API docs load scripts from a CDN)."""

    def __init__(self, app: ASGIApp, exempt_prefixes: tuple[str, ...]) -> None:
        self.app = app
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        exempt = scope["path"].startswith(self.exempt_prefixes)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if not exempt:
                    headers["Content-Security-Policy"] = CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
            await send(message)

        await self.app(scope, receive, send_with_headers)
