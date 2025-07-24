import asyncio
from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.database import ModpackDatabase, ModrinthDatabase
from src.model.modpack import ModpackBundle, ModpackProject, ProjectSourceModrinth
from src.exceptions import *

class ModpackService():
    def __init__(self,
            modpackDB: ModpackDatabase,
            modrinthDB: ModrinthDatabase,
            ) -> None:
        self.modpackDB = modpackDB
        self.modrinthDB = modrinthDB

    async def project_modrinth_create(self, bundle_id: str, project_id: str) -> ModpackProject:
        modrinth = await self.modrinthDB.project_get(project_id)
        bundle = await self.modpackDB.bundle_get(bundle_id)
        return await self.modpackDB.project_insert(
            project=ModpackProject(
                modpack=bundle, # type: ignore
                source=ProjectSourceModrinth(
                    project=modrinth))) # type: ignore
    
    async def project_modrinth_create_many(self, bundle_id: str, project_ids: list[str]) -> Iterable[ModpackProject]:
        modrinths = await self.modrinthDB.project_find(project_ids=project_ids).to_list()
        bundle = await self.modpackDB.bundle_get(bundle_id)
        return await asyncio.gather(*[
            self.modpackDB.project_insert(
                project=ModpackProject(
                    modpack=bundle, # type: ignore
                    source=ProjectSourceModrinth(
                        project=modrinth))) # type: ignore
                for modrinth in modrinths])