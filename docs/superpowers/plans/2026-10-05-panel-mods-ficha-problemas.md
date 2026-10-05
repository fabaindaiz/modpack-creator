# Panel web: acciones para problemas, ficha local de mod y pestañas, plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usar superpowers:subagent-driven-development (recomendada)
> o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas
> (`- [ ]`) para seguir el avance.

**Objetivo:**
- cada problema de un pack llega con una acción que lo resuelve;
- cada mod tiene una ficha local, en página propia y en panel lateral, y su nombre es clicable en
  todo el panel;
- lo pendiente se ve en pestañas internas de Mods.

**Arquitectura:**
- `Problem` suma datos estructurados (`requested_by`, `alternative`, `conflict_with`) que la
  resolución ya conoce.
- La ficha sale de un método de lectura de `ModpackService` (`mod_page`), que junta catálogo,
  packs, resoluciones, builds y escaneos.
- Todo render que muestra mods completa el catálogo con una sola llamada por lotes
  (`fill_catalog`).
- El panel suma un router `mods`, un `<dialog>` lateral en `base.html`, el macro `ui.mod_link`, las
  pestañas `?vista=` y tarjetas de problema con acciones sobre rutas que ya existen.

**Tecnologías:** las de la rama: FastAPI, Jinja2, htmx 2.0.11, Tailwind 4.3.3 y Beanie 2. Sin
dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-05-panel-mods-ficha-problemas-design.md`. Hay que leerla
junto con este plan, y manda sobre él. Viene de la ronda 1:
`docs/superpowers/specs/2026-10-04-panel-usabilidad-design.md`.

## Restricciones globales

- **Lenguaje y capas:**
  - Python 3.12;
  - los routers solo inyectan servicios (`*Service`);
  - nada de defaults mutables;
  - todo `Document` va registrado (este lote no agrega ninguno).
- **Tests:** se escriben primero y se ven fallar (guardar la corrida en rojo en el reporte).
- **CSP:** sin `<script>` inline, `style=` ni `hx-on`. El comportamiento va por atributos htmx y
  `src/web/static/app.js`. `htmx-config` mantiene `attributesToSettle` sin `style`.
- **Commits:** el dueño es el **único autor**, sin `Co-Authored-By` ni otra atribución. Conventional
  Commits. Cada commit va encadenado: `./scripts/check.sh && git commit …`.
- **Nunca** leer ni editar `.env.yaml` ni `.env.*`.
- **Gate:** `./scripts/test-mongo.sh` (`docker` = podman). La VM tiene 2 GiB: no correrlo con otra
  instancia de exploración arriba. El deploy local del dueño (`modpack-api`, `modpack-mongodb`,
  `modpack-mongoweb`) no se toca.
- **Tabla de roles:** un método de servicio nuevo que escribe (también el catálogo) agrega su fila
  en `## Acciones y roles` de `docs/decisions.md` y, si no audita, su entrada con motivo en
  `NOT_AUDITED` de `tests/test_routes_roles.py`, en el mismo commit.
- **Modrinth:** toda llamada nueva o cambiada se contrasta con el openapi v2.7.0 y se prueba en
  `tests/test_api_contracts.py`.
- **Textos:** el que ve la persona va en español. Las cadenas de Modrinth se escapan. Los íconos
  solo se usan si pasan `_icon` (https, `cdn.modrinth.com`); los enlaces externos, solo si pasan
  `_https_url`.
- **Una llamada a Modrinth por render:** toda página o fragmento junta los ids de todas sus
  secciones y llama a `fill_catalog` (o a `projects_filled`) una sola vez, con el tope de 5 s.
- **Shell:** comandos sin esperar stdin, y archivos editados con herramientas de edición. Todo
  proceso o contenedor que una tarea arranca, lo detiene.

## Foco de revisión

1. **Ctrl, Cmd, Shift y el clic central en un nombre de mod abren la página `/mods/{id}`**, nunca
   el panel; un clic simple abre el panel; Escape lo cierra. Se prueba en Chromium (Tarea 8).
2. **`/mods/{id}` con un id inválido, con un mod que Modrinth no conoce o con Modrinth caído**: 404
   con aviso en español, o un aviso con "Reintentar". Nunca un 500. Tests en la Tarea 3.
3. **"Ignorar esta dependencia"** crea un reemplazo **global**: lleva `hx-confirm` con "se descarta
   en todos los packs", va por `replacement_set` (editor, auditado) y no aparece para un mod del
   pack (solo para una dependencia). Test en la Tarea 6.
4. **Una resolución guardada antes de este lote** (sin `requested_by`, `alternative` ni
   `conflict_with`) se ve con su `detail` de siempre y sin acciones que dependan de esos campos.
   Nunca falla la plantilla. Test en la Tarea 6.
5. **Una acción de problema nunca crea un build**: en el diálogo, después de "Usar la beta" o de
   "Quitar", se resuelve de nuevo y el diálogo dice qué queda; si no queda nada, muestra "Crear
   build" destacado. Test en la Tarea 7.

---

### Tarea 1: Problemas con datos estructurados

**Archivos:**
- Modificar: `src/library/resolution.py` (`Problem`, `Alternative`, el resolvedor)
- Tests: `tests/test_resolution.py`

**Interfaces:**
- Produce:
  - `Alternative(BaseModel)` con `version_id: str`, `version_number: str`, `channel: str` y
    `published: datetime`.
  - `Problem` suma `requested_by: list[str] = []`, `alternative: Optional[Alternative] = None` y
    `conflict_with: Optional[str] = None`. `kind`, `project_id` y `detail` no cambian.

- [ ] **Paso 1: tests que fallan**

```python
def test_only_a_beta_fits_offers_it_as_the_alternative() -> None:
    # channels {release}; project P has 1.0-beta (beta, fits 1.21.1+fabric) and 0.9 (release, other MC)
    [p] = resolve(request("P"), catalog).problems
    assert p.kind == "unresolvable" and p.alternative.version_id == "beta-1" and p.alternative.channel == "beta"

def test_beta_wins_over_a_newer_alpha() -> None:
    # beta 1.0-beta (2026-09-01) and alpha 1.1-alpha (2026-09-20), both fit
    assert problem.alternative.channel == "beta"

def test_no_fitting_version_has_no_alternative() -> None:
    assert problem.alternative is None

def test_a_dependency_problem_names_who_asked() -> None:
    # A (admin) requires D; D has no fitting version
    assert problem.project_id == "D" and problem.requested_by == ["A"]

def test_incompatible_names_both_sides() -> None:
    assert (problem.project_id, problem.conflict_with) == ("B", "A")

def test_a_problem_saved_before_these_fields_still_parses() -> None:
    assert Problem.model_validate({"kind": "unresolvable", "project_id": "P", "detail": "x"}).alternative is None
```

- [ ] **Paso 2:** verlos fallar con `hatch run tests -k "alternative or requested_by or conflict_with or saved_before"`.
- [ ] **Paso 3: implementar**
  - En el `unresolvable` "sin versión … en los canales …", `fitting = [c for c in by_project[p]
    if target.fits(c, tipo)]`. Si hay alguna, `alternative` es la mejor según `CHANNEL_RANK` y
    después por fecha más nueva (la clave `min` que ya usa la resolución).
  - `requested_by` es `[requester]` cuando `requester != ADMIN`.
  - En `incompatible`, `conflict_with = parent`.
  - Los demás `Problem` llevan `requested_by` cuando tienen solicitante.
  - Los textos de `detail` no cambian.
- [ ] **Paso 4:** pasan, y `./scripts/test-mongo.sh` también (`ModpackResolution` guarda `Lock`).
- [ ] **Paso 5: commit.** `feat: give each resolution problem who asked, an alternative and its conflict`

### Tarea 2: Todas las versiones de un proyecto desde Modrinth

**Archivos:**
- Modificar: `src/library/api/client/modrinth.py` (`project_versions`), `tests/test_api_contracts.py`
- Modificar: `docs/references.md` (lo que dice el openapi de los filtros vacíos)

**Interfaces:**
- Produce: `ModrinthAPI.project_versions(slug, loaders=(), game_versions=())`. Con `loaders` o
  `game_versions` vacíos, **omite** ese parámetro en la consulta, en lugar de mandar `[]`.

- [ ] **Paso 1: test de contrato que falla**

```python
async def test_project_versions_without_filters_sends_no_filter_params(...) -> None:
    await api.project_versions("sodium")
    assert "loaders" not in query and "game_versions" not in query and query["include_changelog"] == "false"

async def test_project_versions_with_filters_still_sends_them(...) -> None:
    await api.project_versions("sodium", ["fabric"], ["1.21.1"])
    assert query["loaders"] == '["fabric"]' and query["game_versions"] == '["1.21.1"]'
```

- [ ] **Paso 2:** verlo fallar.
- [ ] **Paso 3:** implementarlo, y anotar en `docs/references.md` lo que dice el openapi v2.7.0 de
  `GET /project/{id}/version` (`loaders` y `game_versions` son opcionales).
- [ ] **Paso 4:** pasan. Correr también los llamadores de `project_versions`
  (`hatch run tests -k "resolution or mod_list or versions"`).
- [ ] **Paso 5: commit.** `fix: ask Modrinth for every version of a project without empty filters`

### Tarea 3: La ficha de un mod (servicio y rutas de lectura)

**Archivos:**
- Modificar: `src/services/modpack.py` (`mod_page`, `mod_versions_all`, dataclasses `ModPage`,
  `ModUse` y `VersionRow`)
- Crear: `src/routers/web/mods.py`, registrado donde se registran los routers del panel
- Crear: `src/web/templates/mods/page.html`, `mods/_card.html` (identidad y "dónde se usa",
  compartido por la página y el panel), `mods/_panel.html` y `mods/_versions.html`
- Modificar: `docs/decisions.md` (filas editor de `ModpackService.mod_page` y
  `ModpackService.mod_versions_all`), `tests/test_routes_roles.py` (`NOT_AUDITED` con su motivo)
- Tests: `tests/test_mod_page.py` (servicio, con Mongo y un Modrinth falso que registra las
  llamadas), `tests/test_web_mods.py` (rutas)

**Interfaces:**
- Consume: `fill_catalog` y `FILL_DEADLINE` (`src/services/modrinth.py`), `_icon` y `_https_url`
  (de `src/routers/web/modpacks.py`; si hace falta, se mueven a un módulo común del panel sin
  cambiar su comportamiento) y `environment_label`.
- Produce:
  - `ModUse` (dataclass) con:
    - `bundle_id`, `bundle_name`;
    - `origin: Optional[str]` ("admin", "pack" o None);
    - `dependency_of: list[str]` (títulos);
    - `recommended: Optional[tuple[int, str]]` y `latest: Optional[tuple[int, str]]` (número de
      build y versión).
  - `ServerUse` (dataclass) con `server_id`, `server_name`, `version: Optional[str]` y
    `scanned_at`.
  - `VersionRow` (dataclass) con `version_id`, `version_number`, `channel`, `published`,
    `game_versions` y `loaders`.
  - `ModPage` (dataclass) con:
    - `project: ModrinthProject`;
    - `uses: list[ModUse]` y `servers: list[ServerUse]`;
    - `versions: list[VersionRow]` (del catálogo, tope 50, las más nuevas primero);
    - `versions_partial: bool`;
    - `titles: dict[str, str]`.
  - `ModpackService.mod_page(project_id: str) -> ModPage`:
    - 404 (`HTTP_404_NOT_FOUND` con texto en español) si Modrinth no conoce el proyecto;
    - `UpstreamError`/timeout → la excepción de siempre, que el panel muestra como aviso con
      "Reintentar".

    Si el proyecto falta en el catálogo, lo trae junto con los proyectos de las dependencias de
    sus versiones del catálogo que también falten, en una llamada.
  - `ModpackService.mod_versions_all(project_id: str) -> list[VersionRow]`: llama a
    `modrinthAPI.project_versions(project_id)` sin filtros, dentro de `asyncio.wait_for(…,
    FILL_DEADLINE)`, guarda con `version_upsert_many` y devuelve todas. Si Modrinth falla, la
    excepción sube y la ruta la muestra con "Reintentar".
  - Rutas (editor, lectura), con `project_id` validado por `MODRINTH_REF` (si no, 404):
    - `GET /mods/{project_id}` → `mods/page.html`;
    - `GET /mods/{project_id}/panel` → `mods/_panel.html`, con `?desde={id}` opcional para
      "← volver";
    - `GET /mods/{project_id}/versiones` → `mods/_versions.html`, con todas las versiones.

- [ ] **Paso 1: tests que fallan** (servicio, con Mongo)

```python
async def test_page_lists_packs_dependency_builds_and_servers() -> None:
    # P is admin in pack A; dependency of X in pack B's saved resolution; in A's builds #1 (rec, 0.8.13) and #2 (latest, 0.8.14);
    # identified in server S's latest scan at 0.8.13
    page = await service.mod_page("P")
    assert [(u.bundle_name, u.origin, u.dependency_of) for u in page.uses] == [("A", "admin", []), ("B", None, ["X title"])]
    assert page.uses[0].recommended == (1, "0.8.13") and page.uses[0].latest == (2, "0.8.14")
    assert [(s.server_name, s.version) for s in page.servers] == [("S", "0.8.13")]

async def test_a_project_missing_from_the_catalog_is_fetched_with_its_missing_relatives() -> None:
    assert api.calls == [("project_get_many", ["P", "D1"])]

async def test_unknown_to_modrinth_is_a_404() -> None: ...

async def test_all_versions_are_fetched_without_filters_and_stored() -> None:
    rows = await service.mod_versions_all("P")
    assert api.calls[-1] == ("project_versions", "P", (), ())
```

- [ ] **Paso 2:** tests de rutas que fallan (`tests/test_web_mods.py`, con dobles como en
  `tests/test_web_servers.py`):
  - la página muestra identidad, "dónde se usa" con enlaces y la tabla de versiones con
    "parcial";
  - el panel muestra la ficha compacta, "Abrir ficha completa" y, con `?desde=X`, "← {título de
    X}";
  - un id inválido (`/mods/a%20b`) da 404;
  - Modrinth caído da un aviso con "Reintentar" y nunca 500;
  - `/versiones` reemplaza la tabla;
  - las cadenas de Modrinth salen escapadas.
- [ ] **Paso 3:** verlos fallar.
- [ ] **Paso 4: implementar**
  - **Consultas de "dónde se usa"** (solo lectura): bundles con `projects.project_id == id`;
    `ModpackResolution` con una entrada de ese id sin origen; para cada pack involucrado, su build
    recomendado y su último build (`build_get`, mirando `lock.entries`); servidores
    (`server_find`) y su último escaneo (`scan_latest`) con ese `project_id` identificado.
  - **Índices:** si hace falta un índice para estas consultas, se declara en el `Document` (sin
    migración) y se dice en el reporte.
- [ ] **Paso 5:** pasan, con `./scripts/test-mongo.sh`.
- [ ] **Paso 6: commit.** `feat: a local page and panel for every mod`

### Tarea 4: El panel lateral y `ui.mod_link`

**Archivos:**
- Modificar: `src/web/templates/base.html` (`<dialog id="mod-drawer">` con `#mod-drawer-body` y
  Cerrar), `src/web/templates/_macros.html` (`mod_link`), `src/web/static/app.js`,
  `src/web/assets/app.css` si hace falta
- Tests: `tests/test_web_base.py`

**Interfaces:**
- Consume: las rutas de la Tarea 3.
- Produce:
  - `ui.mod_link(project_id, title, desde=None)` →
    `<a href="/mods/{id}" hx-get="/mods/{id}/panel{?desde}" hx-target="#mod-drawer-body"
    hx-swap="innerHTML" data-mod-link>{{ title }}</a>`. Si `project_id` no pasa `MODRINTH_REF`,
    el macro devuelve el título como texto.
  - `app.js`:
    - abre `#mod-drawer` con `showModal()` cuando un swap llega a `#mod-drawer-body`;
    - Cerrar y Escape lo cierran;
    - un clic con Ctrl, Cmd, Shift o el botón central en `[data-mod-link]` sigue el `href` sin
      petición htmx (cancelada en `htmx:confirm`, o donde corresponda en htmx 2.0.11; verificar en
      el código vendorizado).

- [ ] **Paso 1: tests que fallan** (HTML):
  - `mod_link` produce el `href` y el `hx-get` de arriba;
  - con `desde` agrega `?desde=`;
  - con un id inválido no hay enlace;
  - `base.html` tiene el diálogo vacío, con el botón Cerrar etiquetado;
  - `app.js` contiene el manejo de las teclas modificadoras y del botón central (marcadores; el
    comportamiento lo prueba la Tarea 8).
- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - El diálogo va a la derecha y a toda la altura (`ml-auto h-dvh w-full sm:w-[28rem]`, con
    backdrop) y ocupa la pantalla completa en móvil.
  - Su cuerpo tiene scroll propio.
  - El foco vuelve al enlace al cerrar.
- [ ] **Paso 4:** pasan.
- [ ] **Paso 5: commit.** `feat: open any mod's card in a side panel`

### Tarea 5: Cada nombre de mod, clicable, y ningún mod sin ficha

**Archivos:**
- Modificar estas plantillas, reemplazando cada nombre de mod por `ui.mod_link`:
  - `modpacks/_mods.html`, `_resolution.html`, `_decide_dialog.html`, `_search.html`,
    `_changes.html`, `updates.html`, `_replacements.html`;
  - `servers/_compare.html`, `_import.html`, `_new_modpack_done.html`, `_preview_body.html`;
  - las fichas `mods/_card.html` (dependencias y "dependencia de").
- Modificar: `src/routers/web/modpacks.py` (`_titles` usa `projects_filled`; la búsqueda completa
  los resultados en una llamada) y `src/routers/web/servers.py` (que siga habiendo una sola
  llamada por render).
- Tests: `tests/test_web_modpacks.py`, `tests/test_web_servers.py`, `tests/test_web_updates.py`

**Interfaces:**
- Consume: `ui.mod_link` (Tarea 4) y `ModrinthService.projects_filled`.

- [ ] **Paso 1: tests que fallan.** Un test por plantilla: el nombre del mod es un `a[href="/mods/{id}"]`
  con `hx-get` al panel. Y además:
  - la búsqueda de 10 resultados hace **una** llamada `projects_filled` con los ids de los 10;
  - la página de comparación de builds y la de actualizaciones también completan una vez;
  - los dobles cuentan las llamadas.
- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar.** Ningún render llama a `projects_filled` dos veces: si una ruta junta
  varias secciones, une los ids antes.
- [ ] **Paso 4:** pasan, junto con los barridos de roles y de autenticación.
- [ ] **Paso 5: commit.** `feat: make every mod name open its card`

### Tarea 6: Pestañas de Mods y tarjetas de problema con acciones

**Archivos:**
- Modificar:
  - `src/web/templates/modpacks/_mods.html` (pestañas y vistas), `_resolution.html` (solo
    contadores con enlace);
  - crear `modpacks/_problem.html` (una tarjeta, reutilizada por la pestaña y el diálogo) y
    `modpacks/_pending.html` (la vista "Por decidir");
  - `src/routers/web/modpacks.py`: `vista` en el detalle y en los fragmentos; `contexto` en las
    rutas de acción; `POST /replacements` acepta `bundle_id` y `contexto`;
  - `src/web/static/app.js` (`data-set-all`).
- Tests: `tests/test_web_modpacks.py`

**Interfaces:**
- Consume: `Problem` con sus campos (Tarea 1), `ui.mod_link` (Tarea 4) y `mod_list` / `ModList`
  (ronda 1).
- Produce:
  - `?vista=todos|decidir|problemas` en `GET /modpacks/{id}` (un valor inválido es `todos`).
    Las pestañas son `<a href="?vista=…" hx-get="?vista=…" hx-select="#mods" hx-target="#mods"
    hx-swap="outerHTML" hx-push-url="true">` con su contador; ámbar para decidir y rojo para
    problemas cuando son mayores que 0.
  - `_problem.html` recibe `problem`, `titles`, `pack` (ids → origen) y `contexto`
    ("problemas" | "dialogo"), y muestra las acciones de la tabla §1 de la spec:
    - "Usar la {canal} {número}": `POST …/pins` con `project_id` y `version_id = alternative.version_id`;
    - "Quitar del modpack" / "Quitar {A}": `DELETE …/projects/{id}`, con la nota y el enlace a
      Reemplazos si el origen es `pack`;
    - "Quitar la fijación": `DELETE …/pins/{id}`; "Fijar otra": abre la ficha del mod en el pack;
    - "Crear un reemplazo": enlace a `/replacements`;
    - **"Ignorar esta dependencia"**: `POST /replacements` con `project_id`, `replacement` vacío,
      `bundle_id` y `contexto`, y `hx-confirm="Se descarta {título} en todos los packs. ¿Seguir?"`.
      Solo aparece para una dependencia (`requested_by` no vacío y el id fuera del pack).
  - Cada acción lleva `contexto`. Con `contexto=problemas`, la ruta aplica la acción, llama a
    `resolve` y responde `_mods.html` en la vista `problemas`. Con `contexto=dialogo`, lo mismo,
    pero responde el diálogo de la Tarea 7. Sin `contexto`, la ruta se comporta como hoy.
  - "Por decidir" (`_pending.html`) lista los `lock.undecided`, cada uno con:
    - ficha resumida (ícono, `mod_link`, descripción en una línea, "Modrinth dice: {lado}" con
      `environment_label`, "dependencia de {títulos}" y la versión);
    - su `select`;
    - "Todos al servidor" y "Todos solo cliente" (`data-set-all="si"` / `"no"`), que marcan
      sin guardar;
    - "Guardar las N decisiones" (el bulk de siempre).

- [ ] **Paso 1: tests que fallan**
  - pestañas con contadores y colores, y una vista inválida que cae en "todos";
  - "Por decidir" con la ficha de cada mod y sus atajos;
  - **"Con problemas"**: para cada tipo de problema, sus acciones y su texto con títulos. Incluye
    "Usar la beta 1.0-beta" con el `version_id` correcto y "Ignorar esta dependencia" con su
    `hx-confirm`, que no aparece para un mod del pack;
  - **una acción con `contexto=problemas`** llama a la acción y después a `resolve`, y responde
    la vista de problemas;
  - **un problema de una resolución vieja** (solo `detail`) se ve con su texto y sin acciones que
    dependan de los campos nuevos;
  - el panel del pack muestra "N por decidir · M con problemas" con enlaces a las pestañas.
- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3:** implementar. `data-set-all` en `app.js` marca los `select` del `form` del botón
  con el valor dado; sin petición.
- [ ] **Paso 4:** pasan, junto con los barridos de roles y de autenticación.
- [ ] **Paso 5: commit.** `feat: pending and problem tabs with an action for every problem`

### Tarea 7: El diálogo de build con problemas

**Archivos:**
- Modificar: `src/routers/web/modpacks.py` (`build_create` y las rutas de acción con
  `contexto=dialogo`), `src/web/templates/modpacks/_decide_dialog.html`
- Tests: `tests/test_web_modpacks.py`

**Interfaces:**
- Consume: `_problem.html` (Tarea 6), `BuildBlocked` (ronda 1) y `resolve`.
- Produce:
  - `BuildBlocked` siempre abre el diálogo (`HX-Retarget: #build-dialog`), con los problemas
    primero (tarjetas con `contexto=dialogo`) y los pendientes después.
  - Una acción con `contexto=dialogo` aplica la acción, **resuelve** (no construye) y responde el
    diálogo recalculado desde ese lock.
  - Sin nada pendiente ni con problemas, el diálogo dice "Todo resuelto" y muestra "Crear build"
    destacado (`POST /modpacks/{id}/builds`).
  - "Decidir y crear build" sigue como en la ronda 1 para los pendientes.

- [ ] **Paso 1: tests que fallan**
  - un `BuildBlocked` con un problema y un pendiente da el diálogo con la tarjeta del problema
    (acciones con `contexto=dialogo`) y el pendiente;
  - **una acción de problema en el diálogo nunca llama a `build_create`**: llama a la acción y a
    `resolve`, y el diálogo recalculado ya no muestra ese problema;
  - con todo resuelto aparece "Todo resuelto" y "Crear build";
  - `/v1` sigue dando 409 con el mismo texto.
- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** pasan.
- [ ] **Paso 5: commit.** `feat: resolve build problems inside the build dialog`

### Tarea 8: Barrido visual y documentos

**Archivos:**
- Modificar: `scripts/visual_sweep.py` (siembra y verificaciones nuevas), `docs/decisions.md`
  (las decisiones del dueño de esta spec, con su test), `docs/roadmap.md` (entrada nueva con lo
  hecho y lo que queda)

- [ ] **Paso 1: barrido.** Verificaciones en Chromium, que fallan la corrida si no se cumplen:
  - un clic simple en un nombre abre `#mod-drawer` con la ficha;
  - un clic en otro mod dentro del panel muestra "← volver";
  - Escape lo cierra;
  - **Ctrl+clic y el clic central** abren `/mods/{id}` (una página o pestaña nueva) y no abren el
    panel;
  - una pestaña cambia la lista sin recargar y la URL tiene `?vista=`;
  - "Todos al servidor" marca los selectores sin hacer ninguna petición;
  - capturas de la ficha, el panel, las tres pestañas y el diálogo con un problema y "Usar la
    beta";
  - ningún error de consola ni violación de CSP (escuchar `securitypolicyviolation`).
- [ ] **Paso 2:** correr `PLAYWRIGHT_CHROMIUM=<headless shell> ./scripts/visual-sweep.sh <salida en el scratchpad>`;
  tiene que dar "0 con problemas".
- [ ] **Paso 3:** documentos. No escribe la entrada del registro de la sesión (la escribe el
  controlador al cerrar).
- [ ] **Paso 4:** gate completo con `./scripts/test-mongo.sh` y `hatch run bundle`.
- [ ] **Paso 5: commit.** `docs: record the mod page and problem actions decisions and extend the sweep`

---

## Después del plan (lo hace el controlador)

1. Repetir con Modrinth real, en la instancia descartable de la ronda 2:
   - un pack con un mod sin release pero con beta (por ejemplo, uno de los que fallan en
     1.21.1);
   - un pack con una dependencia sin versiones;
   - medir los clics para dejar el pack construible.
2. **Con los mods de servidores reales del dueño** (decisión del dueño, 2026-10-05): el simulador
   de Pterodactyl sirve los `/mods` de 2 o 3 de sus servidores, rearmados a partir de los nombres o
   slugs que él indique. Los jars se bajan de Modrinth; de su panel no se lee nada.
3. **Móvil a 360 px** (decisión del dueño): el panel lateral a pantalla completa, las pestañas y la
   ficha.
4. Detener la instancia antes de correr el gate.
5. Agregar los hallazgos al documento de usabilidad como ronda 3.

**Decisiones del dueño en la revisión del plan (2026-10-05):** en "Con problemas" se resuelve de
nuevo después de cada acción; ejecución con subagentes y revisión por tarea; la ronda 3 incluye los
mods de sus servidores reales y el móvil a 360 px.
