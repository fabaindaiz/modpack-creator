from src.library.dependency.core import Module, module
from src.plugin.modrinth.database.project import ModrinthProject
from src.plugin.modrinth.database.version import ModrinthVersion

@module(
    dependents=[
        ModrinthProject,
        ModrinthVersion,
    ],
)
class ModrinthDatabase(Module):
    """
    The Modrinth database module.
    
    This module provides access to the Modrinth database, which includes
    projects and versions.
    """
    pass