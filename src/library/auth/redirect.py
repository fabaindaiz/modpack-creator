import re

# A local path whose second character is never a slash: `//host` is protocol-relative and would
# send the browser to another host. The regex engine of pydantic has no lookahead, hence the
# explicit shape. The one rule for the `redirect` of the login, in /v1 and in the panel.
LOCAL_PATH = r"^/([a-zA-Z0-9_\-\?\=\&][a-zA-Z0-9/_\-\?\=\&]*)?$"
_LOCAL_PATH = re.compile(LOCAL_PATH)


def is_local_path(value: str) -> bool:
    """Whether `value` is a local path. `fullmatch`, so a trailing newline never matches."""
    return _LOCAL_PATH.fullmatch(value) is not None
