# Panel web: acciones para cada problema, ficha local de mod y pestañas de pendientes

El dueño aprobó este diseño el 2026-10-05 en el chat. Se apoya en
`2026-10-04-panel-usabilidad-design.md`; donde la contradice, manda esta.

## Qué se quiere (pedido del dueño, 2026-10-05)

1. Para cada problema del modpack, al resolver y al crear un build, sugerir una acción:
   - sin versión estable pero con beta o alpha → sugerir usarla, o quitar el mod;
   - sin ninguna versión → sugerir quitarlo.
2. Cada mod que está en la base (porque se agregó o porque un pack lo usa) tiene una **ficha local**,
   y **cualquier nombre de mod** lleva a ella.
3. La lista de decisiones hoy muestra solo el nombre. Cada mod tiene que ser identificable y verse
   con su ficha. Como eso alarga la pestaña, la vista de mods tiene **una forma interna de ver lo
   pendiente**, separada del panel de revisión del pack.

**Decisiones del dueño (2026-10-05):**
- sin release y con beta o alpha → **fijar la beta (o alpha) más nueva** solo para ese mod (con
  los pins que ya existen), además de "Quitar del modpack";
- la ficha local es **una página propia y también un panel lateral**;
- lo pendiente va en **pestañas internas de Mods**.

**Decisiones del dueño en la revisión por temas (2026-10-05):**
- **Problemas:**
  - la alternativa más estable (beta antes que alpha);
  - tras una acción en el diálogo se recalcula la lista y **el build lo crea él**;
  - una dependencia sin versiones ofrece "Quitar *el que la pide*", "Crear un reemplazo" e
    **"Ignorar esta dependencia"** (reemplazo vacío global, con confirmación).
- **Ficha:**
  - las versiones salen del catálogo, con "Cargar todas desde Modrinth";
  - en "dónde se usa", el build recomendado y el último por pack.
- **Panel:**
  - a la derecha;
  - dentro del panel, un clic reemplaza el contenido y deja "← volver";
  - **ningún mod sin ficha**: los que falten se cargan apenas se puede, en lote, y bajo pedido
    junto con sus relacionados.
- **Pestañas:**
  - empieza en "Todos", con contadores destacados;
  - atajos que marcan sin guardar.

**Éxito:**
- todo problema que bloquea un build se muestra con al menos una acción que lo resuelve en un
  clic, sin ids opacos;
- todo nombre de un mod que está en la base abre su ficha, en un panel lateral o con Ctrl/clic
  central en otra pestaña;
- "Por decidir" y "Con problemas" se ven aparte de la lista completa, con la ficha de cada mod;
- el gate pasa y el barrido visual termina sin problemas.

## 1. Problemas con datos y acciones

**Datos.** `Problem` (`src/library/resolution.py`) suma campos opcionales, todos con default:
- `requested_by: list[str] = []`: los proyectos (ids) que pidieron el mod. Vacío si lo pidió el
  admin o el pack.
- `alternative: Optional[Alternative] = None`, con `version_id`, `version_number`, `channel`
  (`beta` o `alpha`) y `published`. Es la mejor versión que **sirve** para la versión de MC y el
  loader (`Target.fits`) pero no está en los canales del pack. Se elige con el mismo orden que la
  resolución: canal más estable primero, después la más nueva.
- `conflict_with: Optional[str] = None`: en `incompatible`, el otro proyecto.

`detail` se mantiene (texto en español) para `/v1` y para las resoluciones guardadas antes de este
cambio. El panel, en cambio, arma su texto desde los campos estructurados y los títulos, y solo
usa `detail` si faltan los campos.

**Cuándo se llena cada campo:**
- en el `unresolvable` "sin versión … en los canales …": `alternative` cuando
  `[c for c in by_project[p] if target.fits(c, tipo)]` no está vacío (esas versiones ya están en el
  catálogo de la resolución: `project_versions` las baja todas, de todos los canales);
- `requested_by` en todos los `Problem` con un solicitante distinto de `ADMIN`;
- `conflict_with` en `incompatible`, donde `project_id` es el objetivo y `conflict_with`, el padre.

**Acción por tipo** (todas usan rutas que ya existen; ninguna escritura nueva):

| Problema | Acciones |
|---|---|
| `unresolvable` con `alternative` | **"Usar la {canal} {número}"** (`POST /modpacks/{id}/pins` con esa versión) · "Quitar del modpack" |
| `unresolvable` sin alternativa, mod del pack | **"Quitar del modpack"** (`DELETE …/projects/{p}`) |
| `unresolvable` sin alternativa, dependencia (`requested_by` no vacío) | "Quitar *{título del que la pide}*" (por cada solicitante que esté en el pack) · "Crear un reemplazo" (enlace a `/replacements`) · **"Ignorar esta dependencia"** (`POST /replacements` con reemplazo vacío: la dependencia se descarta en **todos** los packs; pide confirmación con ese texto) |
| `unresolvable` por una versión fijada que ya no existe, `pin_mismatch` y `pin_conflict` | "Quitar la fijación" (`DELETE …/pins/{p}`) · "Fijar otra" (abre la ficha en el pack, con su selector) |
| `incompatible` | "Quitar *{A}*" · "Quitar *{B}*" (solo los que estén en el pack) |
| tipo no soportado, `no_file` | "Quitar del modpack" |

"Quitar del modpack" se ofrece para los mods del pack, sean "agregados" o "del .mrpack". El
servicio ya permite quitar ambos (`project_remove`); la ficha de la ronda 1 solo lo ofrecía para
los agregados, porque uno "del .mrpack" vuelve si se reemplaza el contenido del pack
(`pack_replace`). En un problema se ofrece igual, y en uno "del .mrpack" se agrega la nota "vuelve
si se reemplaza el contenido del pack; para que no vuelva, crea un reemplazo vacío", con un enlace
a `/replacements`.

**Al crear un build:** cuando `BuildBlocked` trae problemas (no solo pendientes), la ruta del panel
responde con el **mismo diálogo** de la ronda 1, que ahora lista primero los problemas con sus
acciones y después los mods por decidir. `/v1` sigue viendo el 409 de siempre.

**Después de una acción en el diálogo (decisión del dueño): se recalcula la lista; el build lo creas
tú.**
- Las acciones de un problema del diálogo mandan además `dialog=1`. La ruta aplica la acción (la
  misma de siempre), vuelve a **resolver** (`resolve`, no `build_create`) y responde el diálogo
  recalculado (`HX-Retarget: #build-dialog`).
- Cuando no queda nada pendiente ni con problemas, el diálogo dice "Todo resuelto" y muestra
  **"Crear build"** destacado. Ninguna acción crea un build por sí sola.
- "Decidir y crear build" de la ronda 1 se mantiene para las decisiones (decidir y construir en un
  paso, como ya lo aprobó el dueño). Las acciones de problemas nunca construyen.

**Alternativa beta o alpha (decisión del dueño):** se sugiere la más estable (beta antes que alpha;
dentro del canal, la más nueva). Una alpha solo aparece si no hay beta que sirva.

## 2. Ficha local de cada mod

**Ningún mod sin ficha (decisión del dueño): se cargan apenas se pueda, en lote.**
- Toda página o fragmento que muestra mods (lista, pestañas, problemas, sugerencias, resultados de
  búsqueda, candidatos de importar, comparación del servidor, bloques de cambios, actualizaciones)
  completa el catálogo con **una sola** llamada por lotes (`fill_catalog`, el tope de 5 s de la
  ronda 1) para **todos** los proyectos que muestra y que faltan. Esa llamada se hace una vez por
  render, juntando los ids de todas sus secciones.
- La búsqueda también: los ~10 resultados se completan juntos, así que cada resultado tiene ficha.
- Si al abrir `/mods/{id}` el proyecto todavía no está (Modrinth no respondió antes), la ficha lo
  pide en ese momento **junto con sus relacionados que falten** (los proyectos de las dependencias
  de sus versiones del catálogo), en una llamada. Solo si Modrinth no lo conoce, 404 con un aviso
  en español; si Modrinth no responde, un aviso con "Reintentar".
- Por eso `ui.mod_link` enlaza siempre a la ficha. Un nombre sin ficha solo queda como texto
  cuando su id no es válido.

**Página `GET /mods/{project_id}`** (editor, lectura; completa el catálogo como se dijo). Muestra:
- **identidad:** ícono (solo `cdn.modrinth.com`), título, autor si el catálogo lo tiene,
  descripción, categorías, tipo, el lado según Modrinth ("cliente o servidor", etc.), loaders,
  versiones de MC (con la del pack marcada cuando se llega desde un pack) y enlaces (Modrinth,
  código, issues);
- **dónde se usa:**
  - packs que lo tienen como proyecto (con su origen);
  - packs cuya resolución guardada lo trae como dependencia ("dependencia de *X*");
  - por cada pack, **el build recomendado y el último** que lo incluyen, con su versión
    ("#5 recomendado, 0.8.13 · #7 último, 0.8.14"; decisión del dueño). No es el historial
    completo;
  - servidores cuyo último escaneo lo identificó (con la versión).

  Cada uno enlaza a su página; el del pack, a la pestaña y la ficha de ese mod;
- **versiones conocidas** (decisión del dueño: el catálogo, más un botón para cargar todas):
  - al abrir, solo las del catálogo (`ModrinthVersion` del proyecto), con número, canal, fecha,
    versiones de MC y loaders, las más nuevas primero (tope de 50), y la nota "parcial: las que
    se bajaron para tus packs". Abrir la ficha nunca espera a Modrinth;
  - **"Cargar todas desde Modrinth"** (`hx-get` a `/mods/{id}/versiones`) pide todas las versiones
    del proyecto con `GET /project/{id}/version` **sin filtros**, las guarda en el catálogo y
    reemplaza la tabla. Tiene el tope de 5 s de la ronda 1; si Modrinth falla, aparece "Modrinth no
    respondió" y "Reintentar". El cliente hoy manda `loaders=[]` y `game_versions=[]`: cuando
    vienen vacíos se **omiten** los parámetros (contrastar con el openapi de Modrinth v2.7.0 y
    probarlo en `tests/test_api_contracts.py`). El método de servicio que lo hace escribe el
    catálogo, así que lleva su fila (editor) en la tabla de roles, como `projects_filled`.

Es solo lectura. Quitar, fijar o decidir se hace desde el pack: la ficha lleva a él. Los datos
salen de un método de servicio de lectura nuevo, `ModrinthService.mod_page(project_id) -> ModPage`
(o donde encaje, sin que un router pida una base). Las consultas "dónde se usa" son de lectura
sobre Mongo; si alguna necesita un índice, se agrega sin migración.

**Panel lateral (decisión del dueño: a la derecha):** `GET /mods/{project_id}/panel` devuelve la
misma ficha en versión compacta (sin la tabla de versiones; con "Abrir ficha completa").
- Ocupa el alto de la pantalla a la derecha (`w-[28rem]` en escritorio) y la pantalla completa en
  móvil; la lista queda visible detrás en escritorio.
- **Dentro del panel, un clic en otro mod reemplaza el contenido y aparece "← {mod anterior}"**
  para volver (decisión del dueño). Basta un nivel de historia en el propio fragmento: el enlace
  manda `?desde={id}` y el panel muestra el botón de volver a ese id.
- Hay un `<dialog id="mod-drawer">` en `base.html`.
- Un nombre de mod es `<a href="/mods/{id}" hx-get="/mods/{id}/panel" hx-target="#mod-drawer-body"
  hx-swap="innerHTML">`. `app.js` abre el diálogo (`showModal`) cuando el swap apunta a
  `#mod-drawer-body`, y lo cierra con Escape o con el botón Cerrar.
- **Ctrl, Cmd, Shift y el clic central siguen el `href`** (abren la página en otra pestaña). Si
  htmx no los respeta solo, `app.js` cancela la petición htmx en esos casos (`htmx:confirm` o
  `htmx:beforeRequest`). Se verifica en Chromium.

**Macro `ui.mod_link(project_id, title)`:** enlaza siempre a la ficha (ver "ningún mod sin ficha").
Se usa en todos los lugares donde aparece el nombre de un mod:
- la lista de mods, las pestañas y las decisiones;
- los problemas y sus acciones, y las sugerencias;
- la comparación del servidor, los bloques de cambios (vista previa, comparar builds, simulación),
  la página de actualizaciones y el resultado de importar;
- todos los resultados de búsqueda.

## 3. Pestañas internas en Mods

Arriba de la lista: **Todos (N) · Por decidir (N) · Con problemas (N)**. Los conteos salen de la
resolución guardada y vigente; si está vieja o no hay, las pestañas lo dicen ("resuelve para ver lo
pendiente").
- Son enlaces `?vista=todos|decidir|problemas` con `hx-get` a la página del pack, `hx-select="#mods"`,
  `hx-target="#mods"` y `hx-push-url="true"`. Sin JS propio, y la URL de cada pestaña se puede
  compartir.
- **Por decidir:** cada mod con su ficha resumida (ícono, título enlazado, descripción en una
  línea, "Modrinth dice: {lado}", "dependencia de *X*", la versión), sus botones **En el servidor /
  Solo cliente** y, al pie, "Guardar las N decisiones" (el bulk de hoy). Reemplaza el bloque
  "Decidir varios" que hoy va arriba de la lista.
- **Con problemas:** cada problema como una tarjeta con la ficha resumida del mod, el texto del
  problema con títulos y sus acciones (§1).
- **Todos:** la lista de la ronda 1.

**Pestaña inicial (decisión del dueño):** siempre "Todos". Si hay pendientes o problemas, sus
pestañas llevan el contador en color (ámbar para por decidir, rojo para problemas) y el panel del
pack lo dice. La vista nunca cambia sola.

**Atajos en "Por decidir" (decisión del dueño):** "Todos al servidor" y "Todos solo cliente"
**marcan** todos los selectores del formulario, pero **no guardan**; se guarda con "Guardar las N
decisiones". Lo hace `app.js` con un atributo genérico (`data-set-all="si|no"` sobre el botón, que
marca los `select` de su formulario), sin código inline y sin petición.

El panel de revisión del pack (estado de la resolución, sugerencias, builds) deja de listar
pendientes y problemas: muestra "3 por decidir · 2 con problemas" con enlaces a la pestaña.

## Seguridad y datos

- Las páginas y fragmentos que muestran mods escriben el **catálogo** (datos de Modrinth, como ya
  lo hacen la lista de mods y la comparación del servidor). Es la misma excepción aceptada en la
  ronda 1 para un GET que llena una caché. No escriben nada del pack ni de un servidor.
- No hay escrituras nuevas del pack: las acciones usan `pin_set`, `pin_unset`, `project_remove`,
  `decisions_set` y `replacement_set` ("Ignorar esta dependencia"), que ya están en la tabla de
  roles. Las rutas nuevas (`/mods/{id}` y
  `/mods/{id}/panel`) son de lectura y de editor. Los barridos de roles y de autenticación las
  cubren solos.
- `project_id` en la URL se valida con `^[A-Za-z0-9_-]+$` (si no, 404).
- Todo texto de Modrinth se escapa; los íconos, solo de `cdn.modrinth.com`; los enlaces, solo
  `https`.
- Los campos nuevos de `Problem` son opcionales, así que las resoluciones guardadas y los builds
  viejos los leen vacíos. No hay migración.

## Tests

- **Resolución:**
  - un mod con solo una beta que sirve → `alternative` con esa beta (y con la más estable si hay
    beta y alpha);
  - sin ninguna versión → sin `alternative`;
  - `requested_by` en una dependencia; `conflict_with` en `incompatible`;
  - una resolución vieja sin los campos se lee.
- **Panel:**
  - cada tipo de problema muestra sus acciones (incluida "Usar la beta …" con el `version_id`
    correcto) y textos con títulos, no ids;
  - el diálogo de build bloqueado por problemas lista problemas y pendientes;
  - las pestañas con sus conteos y su contenido, `?vista=` inválida → "todos";
  - la ficha `/mods/{id}` (404 fuera del catálogo y con un id inválido), "dónde se usa" con
    packs, dependencia, build y servidor, y las versiones;
  - el panel compacto;
  - `ui.mod_link` con y sin el proyecto en el catálogo;
  - las cadenas de Modrinth escapadas.
- **Barrido visual (Chromium):**
  - un clic abre el panel lateral;
  - Ctrl o el clic central abren la página (no el panel);
  - Escape cierra el panel;
  - las pestañas cambian sin recargar y la URL cambia;
  - capturas de la ficha, el panel y las pestañas.

## Fuera de alcance

- Editar un mod desde su ficha.
- Sugerir otra versión de Minecraft.
- Un canal propio por mod.
- Una página de catálogo o de búsqueda de mods fuera de un pack.
