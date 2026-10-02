"""Build a session cookie signed exactly as Starlette's SessionMiddleware does."""
import json
from base64 import b64encode
from typing import Any

import itsdangerous


def session_cookie(data: dict[str, Any], secret: str = "test-session-secret") -> str:
    payload = b64encode(json.dumps(data).encode("utf-8"))
    return itsdangerous.TimestampSigner(secret).sign(payload).decode("utf-8")
