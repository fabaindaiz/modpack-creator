import logging
from pprint import pformat
from typing import cast
from src.library.dependency.core.module.base import Module
from src.library.dependency.core.container import Container
from src.library.dependency.core.resolver import resolve_dependency_layers
logger = logging.getLogger("DependencyLoader")

def resolve_dependency(container: Container, appmodule: type[Module]) -> None:
    # Cast due to mypy not supporting class decorators
    _appmodule = cast(Module, appmodule)
    logger.info(f"Resolving dependencies in {_appmodule}")

    unresolved_layers = _appmodule.init_providers()
    resolved_layers = resolve_dependency_layers(unresolved_layers)

    named_layers = pformat(resolved_layers)
    logger.info(f"Resolved layers:\n{named_layers}")

    for resolved_layer in resolved_layers:
        for provider in resolved_layer:
            provider.resolve(container, unresolved_layers)
    
    container.check_dependencies()
    container.init_resources()
    _appmodule.init_modules(unresolved_layers)
    _appmodule.init_bootstrap()
    logger.info("Dependencies resolved and injected")