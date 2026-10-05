import os
import yaml
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar

WRAP = TypeVar("WRAP", bound=Callable[..., Any])
logger = logging.getLogger("EnvLogger")
# The keys of the env file that load_env ignored because YAML did not read them as text (an
# unquoted 10:30 is the base-60 number 630, an unquoted 8080 an int). Ignoring them is the old
# behavior and stays; a setting that must not be lost silently checks here (schedule_from_env).
SKIPPED_NON_STRING: set[str] = set()

def load_env(path: str = ".env", verbose: bool = False):
    if not os.path.exists(path):
        return
    envvars: dict[str, Any] = load_yaml(path)
    # Keys only, never values: the file holds secrets, and the log leaves the process.
    if verbose:
        logger.info(f"keys in {path}: {sorted(envvars)}")
    for key,value in envvars.items():
        if key in os.environ:
            logger.info(f"key already in environ, {path} ignored for it: {key}")
            continue
        if not isinstance(value, str):
            # The key only, never the value: it may be a secret.
            SKIPPED_NON_STRING.add(key)
            logger.warning(f"{key} in {path} is not a string and was ignored; quote its value")
            continue
        SKIPPED_NON_STRING.discard(key)
        os.environ[key] = value

def getenv(name: str, default: Optional[str] = None) -> str:
    """Get an environment variable with a default value.
    Args:
        name (str): The name of the environment variable.
        default (Optional[str]): The default value if the variable is not set.
    Returns:
        str: The value of the environment variable.
    Raises:
        Exception: If the environment variable is not found and no default is provided.
    """
    var = os.getenv(name, default)
    if var is None:
        raise Exception(f"Environment variable {name} not found.")
    return var

def getenv_opt(name: str, default: Optional[str] = None) -> Optional[str]:
    """Get an environment variable with an optional default value.
    Args:
        name (str): The name of the environment variable.
        default (Optional[str]): The default value if the variable is not set.
    Returns:
        Optional[str]: The value of the environment variable or the default value.
    """
    var = os.getenv(name, default)
    return var

def load_yaml(path: str) -> Any:
    """Loads a yaml file.
    Args:
        path_file (str): The file path.
    Returns:
        dict: The file data.
    """
    with open(path) as file:
        return yaml.load(file, Loader=yaml.FullLoader) # type: ignore

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

def now() -> float:
    return datetime.now(tz=timezone.utc).timestamp()

def utcnow() -> datetime:
    return datetime.now(timezone.utc)
