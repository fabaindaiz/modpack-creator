from abc import ABC, abstractmethod
from beanie import Document, Indexed
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Optional
from src.library.datatypes import CustomEnum

class UsersProviderList(CustomEnum):
    AUTHENTIK = "authentik"

class UsersProviderBase(ABC, BaseModel):
    @abstractmethod
    def users_export(self) -> "UsersInternal":
        pass

    @abstractmethod
    def users_validate(self, model: "UsersProviderBase") -> bool:
        pass

class UsersProviderAuthentik(UsersProviderBase):
    type: Literal[UsersProviderList.AUTHENTIK] = UsersProviderList.AUTHENTIK
    iss: str
    sub: str
    name: str
    email: str
    email_verified: bool

    def users_export(self) -> "UsersInternal":
        return UsersInternal(
            email=self.email,
            name=self.name,
            provider=self
        )
    
    def users_validate(self, model: "UsersProviderBase") -> bool:
        if not isinstance(model, UsersProviderAuthentik):
            return False
        return (
            self.iss == model.iss and
            self.sub == model.sub
        )

type UsersProvider = Annotated[
    UsersProviderAuthentik,
    Field(discriminator="type")
]

class UsersInternal(Document):
    email: str
    name: str

    provider: UsersProvider

    # Sin use_cache (i-7985ee-c7b593): la caché de Beanie no se invalida al escribir, así que un
    # cambio en el usuario tardaría hasta 10 minutos en valer, y distinto en cada worker.
    class Settings:
        use_cache = False
