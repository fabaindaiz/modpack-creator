from enum import Enum
from typing import Any, Optional

class CustomEnum(str, Enum):
    """A string enum. One with an `unknown` member reads any value it does not know as `unknown`.

    External APIs add values (Modrinth added nine environments in 2025): a value from tomorrow
    must not make a whole catalog unreadable. An enum without `unknown` still refuses it.
    """
    @classmethod
    def _missing_(cls, value: Any) -> Optional["CustomEnum"]:
        return cls.__members__.get("unknown")
