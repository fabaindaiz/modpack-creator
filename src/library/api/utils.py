import asyncio
import backoff
from functools import wraps
from aiohttp import ClientResponse, ConnectionTimeoutError, ClientResponseError, ClientConnectionError, ServerConnectionError
from src.library.api.exceptions import *
from src.library.utils import WRAP

class FakeResponse(Exception): ...

def handle_auth(func: WRAP) -> WRAP:
    @wraps(func)
    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ClientResponseError as e:
            await asyncio.sleep(30)
            raise Exception("Authentication has failed") from e
    return wrapper # type: ignore

def handle_errors(func: WRAP) -> WRAP:
    @wraps(func)
    @backoff.on_exception(backoff.expo, ClientConnectionError, max_time=15)
    async def wrapper(*args, **kwargs): # type: ignore
        try:
            return await func(*args, **kwargs)
        except ServerConnectionError as e:
            raise HTTP_502_BAD_GATEWAY(f"Request has failed with connection error: {e}") from e
        except ConnectionTimeoutError as e:
            raise HTTP_504_GATEWAY_TIMEOUT(f"Request has failed with timeout error: {e}") from e
        except ClientResponseError as e:
            raise HTTPException(status_code=e.status, detail=f"Request has failed with status error {e.status}: {e.message}") from e
        except Exception as e:
            raise HTTP_500_INTERNAL_SERVER_ERROR(f"Request has failed with unhandled: {e}") from e
    return wrapper # type: ignore

# This function is not implemented yet, anyways validation already happens in aiohttp
async def validate_results(response: ClientResponse) -> None:
    pass