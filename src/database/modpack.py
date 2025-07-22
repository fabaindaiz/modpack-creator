from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.model.modpack import (
    ModpackBundle,
    ModpackBundlePatch,
    ModpackProject,
    ModpackProjectPatch,
)
from src.exceptions import *

class ModpackDatabase():
    def bundle_find(self,
            bundle_ids: Optional[list[str]] = None,
            ) -> FindMany[ModpackBundle]:
        """
        Get all modpack bundles.
        
        :return: An iterable of ModpackBundle objects.
        """
        query = ModpackBundle.find()
        if bundle_ids:
            query = query.find(In(ModpackBundle.id, (PydanticObjectId(id) for id in bundle_ids)))
        return query

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        """
        Get a single modpack bundle by its ID.
        
        :param id: The ID of the bundle to retrieve.
        :return: The ModpackBundle object.
        """
        document = await ModpackBundle.get(bundle_id)
        if not document:
            raise ValueError(f"Bundle with id '{bundle_id}' not found.")
        return document
    
    async def bundle_update(self, bundle_id: str, patch: ModpackBundlePatch) -> ModpackBundle:
        """
        Update an existing modpack bundle.
        
        :param bundle_id: The ID of the bundle to update.
        :param patch: The patch containing the updated fields.
        :return: The updated ModpackBundle object.
        """
        document = await self.bundle_get(bundle_id)
        return await document.set(patch.model_dump(exclude_unset=True))

    async def bundle_insert(self, bundle: ModpackBundle) -> ModpackBundle:
        """
        Create a new modpack bundle.
        
        :param bundle: The ModpackBundle object to create.
        :return: The created ModpackBundle object.
        """
        return await ModpackBundle.create(bundle)
    
    async def bundle_insert_many(self, bundles: Iterable[ModpackBundle]) -> Iterable[ModpackBundle]:
        """
        Add multiple modpack bundles to the database.
        
        :param bundles: The list of ModpackBundle objects to add.
        :return: An iterable of created ModpackBundle objects.
        """
        result = await ModpackBundle.insert_many(bundles)
        return await self.bundle_find().to_list()
    
    async def bundle_save(self, bundle: ModpackBundle, bundle_id: Optional[PydanticObjectId] = None) -> ModpackBundle:
        """
        Save a modpack bundle to the database.
        
        :param bundle: The ModpackBundle object to save.
        :param bundle_id: Optional ID of the bundle to update.
        :return: The saved ModpackBundle object.
        """
        if bundle_id:
            bundle.id = PydanticObjectId(bundle_id)
        await bundle.save()
        return bundle

    def project_find(self,
            ids: Optional[list[str]] = None,
            bundle_id: Optional[str] = None,
            ) -> FindMany[ModpackProject]:
        """
        Get all projects in a bundle.
        
        :param bundle_id: The id of the bundle to retrieve projects from.
        :return: An iterable of ModpackProject objects.
        """
        query = ModpackProject.find(fetch_links=True)
        if ids:
            object_ids = [PydanticObjectId(id) for id in ids]
            query = query.find(In(ModpackProject.id, object_ids))
        if bundle_id:
            query = query.find(ModpackProject.modpack.id == PydanticObjectId(bundle_id)) # type: ignore
        return query
    
    async def project_get(self, project_id: str) -> ModpackProject:
        """
        Get a single project by its ID.
        
        :param project_id: The ID of the project to retrieve.
        :return: The ModpackProject object.
        """
        document = await ModpackProject.get(project_id)
        if not document:
            raise ValueError(f"Project with id '{project_id}' not found.")
        return document
    
    async def project_insert(self, project: ModpackProject) -> ModpackProject:
        """
        Add a single project to the database.
        
        :param project: The ModpackProject object to add.
        :return: The created ModpackProject object.
        """
        return await ModpackProject.create(project)
    
    async def project_update(self, project_id: str, patch: ModpackProjectPatch) -> ModpackProject:
        """
        Update an existing project.
        
        :param project_id: The ID of the project to update.
        :param patch: The patch containing the updated fields.
        :return: The updated ModpackProject object.
        """
        document = await self.project_get(project_id)
        return await document.set(patch.model_dump(exclude_unset=True))
    
    async def project_delete(self, project_id: str, bundle_id: Optional[str] = None) -> None:
        """
        Delete a project from the database.

        :param project_id: The ID of the project to delete.
        :return: None
        """
        document = await self.project_get(project_id)
        if bundle_id and document.modpack.id != PydanticObjectId(bundle_id):
            raise HTTP_404_NOT_FOUND(f"Project with ID '{project_id}' not found")
        await document.delete()
    
    async def project_insert(self, project: ModpackProject) -> ModpackProject:
        """
        Add a single project to the database.
        
        :param project: The ModpackProject object to add.
        :return: The created ModpackProject object.
        """
        return await ModpackProject.create(project)
    
    async def project_insert_many(self, projects: Iterable[ModpackProject]) -> Iterable[ModpackProject]:
        """
        Add multiple projects to the database.
        
        :param projects: The list of ModpackProject objects to add.
        :return: An iterable of created ModpackProject objects.
        """
        result = await ModpackProject.insert_many(projects)
        return await self.project_find(ids=result.inserted_ids).to_list()
    
    async def project_save(self, project: ModpackProject, project_id: Optional[PydanticObjectId] = None) -> ModpackProject:
        """
        Add a project to the database.
        
        :param project: The ModpackProject object to add.
        :param project_id: Optional ID of the project to update.
        :return: The created ModpackProject object.
        """
        if project_id:
            project.id = PydanticObjectId(project_id)
        await project.save()
        return project