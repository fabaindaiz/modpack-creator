from pydantic import BaseModel

class PterodactylInstance(BaseModel):
    name: str
    api_url: str
    token: str