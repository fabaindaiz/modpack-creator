from pydantic import BaseModel

class Sftp(BaseModel):
    ip: str
    port: int

class Limits(BaseModel):
    memory: int
    swap: int
    disk: int
    io: int
    cpu: int

class Feature(BaseModel):
    databases: int
    allocations: int
    backups: int

class PteroServer(BaseModel):
    server_owner: bool
    identifier: str
    uuid: str
    node: str
    sftp_details: Sftp
    description: str
    limits: Limits
    feature_limits: Feature
    is_suspended: bool
    is_installing: bool
    relationships: dict