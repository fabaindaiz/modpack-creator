# Loaders secundarios: mods de otro loader mediante un puente

**Fecha:** 2026-10-13 · **Origen:** observación 7 del dueño sobre la 0.3.1 (i-7985ee-44060c, tanda C)
· **Investigación:** `docs/research/2026-10-13-loaders-secundarios.md` (con su verificación contra la
API de Modrinth) · **Decisiones:** del dueño, 2026-10-13, en revisión interactiva (C1–C9, filas
d-7985ee-45c2ba … d-7985ee-d7fcae en `docs/decisions.md`).

## Qué se busca

Que un pack de NeoForge (o Forge 1.20.1) pueda llevar un mod que solo existe para Fabric, y un pack de
Fabric uno que solo existe para Forge, a través de un mod puente, prefiriendo siempre lo nativo y sin
que nada peligroso ocurra solo.

**Se sabe que funciona cuando:** con el puente activo, un mod solo de Fabric se agrega a un pack de
NeoForge confirmando lo que entra con él, y el build trae el mod, Connector y Forgified Fabric API (y
Launchpad en 26.1.2), nunca el Fabric API normal; un mod con versión nativa usa la nativa; un mod
puenteado que recibe una nativa que alcanza a la instalada aparece sugerido para pasar, sin cambiar
solo.

## Decisiones del dueño (2026-10-13)

| | Decisión |
|---|---|
| C1 | Puentes: Connector (mods de Fabric en NeoForge; en Forge solo 1.20.1) y Kilt (mods de Forge en Fabric), este marcado «experimental». |
| C2 | Se activa con una opción del pack, «Loaders secundarios», que solo aparece cuando el loader y la versión del pack tienen un puente. Sin activarla, nada cambia. |
| C3 | Siempre la versión nativa si hay una en los canales del pack; la del puente solo cuando no la hay. Un mod es uno solo: un jar etiquetado con los dos loaders es nativo. |
| C4 | Agregar el primer mod puenteado abre una confirmación con lo que entra con él (el puente, sus compañeros, el reemplazo de Fabric API) y «Agregar todo». Quitar el último mod puenteado sugiere quitar el puente, no lo hace. |
| C5 | Etiqueta «vía Connector (Fabric)» / «vía Kilt (Forge) · experimental» en la búsqueda y en la fila; «no preferible» cuando Modrinth tiene una versión nativa del mismo mod en otro canal o versión. Las sugerencias, solo nativas. |
| C6 | Un mod puenteado que va al servidor se marca «servidor: probar antes»; la vista previa de la simulación lista aparte los mods puenteados. |
| C7 | Cuando un mod puenteado recibe una versión nativa publicada en o después de la instalada (build vigente o servidores), se **sugiere** «Pasar a nativa» (pestaña «Vía puente» y tablero); nunca se cambia solo. |
| C8 | Búsqueda con pestañas «Nativos» (por defecto) y «Vía Connector (n)»; lista de mods con una pestaña «Vía puente (n)» que dice el estado de cada uno; filtro «Loader» en Todos. |
| C9 | Regla general del panel: una acción peligrosa (cambiar de loader un mod, agregar o quitar un puente, bajar una versión) se sugiere, nunca es automática; una actualización segura (más nueva, misma vía, en los canales, sobre el piso) sí. |

**Del agente:** la tabla de puentes en el código, con tests (d-7985ee-171417); una dependencia de un mod
puenteado que no tiene versión en ninguna vía es un problema con su acción, el resolvedor no adivina
otro reemplazo «forgified» (d-7985ee-d7fcae); el `.mrpack` declara solo el loader principal y el
puente viaja como un mod más; la opción del pack se edita también por `/v1` y queda en la auditoría.

## 1. La tabla de puentes (`src/library/bridges.py`, pura)

`Bridge(key, label, host_loader, guest_loader, game_versions, mods, experimental)`:

| key | host | guest | versiones | mods (Modrinth id, canal aceptado) |
|---|---|---|---|---|
| `connector` | neoforge | fabric | 1.21, 1.21.1, 26.1.2 | Connector `u58R1TMW` (beta), Forgified Fabric API `Aqlf1Shp`; Launchpad `voWgQoWV` solo 26.1.2 |
| `connector` | forge | fabric | 1.20.1 | Connector `u58R1TMW` (beta), Forgified Fabric API `Aqlf1Shp` |
| `kilt` | fabric | forge | 1.20.1, 1.21.1 | Kilt `t87XZEqv`, Fabric API `P7dR8mSH`, Fabric Language Kotlin `Ha28R6CL` |

`REPLACES = {"connector": {"P7dR8mSH": "Aqlf1Shp"}}`: con Connector, una dependencia de Fabric API se
resuelve a Forgified Fabric API, y Fabric API nunca entra. `bridges_for(loader, game_version)` dice qué
puentes ofrece un pack. Una versión nueva de Minecraft se agrega a la tabla a mano, tras mirar la
lista de versiones del puente en Modrinth (la verificación mostró que el proyecto no lo dice por versión).

## 2. El modelo

- `ModpackBundle.secondary_loaders: list[str]` (claves de puente activas; vacío por defecto: nada
  cambia). `ModpackBundlePatch` lo acepta; `/v1` igual que el resto del pack.
- `Target.bridges: frozenset[str]`. `Target.loaders(kind)` sigue siendo el nativo;
  `Target.via(candidate, kind) -> Optional[str]`: `None` si es nativo, la clave del puente si solo
  corre por uno activo.
- `LockEntry.via: Optional[str]` (qué puente usa; `None` nativo), para la fila, la vista previa y el
  servidor.

## 3. El resolvedor

- `fits` acepta una versión nativa o una del loader huésped de un puente activo, en sus versiones de
  Minecraft.
- **Orden (C3):** primero nativo, después canal más estable, después la más nueva. Una versión
  puenteada solo gana si el mod no tiene ninguna nativa que encaje.
- **Piso (d-7985ee-6dc34c) y C7:** si la versión en el build o en un servidor es puenteada, la
  resolución se queda en la vía puenteada (actualizaciones seguras por esa vía) aunque aparezca una
  nativa; anota `native_available` en el lock con la nativa y si ya alcanzó a la instalada. Si la
  instalada es nativa, nunca se elige una puenteada.
- **Compañeros:** el resolvedor no los agrega solo (C4, C9): si hay un mod puenteado y faltan, es un
  problema `bridge_missing` con la acción «Agregar el puente» (lo mismo que la confirmación).
- **Reemplazo:** con Connector, la dependencia `P7dR8mSH` se resuelve a `Aqlf1Shp`; si el pack tiene
  Fabric API como proyecto, es un problema `bridge_conflict` («Fabric API no convive con Forgified
  Fabric API») con la acción de quitarlo.

## 4. El panel

- **Canales:** «Loaders secundarios» con una casilla por puente que ofrece `bridges_for` (texto: qué
  permite y qué agrega; Kilt dice «experimental»). Guardarla resuelve de nuevo.
- **Búsqueda:** con un puente activo, dos pestañas: «Nativos» (por defecto, como hoy) y «Vía Connector
  (n)» / «Vía Kilt (n)»: la búsqueda de Modrinth con el loader huésped. Cada resultado puenteado lleva
  su etiqueta.
- **Agregar un mod puenteado (C4):** si faltan el puente o sus compañeros, la respuesta es un diálogo
  con lo que entra y «Agregar todo» (agrega los proyectos, origen `admin`, y resuelve). Si ya están,
  entra como cualquier mod.
- **Lista de mods:** etiqueta «vía Connector (Fabric)» junto a la versión; «no preferible» (C5);
  «servidor: probar antes» si va al servidor (C6). Pestaña «Vía puente (n)»: cada mod puenteado con
  su estado (`sin versión nativa` · `hay nativa en otro canal` · `hay nativa {n}: se puede pasar` con
  el botón «Pasar a nativa», que fija la nativa como una acción del admin). Filtro «Loader»: nativos /
  vía puente. Quitar el último mod puenteado deja un aviso «Ya no hay mods por el puente: puedes quitar
  Connector y Forgified Fabric API» con el botón.
- **Tablero:** «Pack X: ModY ya tiene versión nativa» → «Ver» (la pestaña «Vía puente»).
- **Simulación (C6):** la vista previa lista «N mods van por Connector: si el servidor no arranca,
  revisa sus logs».

## 5. Exportar

El `.mrpack` declara solo el loader principal; el puente y los compañeros van como mods en `files`.

## Errores

- Una versión de Minecraft sin puente: la opción no aparece; un pack que la tenía activa y cambia de
  versión la conserva sin efecto y lo dice.
- Modrinth caído al buscar en la pestaña puenteada: el aviso de siempre.
- Un mod puenteado cuyo puente se desactivó: el mod queda como problema «sin versión para NeoForge»
  con la acción de reactivar el puente o quitarlo.

## Tests (antes que el código)

Tabla y `bridges_for`; `Target.via` y el orden nativo primero; el piso con una vía puenteada y la
sugerencia de C7; `bridge_missing` y `bridge_conflict`; el reemplazo de Fabric API; la opción del pack
(web y `/v1`, auditada); pestañas de búsqueda; diálogo de agregar; pestaña «Vía puente» con sus
estados y «Pasar a nativa»; filtro «Loader»; tablero; vista previa de la simulación; exportación.

## Fuera de esta tanda

Detectar incompatibilidades de un mod con Connector (Modrinth no las publica); el parche de mixins de
servidor (no está en Modrinth); un pack con dos puentes a la vez (ningún loader tiene dos).
