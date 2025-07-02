from pathlib import Path
from pydantic import BaseModel
from tinydb import TinyDB, where
from tinydb.database import Document
from tinydb.middlewares import CachingMiddleware
from tinydb.storages import JSONStorage
from tinydb.queries import QueryLike
from tinydb.table import Table
from tinydb_serialization import SerializationMiddleware
from tinydb_serialization.serializers import DateTimeSerializer
from threading import Lock
from typing import Generic, Optional, TypeVar
from src.library.utils import getenv

# TINYDB_FOLDER = getenv('TINYDB_FOLDER', '/data/tinydb')
TINYDB_FOLDER = getenv('TINYDB_FOLDER', 'data/tinydb')
M = TypeVar("M", bound=BaseModel)

class TinyDBDatabase(Generic[M]):
    def __init__(self, model_cls: type[M], file: str, table: str = "default"):
        path = Path(TINYDB_FOLDER).joinpath(file)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        storage = SerializationMiddleware(CachingMiddleware(JSONStorage))
        storage.register_serializer(DateTimeSerializer(), 'datetime')
        tinydb = TinyDB(path=path, storage=storage) # type: ignore
        self.__model_cls: type[M] = model_cls
        self.__database: Table = tinydb.table(table)
        self.__lock: Lock = Lock()
        super().__init__()
    
    def __enter__(self) -> 'TinyDBDatabase[M]':
        self.__lock.acquire(timeout=10)
        return self
    
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.__database.storage.flush() # type: ignore
        self.__lock.release()

    def parse(self, data: dict) -> M:
        return self.__model_cls(**data)
    
    def length(self) -> int:
        return len(self.__database)

    def exists(self, query: QueryLike) -> bool:
        return self.__database.contains(query)

    def get_one(self, query: QueryLike) -> M:
        document: Optional[Document] = self.__database.get(query) # type: ignore
        if document is None:
            raise ValueError("Document not found.")
        return self.parse(document)
    
    def get_one_optional(self, query: QueryLike) -> Optional[M]:
        document: Optional[Document] = self.__database.get(query) # type: ignore
        if document is None:
            return None
        return self.parse(document)

    def get_many(self, query: QueryLike) -> list[M]:
        documents: list[Document] = self.__database.search(query)
        return [self.parse(document) for document in documents]

    def get_all(self) -> list[M]:
        documents: list[Document] = self.__database.all()
        return [self.parse(document) for document in documents]

    def insert_one(self, data: dict) -> M:
        model = self.parse(data)
        self.__database.insert(model.model_dump())
        return model
    
    def insert_many(self, data: list[dict]) -> list[M]:
        return [self.insert_one(item) for item in data]
    
    def update(self, data: dict, query: QueryLike) -> int:
        return len(self.__database.update(data, query))

    def update_one(self, data: dict, query: QueryLike) -> M:
        document: Document = self.__database.get(query) # type: ignore
        document.update(data)
        model = self.parse(document)
        self.__database.update(model.model_dump(), doc_ids=[document.doc_id])
        return model

    def delete(self, query: QueryLike) -> int:
        return len(self.__database.remove(query))