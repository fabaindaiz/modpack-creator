from enum import Enum
from typing import Any, Callable, Optional
from aiohttp import hdrs, ClientSession, ClientTimeout
from src.library.api.handler import REQUEST, RESPONSE, DEFAULT_REQUEST, DEFAULT_RESPONSE
from src.library.api.session import SessionAuthorization, NO_AUTHORIZE
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
            session_headers: dict[str, str] = {},
            session_kwargs_fun: Callable[[], dict[str, Any]] = KWARGS_DEFAULT,
            raise_for_status: bool = True,
            **kwargs) -> None:
        self.base_url = base_url
        self.proxy = proxy
        self.timeout = timeout
        self.session_auth = session_auth
        self.session_headers = session_headers
        self.session_kwargs = kwargs
        self.session_kwargs_fun = session_kwargs_fun
        self.raise_for_status = raise_for_status
    
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
            query: dict[str, Any] = {},
            json: dict[str, Any] = {},
            body: Optional[Any] = None,
            headers: dict = {},
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
        """
        session_kwargs: dict = self.session_kwargs_fun()
        session_kwargs.update(self.session_kwargs)
        async with ClientSession(self.base_url, proxy=self.proxy, timeout=self.timeout, raise_for_status=self.raise_for_status, **session_kwargs) as session:
            try:
                session_auth = session_auth or self.session_auth
                request.set_use_body(method in POST_METHOD)

                headers.update(self.session_headers)
                headers = await request.headers(headers)
                headers = await response.headers(headers)
                headers = await session_auth.headers(session, headers)
                kwargs = await request.kwargs(query, json, body, kwargs)
                
                async with session.request(method.value, path, headers=headers, **kwargs) as results:
                    await response.handle(results)
                    await validate_results(results)
                    return response
            
            except FakeResponse:
                await response.set_response(response.FAKE_RESPONSE)
                return response