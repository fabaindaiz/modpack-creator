# Selectores de versión de Minecraft y de loader (F09)

Investigación del 2026-10-05, solo lectura. Todo lo marcado "verificado" se comprobó con `curl` o
lectura directa el 2026-10-05; lo demás va como SIN VERIFICAR. No se editó el repositorio.

Contexto del repo: `MinecraftLoaders` (`src/model/utils.py`) lista bukkit, bungeecord, fabric, folia,
forge, neoforge, paper, purpur, quilt, spigot, sponge, velocity. `src/library/api/client/loaders.py`
ya consulta Fabric y Quilt (`/v2/versions/loader/{mc}` y `/v3/versions/loader/{mc}`); NeoForge y Forge
no tienen cliente (la versión la pone el admin).

## 1. Versiones de Minecraft

### Mojang: `version_manifest_v2.json`
Fuente: https://piston-meta.mojang.com/mc/game/version_manifest_v2.json (verificado en vivo);
descripción de campos en https://minecraft.wiki/w/Version_manifest.json (secundaria, comunitaria).

| Dato | Valor verificado |
|---|---|
| Raíz | `latest: {release, snapshot}` y `versions[]` (hoy `release: 26.3`, `snapshot: 26.4-snapshot-2`) |
| Cada entrada | `id`, `type`, `url`, `time`, `releaseTime`, `sha1`, `complianceLevel` |
| `type` | `release` (103), `snapshot` (753), `old_beta` (26), `old_alpha` (35); total 917 |
| Orden | ya viene de la más nueva a la más vieja por `releaseTime` (las dos primeras entradas lo confirman). Para listar releases: filtrar `type == "release"`, mantener el orden. No ordenar por `id` como texto (mezcla de `1.x` y `26.x`) |
| Tamaño | 277 KB |
| Caché | `cache-control: public, max-age=120`, `etag`, `last-modified`; responde 304 con `If-None-Match` (verificado) |
| Límites | no hay límite documentado (SIN VERIFICAR en documentación oficial; Mojang no publica doc de este endpoint) |
| Contrato | no es una API documentada por Mojang; la wiki dice que la ausencia de una versión en la lista la hace no disponible para el launcher |

### Modrinth: `GET /tag/game_version`
Fuente: https://docs.modrinth.com/api/ (límite y User-Agent) y la operación de `tag/game_version`
(campos leídos en docs.modrinth.com/api/operations/versionlist/), más prueba en vivo.

- Campos: `version`, `version_type` (`release`/`snapshot`/`alpha`/`beta`), `date`, `major`.
- En vivo: 917 entradas, mismos conteos que Mojang (103 release, 753 snapshot, 35 alpha, 26 beta). Es
  decir, Modrinth refleja el manifiesto completo, **no filtra a lo que sus mods apuntan**.
- Orden: de la más nueva a la más vieja (ya anotado en `docs/references.md`).
- `major`: solo 29 `true`, y es la etiqueta "versión destacada" histórica (1.21, 1.20.6, 1.20.4...). Las
  versiones `26.x` salen con `major: false`. No sirve como criterio de filtro.
- Caché: `cache-control: no-store` pero detrás de Cloudflare (`cf-cache-status: HIT`, `age: 52`).
  Límite documentado: 300 peticiones/minuto por IP, cabeceras `X-Ratelimit-*`; User-Agent
  identificable obligatorio.

### Cuál es mejor para el panel
Modrinth. Razones: mismos IDs que usan `game_versions` en las búsquedas y resoluciones del propio panel
(sin traducción), un solo host (ya es dependencia), `version_type` más simple. Mojang aporta lo
mismo sin ventaja funcional para este caso; solo sería mejor como respaldo o si se necesitara
`url`/`sha1` del `version.json` (no hace falta). Ninguna de las dos filtra por "versiones con mods"; si
se quisiera, habría que contar versiones de proyecto por `game_version` (no existe endpoint directo:
SIN VERIFICAR).

## 2. Versiones de loader por versión de Minecraft

| Loader | Fuente | Estable | Verificado |
|---|---|---|---|
| Fabric | `https://meta.fabricmc.net/v2/versions/loader/{mc}` (lista de objetos con `loader.version`, `loader.stable`, `intermediary`, `launcherMeta`) y `/v2/versions/loader` (global) y `/v2/versions/game` (`version`, `stable`) | `loader.stable` booleano. Ojo: no todas las versiones nuevas son estables (0.19.5 true, 0.19.4 false). Orden: la más nueva primero. 253 entradas para 1.21.1, ~100 KB | Sí. `cache-control: public, max-age=1800` |
| Quilt | `https://meta.quiltmc.org/v3/versions/loader/{mc}` (892 KB para 1.21.1, 307 entradas) y `/v3/versions/loader` global | No hay campo `stable`; hay que inferirlo por el texto (`-beta`). Orden NO fiable (la primera es `0.20.0-beta.9`, sale `0.24.0` sexta) | Sí. `cache-control: no-store`, `etag` presente |
| NeoForge | `https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml` (66 KB) o `.../api/maven/versions/releases/net/neoforged/neoforge` (JSON `{isSnapshot, versions[]}`) | Sufijo `-beta` = no estable; sin sufijo = estable (inferido por observación: 21.1.255 sin sufijo, 26.3.0.51-beta). La doc no define reglas de promoción | Sí (datos); doc: https://docs.neoforged.net/docs/gettingstarted/versioning/ |
| Forge | `https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json` (claves `{mc}-latest` y `{mc}-recommended`; 117 claves) | `recommended` existe solo para algunas versiones de MC (hoy: `26.3` solo tiene `latest`) | Sí |

Mapeo NeoForge a Minecraft (doc oficial y datos reales):
- Esquema viejo (1.20.2 a 1.21.x): `20.2.59` = 60.ª versión de 1.20.2; mayor = minor de MC, minor = parche de MC. `21.1.x` es 1.21.1. `21.0.x` es 1.21 (parche 0 se omite en MC pero no en NeoForge).
- Esquema nuevo (desde 26.1): cuatro componentes. `26.1.0.5-beta` es la 6.ª para Minecraft 26.1. Los tres primeros identifican el MC (`26.3.0` = `26.3`; el tercer número es el hotfix), el cuarto es la build. Ejemplo en vivo: `26.3.0.51-beta`.
- Prefijo para filtrar: se extrae el patrón de MC de cada versión. Casos raros en el maven (`0.25w14craftmine.3-beta`, `26.1.0.0-alpha.1+snapshot-1`) deben descartarse. La regla exacta para hotfix distinto de 0 (p. ej. MC `26.1.1`): SIN VERIFICAR (se infiere `26.1.1.x`).
- Forge: promociones por clave de MC; el maven (`maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml`) usa `{mc}-{forge}` (`26.3-66.0.9`, `1.20.1-47.4.26`). El número mayor de Forge (66, 47) sube por versión de MC.

Plataformas que solo son servidor (paper, purpur, folia, spigot, bukkit, velocity, bungeecord,
sponge): fuera de alcance. Paper tiene su API (`api.papermc.io/v2/projects/paper`) y Purpur la suya:
SIN VERIFICAR, no consultadas. Modrinth `/tag/loader` también lista estos nombres como loaders de
`plugin`, y otros no soportados por el repo (babric, legacy-fabric, liteloader, rift, ornithe...).
Para modpacks (`supported_project_types` incluye `modpack`) solo hay cuatro: **fabric, forge,
neoforge, quilt** (verificado). Un selector de modpack solo necesita esos cuatro.

## 3. Cómo lo hacen otras herramientas (solo ideas, sin copiar código)

| Herramienta | Licencia (verificada con `gh api`) | Idea observada |
|---|---|---|
| Prism Launcher | GPL-3.0 | Mantiene su propio meta (`meta.prismlauncher.org/v1/<uid>/index.json`) con `versions[]` con `version`, `recommended` (bool), `releaseTime`, `type`, `requires`. Verificado en vivo: Fabric loader 0.19.5 con `recommended: true`. El "recomendado" lo decide el meta, no el cliente |
| packwiz | MIT | `pack.toml` exige versión explícita (`minecraft`, `fabric`, `forge`, `quilt`); la doc no menciona "latest" o "recommended" (verificado en https://packwiz.infra.link/reference/pack-format/pack-toml/). Pone la elección en el usuario y la CLI ofrece ayuda aparte (SIN VERIFICAR) |
| mrpack-install | MIT | Para instalar servidor: `--minecraft-version` y `--flavor-version` con valor por defecto `latest` (README, verificado). Es una convención, no una lista |
| itzg/docker-minecraft-server | Apache-2.0 | SIN VERIFICAR (la página de docs dio 404): de memoria, `VERSION=LATEST` y para Forge `FORGE_VERSION=recommended`/`latest`; para Fabric versiones de loader y launcher con valor por defecto el último. No contrastado en fuente primaria |
| Modrinth App | repo `modrinth/code`: licencia `NOASSERTION` en la API de GitHub | SIN VERIFICAR el comportamiento de su selector (de memoria: lista de releases con casilla para mostrar snapshots y selector de loader "Stable/Latest/Other"). No se consultó el código; no reutilizar nada hasta aclarar la licencia |

Idea común: "latest" y "recommended" son atajos con nombre y lista explícita detrás; solo releases por
defecto, snapshots tras una opción.

## 4. Recomendación para el panel

| Campo | Fuente | Detalle |
|---|---|---|
| Versión de Minecraft | Modrinth `/tag/game_version`, respaldo Mojang manifest | Filtrar `version_type == "release"`, conservar el orden. Casilla "mostrar snapshots" que agrega `snapshot` (no `alpha`/`beta` viejos, 61 entradas; solo si alguien lo pide) |
| Loader (fabric/quilt) | meta del loader (ya hay cliente) | Fabric: usar `loader.stable`; por defecto el primer `stable == true`. Quilt: filtrar sin `-beta` y ordenar con parser de versión (la lista no viene ordenada; `quilt_loader_version` en `src/library/mrpack.py` ya hace algo parecido, ver `docs/references.md`) |
| Loader (neoforge) | maven-metadata de NeoForge (o su API JSON) | Filtrar por prefijo del MC elegido; sin `-beta` como estable; por defecto la mayor; ordenar numéricamente |
| Loader (forge) | `promotions_slim.json` | `{mc}-recommended` si existe, si no `{mc}-latest`; mostrar cuál es cuál. Una sola petición cubre todo |

Cantidad de peticiones: el loader solo se consulta al elegir la versión de MC (1 petición por
selección), no al abrir la página.

Caché (TTL propuesto, en memoria del proceso, con ETag si se puede):

| Fuente | TTL | Razón |
|---|---|---|
| Versiones de MC | 1 h | Mojang cachea 120 s; las versiones nuevas salen pocas veces por semana |
| Fabric loader por MC | 30 min | la propia fuente declara `max-age=1800` |
| Quilt (892 KB, `no-store`) | 1 h, solo al elegir MC | pesada; pedir el JSON global solo si hace falta |
| NeoForge maven (66 KB) | 15 a 30 min | cambia varias veces al día (`lastUpdated` hoy 09:04 UTC) |
| Forge promotions | 30 min a 1 h | `last-modified` de hace 3 días; sin `cache-control` |

Con "caché obsoleta aceptable" (stale-if-error): si la fuente falla, servir la última copia buena aunque
haya vencido su TTL, con una indicación.

Respaldo si la fuente cae:
1. Servir la copia vieja si existe.
2. Si no hay nada, **dejar el texto libre** (el comportamiento actual) con un aviso "no se pudo
   cargar la lista". El campo no puede bloquear la creación.
3. Aun con selector, aceptar un valor fuera de la lista ("Otra...") para versiones recién salidas o
   loaders que no tienen meta. NeoForge/Forge hoy no tienen cliente: sin ese respaldo el selector
   rompería lo que ya funciona.
4. Seguir validando en el servidor el valor recibido: un selector es ayuda de interfaz, no validación.

Qué mostrar: solo releases por defecto, con toggle de snapshots; loaders estables por defecto (con
"mostrar betas" en la misma casilla o aparte); marcar con una etiqueta la "recomendada".

Riesgos y notas para el repo:
- Las invariantes del repo piden contrastar contra especificación: añadir estas rutas a
  `docs/references.md` y a `tests/test_api_contracts.py` (método, ruta, forma) si se implementa.
- Los hosts nuevos (maven.neoforged.net, files.minecraftforge.net, piston-meta si se usa) deben ser
  configurables vía `getenv` con default, como `FABRIC_META_URL`; son lecturas (sin `backoff` sobre
  escrituras, no aplica).
- Seguir enviando un User-Agent identificable a Modrinth (requisito documentado).
- Los tests no pueden resolver nombres fuera de localhost: usar respuestas fijas.
- Los formatos de versión cambiaron en 2026 (1.21.x a 26.x en MC; 21.x a 26.x.y.z en NeoForge). No
  ordenar por texto ni asumir `1.`; cualquier orden debe venir de la fuente o de un parser que acepte
  ambos esquemas.

## Fuentes (acceso 2026-10-05)
- https://piston-meta.mojang.com/mc/game/version_manifest_v2.json
- https://minecraft.wiki/w/Version_manifest.json
- https://api.modrinth.com/v2/tag/game_version y /v2/tag/loader
- https://docs.modrinth.com/api/ y https://docs.modrinth.com/api/operations/versionlist/
- https://meta.fabricmc.net/v2/versions/loader/1.21.1 , /v2/versions/loader , /v2/versions/game
- https://meta.quiltmc.org/v3/versions/loader/1.21.1 , /v3/versions/loader
- https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml y https://docs.neoforged.net/docs/gettingstarted/versioning/
- https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json y https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml
- https://meta.prismlauncher.org/v1/net.fabricmc.fabric-loader/index.json , https://github.com/PrismLauncher/PrismLauncher
- https://packwiz.infra.link/reference/pack-format/pack-toml/ , https://github.com/nothub/mrpack-install (README)
- Licencias vía `gh api repos/<owner>/<repo>`
