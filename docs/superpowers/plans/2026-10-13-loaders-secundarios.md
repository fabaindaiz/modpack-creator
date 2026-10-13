# Loaders secundarios: plan

> **Para quien ejecuta:** cada tarea, test primero y visto fallar (un test de base, en rojo con
> `./scripts/test-mongo.sh` antes del cuerpo: primero la firma sola si mypy lo pide); `./scripts/check.sh
> && git commit`. Pasos con casillas.

**Objetivo:** que un pack use mods de otro loader a través de Connector o Kilt, prefiriendo lo nativo
y sin acciones peligrosas automáticas.

**Arquitectura:** una tabla pura de puentes; `Target` aprende los puentes activos: `loaders()` sigue nativo
y `loaders_with_bridges()` lo usan solo los usos que eligen (d-7985ee-6ebc03), `via()` distingue; las
versiones huésped en listas aparte, pedidas solo cuando hacen falta (d-7985ee-4fd88e); el resolvedor ordena nativo primero,
respeta la vía instalada y deja problemas en vez de agregar compañeros; el panel suma la opción,
pestañas, un diálogo y la pestaña «Vía puente».

**Stack:** FastAPI, Jinja2 + htmx, Tailwind, Beanie/MongoDB.

**Spec:** `docs/superpowers/specs/2026-10-13-loaders-secundarios-design.md` (C1–C9;
d-7985ee-45c2ba … d-7985ee-d7fcae). **Estado (2026-10-13):** escrito.

## Restricciones

- Ninguna acción peligrosa automática (d-7985ee-56a529): cambiar de loader un mod, agregar o quitar un
  puente o sus compañeros, bajar una versión. Las actualizaciones seguras sí.
- El piso (d-7985ee-6dc34c) sigue: nada baja; la vía instalada (nativa o puente) se conserva.
- Ids de Modrinth exactos de la spec §1: Connector `u58R1TMW`, Forgified Fabric API `Aqlf1Shp`,
  Launchpad `voWgQoWV` (solo 26.1.2), Kilt `t87XZEqv`, Fabric API `P7dR8mSH`, Fabric Language Kotlin
  `Ha28R6CL`.
- Textos visibles en español, los de la spec; Tailwind; nada inline; routers solo con servicios; un
  método de servicio que escribe suma su fila a «Acciones y roles».

## Review Focus

1. **Un pack sin puentes activos** se resuelve, busca, fija y exporta exactamente como hoy (mismas
   versiones, mismas requests). → test de regresión en la tarea 2.
2. **Desactivar el puente** con mods puenteados: problema «sin versión para NeoForge» con acciones,
   nunca un 500 ni un build que los pierda en silencio. → tarea 3.
3. **Un mod con jar compartido** (`loaders: [fabric, neoforge]`): nativo, sin etiqueta. → tarea 2.
4. **Cambiar la versión de Minecraft del pack** a una sin puente: la opción queda activa sin efecto y
   lo dice; nada se rompe. → tarea 4.
5. **El Fabric API normal** agregado a mano a un pack con Connector: `bridge_conflict` con su acción,
   nunca en el build junto a Forgified. → tarea 3.

## Tareas

| # | Qué | Dónde | Tests (fallan primero) |
|---|---|---|---|
| 1 | Tabla de puentes, `bridges_for`, `REPLACES`, compañeros por versión | `src/library/bridges.py` | `tests/test_bridges.py` |
| 2 | `Target.bridges`, `loaders()` con huésped, `native_loaders()`, `via()`; orden nativo primero; `LockEntry.via`; sugerencias solo nativas; regresión sin puentes | `src/library/resolution.py` | `tests/test_resolution_bridges.py` |
| 3 | Vía instalada y `native_available`; reemplazo de Fabric API; `bridge_missing` y `bridge_conflict` con sus acciones | `resolution.py`, `_problem.html` | mismos + web de problemas |
| 4 | `ModpackBundle.secondary_loaders`; `ModpackService.bridges_set` (auditado, rol editor); `_request` pasa los puentes; `/v1` PATCH; casilla en Canales | `src/model/modpack/project.py`, `services/modpack.py`, `routers/web/modpacks.py`, `routers/v1/modpack.py`, `_channels.html`, `docs/decisions.md` (roles) | servicio (MongoDB), web, `/v1`, `test_routes_roles` |
| 5 | Búsqueda con pestañas «Nativos» / «Vía Connector (n)» | `routers/web/modpacks.py` (search), `_search.html` | web |
| 6 | Agregar un mod puenteado: diálogo con lo que falta y «Agregar todo» | `ModpackService.bridge_companions_missing`, ruta de agregar, `_bridge_dialog.html` | servicio + web |
| 7 | Lista: etiqueta «vía …», «no preferible», «servidor: probar antes»; pestaña «Vía puente (n)» con estados y «Pasar a nativa»; filtro «Loader»; aviso al quitar el último | `ModRow`, `mod_filters.py`, `_mods.html`, `_row_selects.html` | `test_mod_list`, `test_mod_filters`, web |
| 8 | Tablero: «ya tiene versión nativa»; vista previa de la simulación con los puenteados; exportación con solo el loader principal | `attention.py`, `home.py`, `_preview_body.html`, `mrpack` | `test_attention`, web, export |
| 9 | Barrido (un pack con Connector en la siembra), filas «Se cumple en», roadmap, registro, revisión final | `scripts/visual_sweep.py`, `docs/` | barrido |

### Tarea 1

- `@dataclass(frozen=True) class Bridge: key: str; label: str; host: str; guest: str;
  game_versions: frozenset[str]; mods: tuple[tuple[str, str], ...]  # (project_id, channel)`;
  `experimental: bool`; `extra: dict[str, tuple[tuple[str, str], ...]]` (mods por versión: Launchpad
  en 26.1.2).
- `BRIDGES: tuple[Bridge, ...]` con las tres filas de la spec §1; `bridges_for(loader, game_version) ->
  list[Bridge]`; `bridge(key, loader, game_version) -> Optional[Bridge]`;
  `companions(bridge, game_version) -> list[tuple[str, str]]`; `REPLACES = {"connector": {"P7dR8mSH":
  "Aqlf1Shp"}}`.
- Tests: `bridges_for("neoforge", "1.21.1")` es Connector; `("forge", "1.21.1")` vacío; `("forge",
  "1.20.1")` Connector; `("fabric", "1.21.1")` Kilt experimental; `companions(connector, "26.1.2")`
  incluye Launchpad y `"1.21.1"` no; `("quilt", …)` vacío.

### Tarea 2

- `Target.bridges: frozenset[str] = frozenset()`; `loaders(kind)` igual que hoy;
  `loaders_with_bridges(kind)` = nativos ∪ huéspedes de los puentes activos que cubren `game_version`
  (solo `kind == "mod"`); `via(candidate, kind) -> Optional[str]` = `None` si toca un nativo, si no la
  clave del puente. Revisar los 8 usos de `loaders()` (`grep -n "\.loaders(" src`): resolución
  (`fits`), catálogo y fijación de la fila usan `loaders_with_bridges`; cada uno con su test.
- Catálogo (d-7985ee-4fd88e): la lista huésped se guarda con su propio `target` (`1.21.1|fabric`) y se
  pide solo para los proyectos sin versión nativa que encaje (y para la búsqueda puenteada).
- `_preference` pasa a `(via is not None, canal, -fecha)` para mods; `LockEntry.via: Optional[str] =
  None` en `_take`.
- Una sugerencia solo se ofrece si el proyecto tiene una versión nativa que encaje.
- Tests: con el puente, `fits` acepta una versión `fabric`; una nativa más vieja gana a una puenteada
  más nueva; jar compartido es nativo (`via is None`); `LockEntry.via == "connector"`; sugerencia
  puenteada no aparece; sin puentes, el lock de un caso conocido es idéntico al de antes (Review 1, 3).

### Tarea 3

- La vía instalada (la del piso) es la única que la resolución usa para ese mod (d-7985ee-7e602f): si
  es puenteada y hay una nativa con fecha ≥ la del piso, `Lock.native_available[project_id] =
  version_id`; si es más vieja, `Lock.native_behind[project_id] = version_id`. `native_only` del pack
  (tarea 7) fuerza la vía nativa. Si en la vía instalada no queda versión, problema `via_change` con
  la acción que abre la confirmación.
- Dependencias: la de un mod nativo que solo existe por el puente es `bridge_dependency` con «Usar vía
  Connector» (d-7985ee-b921b2); las de un mod puenteado entran por el puente (d-7985ee-b7bddf).
- Dependencias: con Connector, `P7dR8mSH` se resuelve como `Aqlf1Shp`.
- Problemas: `bridge_missing` (hay un mod con `via` y falta un compañero de `companions(...)`, acción
  «Agregar el puente» = el diálogo de la tarea 6), `bridge_conflict` (el pack tiene `P7dR8mSH` y
  Connector activo; acción «Quitar Fabric API»), `unresolvable` con «reactivar el puente» cuando el
  mod solo existe por un puente inactivo (Review 2).
- Tests de cada caso, y el de Review 5.

### Tarea 4

- `ModpackBundle.secondary_loaders: list[str] = []`; `ModpackBundlePatch.secondary_loaders:
  Optional[list[str]]`; `ModpackService.bridges_set(bundle_id, keys, actor)` valida contra
  `bridges_for` (422 si no aplica), escribe atómico y audita `bundle.bridges`; fila en «Acciones y
  roles» (editor) y acción en `audit_labels.ACTIONS` («Cambió los loaders secundarios»).
- `_request` arma `Target(bridges=…)` solo con las claves que `bridges_for` acepta para la versión
  actual (Review 4: el resto queda guardado y la casilla dice «sin efecto en {versión}»).
- Ruta `POST /modpacks/{id}/bridges` (checkboxes), responde `#pack-main` como Canales.
- Tests: servicio con MongoDB (escribe, audita, 422); web (casilla solo si aplica, «experimental» en
  Kilt, «sin efecto»); `/v1` PATCH; roles.

### Tarea 5

- `GET /modpacks/{id}/search?q=&via=connector`: con `via`, busca con el loader huésped y marca cada
  resultado «vía Connector (Fabric)»; sin `via`, como hoy. Pestañas en `_search.html` solo con un
  puente activo; el conteo de la pestaña puenteada sale de su búsqueda (una request más, solo al
  abrirla: `hx-get` al tocar la pestaña).
- Tests web: pestañas solo con puente; la puenteada pide `loader=fabric`; etiqueta; sin puente, igual
  que hoy.

### Tarea 6

- `ModpackService.bridge_companions_missing(bundle_id, project_id) -> list[tuple[str, str]]`
  (proyecto y título) para un proyecto que solo encaja por un puente activo.
- La ruta de agregar responde el diálogo `#bridge-dialog` con esa lista y «Agregar todo» (`POST
  …/projects` con `ref` múltiple y `confirm_bridge=1`); sin faltantes, como hoy.
- Tests: servicio; web (diálogo con Connector y Forgified; «Agregar todo» agrega los tres; cancelar no
  agrega nada).

### Tarea 7

- `ModRow.via: Optional[str]`, `ModRow.native: Optional[str]` («otro canal» | «alcanza» | «detrás»),
  `ModRow.server_risky: bool`.
- Pestaña `vista=puente` (cuenta los `via`), con cada fila, su estado y, si «alcanza», «Pasar a
  nativa»: abre el diálogo de cambio de vía (d-7985ee-7e602f); «Pasar igual» llama a
  `ModpackService.via_set(bundle_id, project_id, native=True, actor, confirm=True)`, que guarda
  `ModpackBundle.native_only` (auditado, rol editor, fila de roles); sin `confirm` es un
  `ViaChangeRefused` (409) que describe el cambio, como `DowngradeRefused`. `/v1`: `confirm_via=true`.
  Quitar la marca (volver a Connector) pide la misma confirmación.
- `ModFilters.loader`: `nativos` | `puente`.
- Al quitar el último mod puenteado: aviso con «Quitar Connector y Forgified Fabric API» (no lo hace).
- Tests de cada uno.

### Tarea 8

- `attention`: `PackFact.native_ready: int` → «{pack}: {n} mods ya tienen versión nativa» → «Ver»
  (`?vista=puente`).
- Vista previa de simulación: «{n} mods van por Connector: si el servidor no arranca, revisa sus
  logs».
- Exportación: `dependencies` del `.mrpack` sin el loader huésped (test).

### Tarea 9

- Siembra del barrido: un pack NeoForge 1.21.1 con Connector activo y un mod puenteado (el doble de
  Modrinth responde versiones `fabric`); capturas de la búsqueda con pestañas, el diálogo y la pestaña
  «Vía puente». Documentos y revisión final.

## Lo que no cubre

Modrinth real (las versiones de Connector y sus dependencias pueden cambiar: la tabla es a mano); si un
mod concreto corre de verdad por Connector (eso solo se sabe arrancándolo).
