from src.model.pterodactyl.server import PterodactylFile, PterodactylServer
from src.library.api.client.pterodactyl import PterodactylAPI

class PterodactylServerService():
    def __init__(self,
            pterodactylAPI: PterodactylAPI):
        self.pterodactylAPI = pterodactylAPI