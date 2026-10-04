import logging
from typing import Optional
from fastapi import HTTPException, status

logger = logging.getLogger("ExceptionsLogger")

class HTTP_400_BAD_REQUEST(HTTPException):
    """Used when the user sends a malformed request"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_400_BAD_REQUEST, detail)
    
class HTTP_401_UNAUTHORIZED(HTTPException):
    """Used when the user is not authenticated"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_401_UNAUTHORIZED, detail)
    
class HTTP_403_FORBIDDEN(HTTPException):
    """Used when the user tries to access a resource that he is not allowed to"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_403_FORBIDDEN, detail)
    
class HTTP_404_NOT_FOUND(HTTPException):
    """Used when the requested resource is not found"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_404_NOT_FOUND, detail)

class HTTP_405_METHOD_NOT_ALLOWED(HTTPException):
    """Used when the method is not allowed"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_405_METHOD_NOT_ALLOWED, detail)
    
class HTTP_408_REQUEST_TIMEOUT(HTTPException):
    """Used when the request takes too long to process"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_408_REQUEST_TIMEOUT, detail)
    
class HTTP_409_CONFLICT(HTTPException):
    """Used when the request conflicts with the current state of the resource"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_409_CONFLICT, detail)

class HTTP_422_UNPROCESSABLE_ENTITY(HTTPException):
    """Used when a value is well formed as a request but not acceptable"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)

class HTTP_429_TOO_MANY_REQUESTS(HTTPException):
    """Used when an upstream API rate-limits us; carries how long it asked to wait, if it did"""
    def __init__(self, detail: Optional[str] = None, retry_after: Optional[float] = None):
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS, detail)
        self.retry_after = retry_after

class HTTP_500_INTERNAL_SERVER_ERROR(HTTPException):
    """Used when an unexpected error occurs"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_500_INTERNAL_SERVER_ERROR, detail)

class HTTP_501_NOT_IMPLEMENTED(HTTPException):
    """Used when the requested method is not implemented"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_501_NOT_IMPLEMENTED, detail)

class HTTP_502_BAD_GATEWAY(HTTPException):
    """Used when a request to another api fails"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_502_BAD_GATEWAY, detail)

class HTTP_504_GATEWAY_TIMEOUT(HTTPException):
    """Used when a request to another api takes too long to process"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_504_GATEWAY_TIMEOUT, detail)

class UpstreamError(HTTPException):
    """An external service (Modrinth, Pterodactyl, ...) failed. `detail` is the English text /v1
    returns; the panel shows its own Spanish message from `service` and `upstream_status`, plus
    `raw` (sanitized) as a detail."""
    def __init__(self, service: str, upstream_status: int, detail: Optional[str] = None, raw: str = ""):
        super().__init__(upstream_status, detail if detail is not None else raw)
        self.service = service
        self.upstream_status = upstream_status
        self.raw = raw
