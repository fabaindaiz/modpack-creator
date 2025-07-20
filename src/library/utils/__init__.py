import os
import random
import yaml
import logging
import urllib.parse
from datetime import datetime, timezone
from pprint import pformat
from typing import Any, Callable, Optional, TypeVar

AUTO_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
WRAP = TypeVar("WRAP", bound=Callable[..., Any])
logger = logging.getLogger("EnvLogger")

def load_env(path: str = ".env", verbose: bool = False):
    if not os.path.exists(path):
        return
    envvars: dict[str, Any] = load_yaml(path)
    if verbose:
        logger.info(pformat(envvars))
    for key,value in envvars.items():
        if key in os.environ:
            logger.info(f"key already in environ -> {key}: {value}")
            continue
        if not isinstance(value, str):
            continue
        os.environ[key] = value

# Used to get environment variables with default values
def getenv(name: str, default: Optional[str] = None, fail_on_none: bool = True) -> str:
    var = os.getenv(name, default)
    if fail_on_none and var is None:
        raise Exception(f"Environment variable {name} not found.")
    return var # type: ignore

def load_yaml(path: str) -> Any:
    """Loads a yaml file.
    Args:
        path_file (str): The file path.
    Returns:
        dict: The file data.
    """
    with open(path) as file:
        return yaml.load(file, Loader=yaml.FullLoader)

def boolToStr(value: bool, int_format: bool = True) -> str:
    """Converts a boolean to a string. The boolean is considered True if it is True, 1 or '1'.

    Args:
        value (bool): The boolean to be converted to a string.
    
    Returns:
        str: The string representation of the boolean.
    """
    if int_format:
        return "1" if value else "0"
    return "true" if value else "false"

def strToBool(string: str) -> bool:
    """Converts a string to a boolean. The string is considered True if it is 'true', '1' or 't' (case insensitive).

    Args:
        string (str): The string to be converted to a boolean.
    
    Returns:
        bool: The boolean representation of the string.
    """
    return string.lower() in ('true', '1', 't')

def now() -> float:
    return datetime.now(tz=timezone.utc).timestamp()

def name_from_url(url: str) -> str:
    """
    Extract the name from a URL.
    
    Args:
        url (str): The URL to extract the name from.
        
    Returns:
        str: The extracted name.
    """
    url_split = url.split("/")
    if len(url_split) < 1:
        raise ValueError("Cannot extract name from URL")
    return urllib.parse.unquote(url.split("/")[-1])