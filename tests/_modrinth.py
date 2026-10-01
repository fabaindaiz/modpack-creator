"""Un ModrinthAPI que contesta con las respuestas grabadas en tests/fixtures/modrinth/.

Lo que no está grabado no existe, como en la API: un proyecto desconocido no viene en la lista, y
una versión desconocida tampoco. Construir sus documentos exige Beanie inicializado (fixture
`mongo`).
"""
import copy
import json
from pathlib import Path
from typing import Any, Sequence

from src.model.modrinth import ModrinthProject, ModrinthVersion

FIXTURES = Path(__file__).parent / "fixtures" / "modrinth"
SODIUM, IRIS, FABRIC_API, SHADER = "AANobbMI", "YL57xq9U", "P7dR8mSH", "HVnmMxH1"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class RecordedModrinthAPI:
    def __init__(self) -> None:
        projects = [load(f.name) for f in sorted(FIXTURES.glob("project-*.json"))]
        self.projects = {key: p for p in projects for key in (p["id"], p["slug"])}
        self.versions = [v for f in sorted(FIXTURES.glob("versions-*.json")) for v in load(f.name)]
        self.calls: list[tuple[str, Any]] = []

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        self.calls.append(("project_get_many", tuple(slugs)))
        return [ModrinthProject.parse(**copy.deepcopy(self.projects[s])) for s in slugs if s in self.projects]

    async def project_versions(self, project: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (),
                               featured: bool | None = None) -> list[ModrinthVersion]:
        self.calls.append(("project_versions", (project, tuple(loaders), tuple(game_versions))))
        project_id = self.projects[project]["id"] if project in self.projects else project
        return [ModrinthVersion.parse(**copy.deepcopy(v)) for v in self.versions
                if v["project_id"] == project_id and set(loaders) & set(v["loaders"])
                and set(game_versions) & set(v["game_versions"])]

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        self.calls.append(("version_get_many", tuple(ids)))
        return [ModrinthVersion.parse(**copy.deepcopy(v)) for v in self.versions if v["id"] in ids]
