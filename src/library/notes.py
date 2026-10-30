"""What a note keeps of a pasted error (O8 tanda 2, P2 and P3 of d-7985ee-a68b5c). Pure."""
import math
import re

# A pasted error over ERROR_MAX keeps its first ERROR_HEAD and its last ERROR_TAIL (P2): a crash's
# description is at the top, its «Caused by» and the mod list at the end.
ERROR_MAX = 64 * 1024
ERROR_HEAD = 48 * 1024
ERROR_TAIL = 16 * 1024

# What is masked with *** before a note is stored (P3): a launcher's access token, `token=` and
# `access_token=` values, Discord webhooks, and IPv4 addresses. Players' names and UUIDs stay.
_SECRETS = [
    (re.compile(r"(--accessToken\s+)\S+"), r"\1***"),
    (re.compile(r"\b((?:access_)?token=)[^\s&]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "***"),
]


def cut_error(text: str) -> tuple[str, int]:
    """The error as stored, and how many KB were cut from its middle (0 when it fits). Its lines end as a
    textarea's value does (LF), whatever the form sent (CRLF): the panel counts that value before it is sent
    (static/app.js), and a cut rounds up, never «0 KB» (review of 2026-10-30)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= ERROR_MAX:
        return text, 0
    cut = math.ceil((len(text) - ERROR_HEAD - ERROR_TAIL) / 1024)
    return f"{text[:ERROR_HEAD]}\n… {cut} KB cortados …\n{text[-ERROR_TAIL:]}", cut


def mask_secrets(text: str) -> str:
    """`text` with its tokens, webhooks and IPv4 addresses replaced by ***."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text
