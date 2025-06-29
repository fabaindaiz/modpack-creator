from src.library.dependency.core import Module, module
from src.plugin.modrinth.client import ModrinthClient
from src.plugin.modrinth.database import ModrinthDatabase

@module(
    declaration=[
        ModrinthClient,
        ModrinthDatabase,
    ],
)
class ModrinthModule(Module):
    def declare_providers(self):
        pass
