from abc import ABC, abstractmethod
from aiohttp import ClientSession
from pydantic import SecretStr
from typing import Optional

class SessionAuthorization(ABC):
    """Base class for authorized sessions"""
    @abstractmethod
    async def headers(self, session: ClientSession, headers: Optional[dict] = None) -> dict:
        """Update the headers for the request with the authorization data.

        Args:
            session (ClientSession): ClientSession object for the request.
            headers (dict, optional): Original headers for the request. Defaults to {}.

        Returns:
            dict: Updated headers for the request with the authorization data included.
        """
        pass

class NoAuthorization(SessionAuthorization):
    """Session class for no authorization"""
    async def headers(self, session: ClientSession, headers: Optional[dict] = None) -> dict:
        return dict(headers or {})

class TokenAuthorization(SessionAuthorization):
    """Session class for token authorization
    
    Args:
        token (str): Token for authorization.
        """
    def __init__(self,
            token: str|SecretStr,
            scheme: Optional[str] = "Bearer ",
            parameter: str = "Authorization",
            ) -> None:
        if isinstance(token, str):
            token = SecretStr(token)
        self.__token: SecretStr = token
        self.__scheme: Optional[str] = scheme
        self.__parameter: str = parameter
        super().__init__()
    
    async def headers(self, session: ClientSession, headers: Optional[dict] = None) -> dict:
        return {**(headers or {}), self.__parameter: f"{self.__scheme}{self.__token.get_secret_value()}"}

class APIKeyAuthorization(TokenAuthorization):
    """Session class for API key authorization
    
    Args:
        api_key (str): API key for authorization.
        """
    def __init__(self,
            api_key: str|SecretStr
            ) -> None:
        super().__init__(
            token=api_key,
            scheme="",  # No scheme for API key
            parameter="X-API-Key"  # Standard header for API keys
        )

NO_AUTHORIZE = NoAuthorization()