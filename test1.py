import os
import asyncio

from src.plugin.modrinth.manager.project import ModrinthProjectManager

modrinth_project_manager = ModrinthProjectManager()
asyncio.run(modrinth_project_manager.project_add_one("betterf3"))