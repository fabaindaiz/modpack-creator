from pydantic import BaseModel
from src.library.datatypes import CustomEnum

class FileHash(BaseModel):
    sha512: str
    sha1: str

class MinecraftLoaders(CustomEnum):
    bukkit = "bukkit"
    bungeecord = "bungeecord"
    fabric = "fabric"
    folia = "folia"
    forge = "forge"
    neoforge = "neoforge"
    paper = "paper"
    purpur = "purpur"
    quilt = "quilt"
    spigot = "spigot"
    sponge = "sponge"
    velocity = "velocity"
    waterfall = "waterfall"