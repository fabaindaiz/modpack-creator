import logging
from typing import Any, Optional
from fastapi import HTTPException, status
from src.library.resolution import Lock

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

class BuildBlocked(HTTP_409_CONFLICT):
    """A build refused because its lock is not ready. Carries the fresh `lock`, and whether the only
    thing missing is the admin's decision on some mods (`only_undecided`), so the panel can show
    them to decide instead of an error."""
    def __init__(self, detail: str, lock: Lock, only_undecided: bool):
        super().__init__(detail)
        self.lock = lock
        self.only_undecided = only_undecided

class DowngradeRefused(HTTP_409_CONFLICT):
    """A pin before the floor sent without confirming it (F27, d-7985ee-c62c61): the detail says the
    step back; `downgrade` (a PinDowngrade) carries it, so the panel can ask to confirm."""
    def __init__(self, detail: str, downgrade: Any):
        super().__init__(detail)
        self.downgrade = downgrade

class ViaChangeRefused(HTTP_409_CONFLICT):
    """Moving a mod between its native and bridged versions without confirming it (batch C,
    d-7985ee-7e602f): the detail says the change; the panel asks with a dialog, `/v1` repeats with
    `confirm_via=true`."""
    def __init__(self, detail: str, project_id: str, title: str, to_native: bool, change: str = ""):
        super().__init__(detail)
        self.project_id = project_id
        self.title = title
        self.to_native = to_native
        # The change alone ("FabricMod: vía Connector → nativa (neoforge)"): the dialog adds the risk.
        self.change = change or detail

class CatalogPending(HTTP_409_CONFLICT):
    """The pack needs information from Modrinth the catalog lacks: a job brings it (spec
    2026-10-09-economia-de-requests, E6) and the pack resolves when it ends. `job` is that job."""
    def __init__(self, job: Any):
        super().__init__("Preparando el catálogo de Modrinth para este pack: se resuelve solo al terminar.")
        self.job = job

class HTTP_413_CONTENT_TOO_LARGE(HTTPException):
    """Used when a request body is larger than the route accepts"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_413_CONTENT_TOO_LARGE, detail)

class HTTP_422_UNPROCESSABLE_ENTITY(HTTPException):
    """Used when a value is well formed as a request but not acceptable"""
    def __init__(self, detail: Optional[str] = None):
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)

class HTTP_429_TOO_MANY_REQUESTS(HTTPException):
    """Used when an upstream API rate-limits us; carries how long it asked to wait, if it did"""
    def __init__(self, detail: Optional[str] = None, retry_after: Optional[float] = None):
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS, detail)
        self.retry_after = retry_after

class QuotaExceeded(HTTP_429_TOO_MANY_REQUESTS):
    """Refused here, before sending: the user (or the app) has no requests left this minute; the
    detail says how long to wait, in words for the panel (spec 2026-10-09-economia-de-requests, E5)."""

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
