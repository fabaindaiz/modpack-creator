from src.library.dependency.core import Module, module
from src.plugin.modrinth.client.api import ModrinthCDN, ModrinthAPI
from src.plugin.modrinth.database.version import ModrinthVersion

@module(
    dependents=[
        ModrinthCDN,
        ModrinthAPI,
    ],
)
class ModrinthClient(Module):
    """
    The Modrinth client module.

    This module provides access to the Modrinth API and CDN.
    """
    pass