"""Graba respuestas reales de Modrinth en tests/fixtures/modrinth/ (i-7985ee-f4ba63).

Los tests parsean estas respuestas, no un dict escrito a mano: un doble escrito a mano acepta lo
que su autor imaginó, no lo que la API manda (tarjeta test-double-fidelity). Solo lecturas
públicas. Se corre a mano cuando Modrinth cambie (docs/plan-de-trabajo.md §5), nunca en el gate:

    python scripts/record_modrinth_fixtures.py

Las listas se recortan a las primeras entradas, en el orden en que la API las devuelve, para
que el repositorio no cargue megas de changelogs.
"""
import json
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

API = "https://api.modrinth.com/v2/"
AGENT = "fabaindaiz/modpack-creator (test fixtures)"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "modrinth"

# Un mod de cliente, uno de ambos lados, uno de servidor, uno con dependencias y un shader,
# cuyos loaders (iris, optifine) no son loaders de mods.
PROJECTS = ["sodium", "fabric-api", "lithium", "iris", "complementary-reimagined"]
# Proyecto → (cuántas versiones, loaders, versiones de juego; vacío es todas). Un shader no usa el
# loader de mods. Fabric API publica para snapshots antes que nadie: sin filtro, solo vendrían esas.
VERSIONS = {"iris": (3, ["fabric"], []), "sodium": (2, ["fabric"], []),
            "fabric-api": (2, ["fabric"], ["26.3"]), "complementary-reimagined": (2, ["iris"], [])}
GAME_VERSIONS = 200


def get(path: str) -> Any:
    request = urllib.request.Request(API + path, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def write(name: str, data: Any) -> None:
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug in PROJECTS:
        write(f"project-{slug}.json", get(f"project/{slug}"))
    dependencies: set[str] = set()
    for slug, (count, loader_filter, game_filter) in VERSIONS.items():
        query = f"loaders={quote(json.dumps(loader_filter))}"
        if game_filter:
            query += f"&game_versions={quote(json.dumps(game_filter))}"
        versions = get(f"project/{slug}/version?{query}")[:count]
        write(f"versions-{slug}.json", versions)
        dependencies |= {d["version_id"] for v in versions for d in v["dependencies"] if d["version_id"]}
    # Las versiones exactas que piden las grabadas: sin ellas no se puede seguir una dependencia.
    ids = quote(json.dumps(sorted(dependencies)))
    write("versions-dependencies.json", get(f"versions?ids={ids}"))
    write("tag-game_version.json", get("tag/game_version")[:GAME_VERSIONS])
    (OUT / "RECORDED").write_text(f"{date.today().isoformat()} {API}\n")


if __name__ == "__main__":
    main()
