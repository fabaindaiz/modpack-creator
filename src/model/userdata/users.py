from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from typing import Annotated, Literal
from src.library.utils.enum import CustomEnum

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
            self.email == model.email
        )

type UsersProvider = Annotated[
    UsersProviderAuthentik,
    Field(discriminator="type")
]

class UsersInternal(BaseModel):
    id: str = ""
    email: str
    name: str

    provider: UsersProvider
