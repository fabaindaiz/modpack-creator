"""The builds of a pack as the panel lists them (O8 tanda 1, d-7985ee-a99db7): their names and, with
the list, what is shown and what folds. Pure."""
from typing import Optional

# A build's name (B2): short, one line in the list and in the server's selects.
BUILD_NAME_MAX = 60


def build_name(name: Optional[str]) -> Optional[str]:
    """A name as it is stored: trimmed and cut at BUILD_NAME_MAX; empty or only spaces is no name
    (user-walk W15)."""
    trimmed = (name or "").strip()[:BUILD_NAME_MAX].strip()
    return trimmed or None
