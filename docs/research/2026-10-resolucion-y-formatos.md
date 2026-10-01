# Resolución de dependencias, metadatos de versiones y formatos de exportación

**Fecha:** 2026-10-01 · **Método:** especificaciones y código fuente primarios, más llamadas GET
reales a las APIs públicas ese día. Lo inferido dice **INFERIDO** o **SIN VERIFICAR**. Solo lectura.

Prefijos: **[OA]** = [OpenAPI de Modrinth v2.7.0](https://docs.modrinth.com/openapi.yaml),
**[MC]** = [modrinth/code @64fc2b9](https://github.com/modrinth/code/blob/64fc2b9d917324452c00a41b2048002d6ca8f5e4),
**[PW]** = [packwiz @ef87d96](https://github.com/packwiz/packwiz/blob/ef87d964f8cbd52b3b13ea42453ef322290e2b9e),
**[PR]** = [Prism Launcher](https://github.com/PrismLauncher/PrismLauncher/blob/develop),
**[FE]** = [ferium](https://github.com/gorilla-devs/ferium/blob/main).

## Lo que cambia en el código actual

| Hallazgo | Dónde pega | Ítem |
|---|---|---|
| **Minecraft pasó a versiones por año**: la última release es `26.3`, el último snapshot `26.4-snapshot-2` ([manifiesto de Mojang](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json)). Nunca ordenar versiones de juego parseándolas como `1.x.y`: usar el orden del manifiesto o de `/tag/game_version`. | todo filtro o comparación de `game_versions` | i-7985ee-f4ba63 |
| **`GET /project/{id}/dependencies` no sirve para resolver**: devuelve las dependencias de *todas* las versiones del proyecto, sin filtrar por versión de juego, loader ni tipo (incluye las incompatibles) ([MC] `apps/labrinth/src/database/models/project_item.rs#L1054-L1062`). | `ModrinthService.project_dependencies` lo usa | i-7985ee-f4ba63 |
| **`client_side` y `server_side` están deprecados** en favor de `environment` (9 valores). En vivo, `environment` de un proyecto es un array y el de una versión un string; la spec dice enum en ambos. | `ModrinthProject` exige `client_side`/`server_side`: si Modrinth los quita, el parseo falla | i-7985ee-f4ba63 |
| En los datos reales las dependencias casi siempre traen solo `project_id` (`version_id = null`). | la resolución tiene que elegir la versión ella misma | i-7985ee-f4ba63 |

## 1. La API de Modrinth para resolver

- **`VersionDependency`**: `version_id`, `project_id`, `file_name` (todos anulables) y
  `dependency_type` ∈ `required | optional | incompatible | embedded`. La spec no define `embedded`
  [OA]. Tratarlo como "viene dentro del jar padre" es INFERIDO.
- **`Version`**: `game_versions`, `loaders`, `version_type`, `featured`, `status`, `date_published`,
  `environment`, `files[]` (`hashes.sha1/sha512`, `url`, `filename`, `primary`, `size`). Sin
  `primary`, usar el primero [OA].
- **Listar versiones**: `GET /project/{id}/version?loaders=["fabric"]&game_versions=["…"]&include_changelog=false`.
  El servidor también acepta `version_type`, `limit` y `offset` aunque la spec no los liste
  ([MC] `apps/labrinth/src/routes/v2/versions.rs#L34-L43`). Vienen ordenadas por `date_published`,
  la más nueva primero (verificado en vivo).
- **Actualizaciones**: `POST /version_files/update` con `{hashes, algorithm, loaders, game_versions, version_types}`
  devuelve hash → la versión más nueva compatible (probado en vivo).
- **En lote**: `GET /projects?ids=[…]`, `GET /versions?ids=[…]`.
- **Búsqueda**: facets anidados (OR dentro, AND entre); los loaders son `categories`; `limit` ≤ 100.
- **Tags**: `/tag/loader` y `/tag/game_version` (con `version_type` y `date`, la más nueva primero).
- **Límites**: 300 requests/min por IP con o sin token; encabezados `X-Ratelimit-*`; User-Agent
  único obligatorio (`usuario/proyecto/versión (contacto)`) [OA] L78-95. El `.env.default` ya trae
  `MODRINTH_AGENT`.

## 2. Elegir la versión del loader

| Loader | Endpoint | Cómo elegir |
|---|---|---|
| Fabric | [meta](https://github.com/FabricMC/fabric-meta/blob/master/README.md) `/v2/versions/loader/{mc}` | la primera con `loader.stable` |
| Quilt | `https://meta.quiltmc.org/v3/versions/loader/{mc}` | **no filtra por versión ni ordena**: ordenar por semver y descartar `-beta` |
| NeoForge | `maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge` | por prefijo: `X.Y.` para `1.X.Y`; **desde 2026, cuatro componentes** `26.1.0.<build>` ([anuncio](https://github.com/neoforged/websites/blob/main/content/news/26.1release.md#versioning)); preferir sin `-beta` |
| Forge | [promotions_slim.json](https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json) | `<mc>-recommended`, si no `<mc>-latest` (26.3 aún no tiene recomendada) |

## 3. Formatos de exportación

- **`.mrpack`** ([spec](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)):
  `modrinth.index.json` con `formatVersion: 1`, `game`, `versionId`, `name`, `files[]` (`path`,
  `hashes` sha1+sha512 obligatorios, `env`, `downloads`, `fileSize`) y `dependencies` (`minecraft`,
  `forge`, `neoforge`, `fabric-loader`, `quilt-loader`; tolerar claves desconocidas). Descargas solo
  desde `cdn.modrinth.com`, `github.com`, `raw.githubusercontent.com`, `gitlab.com`. Todo sale de
  `Version.files`; `env` se mapea desde `environment` como lo hace packwiz
  ([PW] `modrinth/modrinth.go#L345-L385`).
- **packwiz** ([pack.toml](https://packwiz.infra.link/reference/pack-format/pack-toml/),
  [index.toml](https://packwiz.infra.link/reference/pack-format/index-toml/),
  [mod.toml](https://packwiz.infra.link/reference/pack-format/mod-toml/)): `pack.toml`
  (`pack-format = "packwiz:1.1.0"`, `index`, `versions`; acepta `neoforge` aunque la tabla no lo
  diga, [PW] `core/pack.go#L201`), `index.toml` y un `.pw.toml` por mod con `[update.modrinth]`.
- **CurseForge**: `allowModDistribution` y `downloadUrl` nulo significan que el autor no permite
  descargas de terceros; ferium lo trata como error ([FE] `libium/src/upgrade/mod.rs#L66-L95`).
  **Nunca re-alojar ni embeber esos jars.**

## 4. Cómo resuelven los demás

| Herramienta | Sigue | Elige | Incompatibles | Backtracking |
|---|---|---|---|---|
| Modrinth App ([MC] `packages/modrinth-content-management/src/install/mod.rs`) | solo `required`, DFS | la más nueva por fecha que calza juego + loader | **no las mira** | no; un conflicto se registra y gana la primera |
| packwiz ([PW] `modrinth/install.go#L228-L345`) | solo `required`, ≤ 20 ciclos | la más nueva por fecha | no | no |
| Prism ([PR] `launcher/minecraft/mod/tasks/GetModDependenciesTask.cpp`) | solo `required`, profundidad ≤ 20 | la más nueva | no | no |
| ferium ([FE] `src/subcommands/upgrade.rs#L93-L104`) | `required`, transitivas; ignora el `version_id` fijado | la más nueva según filtros | las junta y no las usa | no |

- **Ninguna usa un solver** (SAT o backtracking): todas son golosas, "la más nueva primero".
- Fabric↔Quilt: packwiz y Prism reemplazan Fabric API → QFAPI en Quilt
  ([PW] `modrinth/modrinth.go#L474-L486`). Es el mismo mecanismo que "declarar reemplazos de
  dependencias" de `docs/requirements.md`.
- Los filtros de ferium (loader preferido, versión de juego estricta o "menor", canal, regex de
  nombre) son un buen modelo ([FE] `libium/src/config/filters.rs#L7-L37`).
- **Las restricciones precisas solo están dentro de los jars**: `fabric.mod.json` (`depends`,
  `breaks`, rangos de versión) y NeoForge `[[dependencies]]` (`versionRange`, `type`, `side`).
  Modrinth no tiene rangos: un proyecto o una versión exacta.

**Lo que este proyecto puede hacer mejor que todos:** mirar `incompatible`, que nadie mira, y
reportar por qué está cada mod en el pack.

## 5. Algoritmo propuesto

Ítem: i-7985ee-7f735a. Entrada: versión de juego, loader, lista de proyectos, reglas de reemplazo, pins y canales.
Salida: un *lock* con conflictos reportados. No usa `/project/{id}/dependencies`.

1. **Validar el destino**: la versión de juego existe (`/tag/game_version`), el loader existe
   (`/tag/loader`), elegir la versión del loader (§2).
2. **Normalizar**: slugs → ids (`/projects?ids=`), guardar ids. Reglas de reemplazo: dependencia →
   sustituto o "quitar"; sembrar Fabric API → QFAPI en Quilt.
3. **Candidatos** por proyecto: `/project/{id}/version` filtrado por loader y juego; `status=listed`;
   ordenar por canal permitido (release > beta > alpha) y fecha; un pin del admin manda. En Quilt
   aceptar también versiones `fabric`.
4. **Lista de trabajo golosa**: elegir el mejor candidato; por cada dependencia `required`, aplicar
   primero los reemplazos; si trae `version_id`, buscarla en lote (`/versions?ids=`) y comprobar
   que calza; si no, el mejor candidato de ese proyecto. Registrar quién pidió a quién.
5. **Unificar** por proyecto: si ya está elegido y satisface, se queda; si dos piden versiones
   fijas distintas, reportar `pin en conflicto (A quiere v1, B quiere v2)`. Opcional: backtracking
   acotado probando versiones más viejas del dependiente.
6. **Opcionales y embebidas**: `optional` se sugiere, nunca se agrega solo; `embedded` no se
   descarga y queda como `provista por <padre>`.
7. **Incompatibles**: por cada versión elegida y cada dependencia `incompatible`, si el destino está
   en el set, reportar `incompatible(A, B)`; los reemplazos pasan por el mismo chequeo.
8. **Faltantes**: dependencia requerida sin candidato ni regla → `irresoluble(dep, pedida por)`.
9. **Lados**: `environment` → `env` de mrpack y `side` de packwiz; `client_or_server` se marca para
   que decida el admin.
10. **Entrada del lock**: `{project_id, version_id, archivo, url, sha1, sha512, tamaño, env, razón, pedido_por[]}`.
11. **Actualizar**: `POST /version_files/update` con los sha512 del lock y los canales permitidos; por
    cada hash que cambia, repetir 4–8 con la versión nueva (una actualización puede traer
    dependencias o incompatibilidades nuevas).
12. **Exportar**: `.mrpack` (rechazar URLs fuera de los cuatro dominios, no embeber) o packwiz.

Tarjetas: `absence-is-a-third-value` (`version_id` nulo no es "cualquier versión" ni "ninguna";
`environment` ausente no es "ambos"), `no-simultaneous-deploy` (el `environment` nuevo de Modrinth
convive con los campos viejos), `validate-each-transformation-run` (cada export se valida contra su
spec antes de servirse).
