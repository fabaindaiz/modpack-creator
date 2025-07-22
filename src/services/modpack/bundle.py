from typing import Iterable
from src.model.modpack import ModpackBundle, ModpackProject, ProjectSourceModrinth
from src.model.modrinth import ModrinthProject, ModrinthVersion

class ModpackBundleService():
    def __init__(self) -> None:
        pass
    
    async def bundle_create_one(self, bundle: ModpackBundle) -> ModpackBundle:
        return await ModpackBundle.create(bundle)
    
    async def bundle_get_all(self) -> Iterable[ModpackBundle]:
        """
        Get all modpack bundles.
        
        :return: An iterable of ModpackBundle objects.
        """
        return await ModpackBundle.find_all().to_list()
    
    async def bundle_get_one(self, bundle_id: str) -> ModpackBundle:
        document = await ModpackBundle.get(bundle_id)
        if not document:
            raise ValueError(f"Bundle with id '{bundle_id}' not found.")
        return document
    
    async def project_get_all(self, bundle_id: str) -> Iterable[ModpackProject]:
        """
        Get all projects in a bundle.
        
        :param bundle_id: The id of the bundle to retrieve projects from.
        :return: An iterable of ModpackProject objects.
        """
        return await ModpackProject.find(ModpackProject.bundle_id == bundle_id).to_list()
    
    async def project_modrinth_add_one(self, bundle_id: str, project_id: str):
        project = await ModrinthProject.find_one({"project_id": project_id})
        if not project:
            raise ValueError(f"Project with id '{project_id}' not found.")
        modpack_project = ModpackProject(
            bundle_id=bundle_id,
            source=ProjectSourceModrinth(
                project=project # type: ignore
            )
        )