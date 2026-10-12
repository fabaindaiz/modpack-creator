import asyncio
import copy
from enum import Enum
from typing import Any, Callable, Optional
from aiohttp import hdrs, ClientSession, ClientTimeout
from src.library.api.handler import REQUEST, RESPONSE, DEFAULT_REQUEST, DEFAULT_RESPONSE
from src.library.api.session import SessionAuthorization, NO_AUTHORIZE
from src.library.api.limiter import CURRENT_USER, ON_WAIT, RateLimiter
from src.library.api.utils import FakeResponse, handle_errors, validate_results
from src.library.utils import getenv

__all__ = [
    "METHOD",
    "HttpAPI"
]

REQUEST_TIMEOUT = int(getenv("REQUEST_TIMEOUT", "30"))
CONNECT_TIMEOUT = int(getenv("CONNECT_TIMEOUT", "5"))
DEFAULT_TIMEOUT = ClientTimeout(total=REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)

class METHOD(Enum):
    GET = hdrs.METH_GET
    PATCH = hdrs.METH_PATCH
    POST = hdrs.METH_POST
    PUT = hdrs.METH_PUT
    DELETE = hdrs.METH_DELETE

POST_METHOD = {METHOD.PATCH, METHOD.POST, METHOD.PUT}
def KWARGS_DEFAULT() -> dict[str, Any]: return {}

class HttpAPI:
    """REST API client class for HTTP requests.

    Args:
        base_url (str): Base URL for the API. Must terminate with trailing slash (/).
        proxy (Optional[str], optional): Proxy URL for the API. Defaults to None.
        timeout (Optional[ClientTimeout], optional): Timeout settings for the API. Defaults to DEFAULT_TIMEOUT.
        session_auth (AuthorizedSession, optional): Authorization session for the API. Defaults to NO_AUTHORIZE.
    """
    def __init__(self,
            base_url: Optional[str],
            proxy: Optional[str] = None,
            timeout: Optional[ClientTimeout] = DEFAULT_TIMEOUT,
            session_auth: SessionAuthorization = NO_AUTHORIZE,
            session_headers: Optional[dict[str, str]] = None,
            session_kwargs_fun: Callable[[], dict[str, Any]] = KWARGS_DEFAULT,
            raise_for_status: bool = True,
            limiter: Optional[RateLimiter] = None,
            **kwargs) -> None:
        self.base_url = base_url
        # Every request takes its turn here first, when the client has one (spec 2026-10-09-economia-de-requests).
        self.limiter = limiter
        self.proxy = proxy
        self.timeout = timeout
        self.session_auth = session_auth
        self.session_headers = dict(session_headers or {})
        self.session_kwargs = kwargs
        self.session_kwargs_fun = session_kwargs_fun
        self.raise_for_status = raise_for_status
        self._session: Optional[ClientSession] = None
        self._session_loop: Optional[asyncio.AbstractEventLoop] = None

    def _client_session(self) -> ClientSession:
        """The client's one ClientSession, created on first use (i-7985ee-57034c).

        aiohttp: "making a session for every request is a very bad idea"; one per client keeps
        its connections alive. A session belongs to the event loop that made it, so a client used
        from another loop gets a new one. base_url and the session options are read once, here.
        """
        loop = asyncio.get_running_loop()
        if self._session is None or self._session.closed or self._session_loop is not loop:
            session_kwargs: dict = self.session_kwargs_fun()
            session_kwargs.update(self.session_kwargs)
            self._session = ClientSession(self.base_url, proxy=self.proxy, timeout=self.timeout,
                                          raise_for_status=self.raise_for_status, **session_kwargs)
            self._session_loop = loop
        return self._session

    async def close(self) -> None:
        """Close the client's session; the next request opens a new one. Called by the lifespan."""
        if self._session is not None and self._session_loop is asyncio.get_running_loop():
            await self._session.close()
        self._session = None
        self._session_loop = None
    
    async def _authorization(self,
            session_auth: Optional[SessionAuthorization] = None
            ) -> dict:
        """Test the authorization for the session.

        Args:
            session_auth (Optional[SessionAuthorization], optional): Authorization session for the API. Defaults to None (use default session).

        Returns:
            dict: Authorization headers for the session.
        """

        session_kwargs: dict = self.session_kwargs_fun()
        session_kwargs.update(self.session_kwargs)
        async with ClientSession(self.base_url, proxy=self.proxy, timeout=self.timeout, raise_for_status=True, **session_kwargs) as session:
            session_auth = session_auth or self.session_auth
            return await session_auth.headers(session)
    
    @handle_errors
    async def _request(self,
            method: METHOD,
            path: str,
            query: Optional[dict[str, Any]] = None,
            json: Optional[dict[str, Any]] = None,
            body: Optional[Any] = None,
            headers: Optional[dict] = None,
            session_auth: Optional[SessionAuthorization] = None,
            request: REQUEST = DEFAULT_REQUEST, # type: ignore
            response: RESPONSE = DEFAULT_RESPONSE, # type: ignore
            **kwargs) -> RESPONSE:
        """Send a request to the API.

        Args:
            method (METHOD): Method type for the request.
            path (str): Path for the request. 
            data (dict, optional): Parameters for the request. Automatically added to the body or query parameters. Defaults to {}.
            headers (dict, optional): Base headers for the request. Defaults to {}.
            session_auth (Optional[SessionAuthorization], optional): Authorization session for the API. Defaults to None (use default session).
            request (REQUEST, optional): Define which type of parameters will be used in the request. Defaults to JsonRequest.
            response (RESPONSE, optional): Define which type of response you expect from the request. Defaults to JsonResponse.

        Returns:
            RESPONSE: Response handler for the request. Same type as the response parameter.

        The request and response handlers passed in are prototypes: each call works on its own
        copy, because a handler keeps the body of its response and concurrent requests on one
        client would otherwise read each other's (i-7985ee-236b45).
        """
        request = copy.copy(request)
        response = copy.copy(response)
        session = self._client_session()
        try:
            session_auth = session_auth or self.session_auth
            request.set_use_body(method in POST_METHOD)

            headers = {**(headers or {}), **self.session_headers}
            headers = await request.headers(headers)
            headers = await response.headers(headers)
            headers = await session_auth.headers(session, headers)
            kwargs = await request.kwargs(query or {}, json or {}, body, kwargs)
            
            if self.limiter is not None:
                await self.limiter.acquire(CURRENT_USER.get(), on_wait=ON_WAIT.get())
            async with session.request(method.value, path, headers=headers, **kwargs) as results:
                if self.limiter is not None:
                    self.limiter.observe_headers(results.headers)
                await response.handle(results)
                await validate_results(results)
                return response
        
        except FakeResponse:
            await response.set_response(response.FAKE_RESPONSE)
            return response