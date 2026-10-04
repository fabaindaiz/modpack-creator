# Panel web, usabilidad (ronda 1): plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usar superpowers:subagent-driven-development (recomendada)
> o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas
> (`- [ ]`) para seguir el avance.

**Objetivo:** que los mods se reconozcan (ficha), que lo que depende de otra cosa se haga en el
momento, que el estado se refresque y se conserve, y que los avisos se vean donde se actúa.

**Arquitectura:**
- **Funciones puras:** la deducción de loader y versión (`src/library/deduce.py`) y el digest de una
  petición de resolución (`src/library/resolution.py`).
- **Datos:** un documento nuevo, `ModpackResolution` (la última resolución de cada pack), y un
  campo opcional, `ModrinthProject.icon_url`.
- **Servicios:**
  - `ModpackService` arma la lista de mods (`mod_list`), lista las versiones para fijar
    (`project_versions`) y guarda la resolución;
  - `PterodactylService` deduce (`deduction`) y crea el pack desde un servidor
    (`bundle_for_server`).
- **Panel:**
  - plantillas nuevas: `_mods.html`, `_resolution.html` y el diálogo de decisiones;
  - un `HX-Trigger` cuando termina un trabajo;
  - avisos junto al formulario o en la esquina fija.

**Tecnologías:** las de la rama (FastAPI, Jinja2, htmx 2.0.11, Tailwind 4.3.3, Beanie 2), sin
dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-04-panel-usabilidad-design.md`. Hay que leerla junto con
este plan, y manda sobre él. Los hallazgos (F01…) están en
`docs/research/2026-10-04-usabilidad-panel.md`.

## Restricciones globales

- **Lenguaje y capas:**
  - Python 3.12;
  - los routers solo inyectan servicios (`*Service`), nunca un `*Database` ni un `*API`;
  - nada de defaults mutables;
  - todo `Document` va registrado en `init_databases` (`src/model/__init__.py`).
- **Tests:** se escriben primero y se ven fallar.
- **CSP:** sin `<script>` inline, `style=` ni `hx-on`. El comportamiento va por atributos htmx y
  `src/web/static/app.js`.
- **Commits:** el dueño es el **único autor**, sin `Co-Authored-By` ni otra atribución. Conventional
  Commits.
- **Nunca** leer ni editar `.env.yaml` ni `.env.default`. Este lote no agrega variables.
- **Gate:** `./scripts/test-mongo.sh` (`docker` = podman) tiene que pasar. Un commit va encadenado
  al gate: `./scripts/check.sh && git commit …`.
- **Tabla de roles:** todo método de servicio nuevo que escribe o audita lleva su fila en
  `## Acciones y roles` de `docs/decisions.md`, en el mismo commit.
- **Modrinth:** toda llamada nueva o cambiada se contrasta con el openapi v2.7.0 y se prueba en
  `tests/test_api_contracts.py`. Este lote solo reutiliza `project_get_many`, `project_versions` y
  `version_get`.
- **Textos:** el que ve la persona va en español. Las cadenas de Modrinth se escapan siempre
  (autoescape de Jinja). Una URL de ícono solo se usa si pasa `_icon` (https, `cdn.modrinth.com`).
- **Shell:** comandos sin esperar stdin, y archivos editados con herramientas de edición.
- **Procesos:** todo proceso o contenedor que una tarea arranca, lo detiene.

## Foco de revisión

1. **Un ícono del catálogo fuera de `cdn.modrinth.com`** (o `http:`, o con usuario en la URL) se
   muestra como cuadro neutro, nunca como `<img src>`. Test en la Tarea 6.
2. **Modrinth caído o lento al armar la lista de mods:** la página se ve sin los íconos que faltaban,
   en menos del tope (5 s), sin 500 y sin aviso. Test en la Tarea 5.
3. **Un editor que fuerza `link=1&import=1`** en `POST /servers/{id}/modpack` solo crea el pack:
   `bundle_for_server` no se llama y el servidor queda sin vincular. Test en la Tarea 8.
4. **La resolución guardada nunca entra a un build:** `build_create` vuelve a resolver aunque la
   guardada sea vigente, y un digest distinto la marca vieja. El digest no cambia con el orden de
   proyectos ni de claves. Tests en las Tareas 2 y 4.
5. **El refresco tras un trabajo no hace bucle:** la fila de un trabajo activo no manda
   `HX-Trigger`, la terminada sí, y `#server-main` no contiene la fila que consulta. Test en la
   Tarea 9.

---

### Tarea 1: Errores de servicios externos en español, con detalle saneado

**Archivos:**
- Modificar: `src/exceptions.py`, `src/library/api/utils.py`
- Modificar: `src/library/api/client/{modrinth,pterodactyl,loaders}.py` (atributo `SERVICE`)
- Modificar: `src/web/__init__.py` (`WebRoute`, `_notice_response`)
- Modificar: `src/web/templates/_notice.html`, `src/web/templates/_macros.html` (`notice`)
- Tests: `tests/test_http_client.py`, `tests/test_web_base.py`

**Interfaces:**
- Produce:
  - `class UpstreamError(HTTPException)` con `service: str`, `upstream_status: int` y `raw: str`.
    El `detail` es el mismo texto en inglés de hoy, así `/v1` no cambia.
  - `upstream_message(service: str, status: int) -> str`, en `src/web/__init__.py`.
  - `sanitize_detail(text: str) -> str`, en `src/web/__init__.py`.
  - `ui.notice(message, tone="error", detail=None, request_id=None)`. `detail` muestra un
    `<details>` "Ver detalle".
  - Cada aviso lleva `data-notice-tone="<tone>"`, que lee la Tarea 3.

- [ ] **Paso 1: tests que fallan**

```python
# tests/test_http_client.py
async def test_status_error_is_an_upstream_error_naming_the_service() -> None:
    # A client with SERVICE = "Pterodactyl" answering 404: the exception is UpstreamError with
    # service "Pterodactyl", upstream_status 404, and the same English detail as before.
    ...
    assert isinstance(error, UpstreamError) and error.service == "Pterodactyl" and error.upstream_status == 404
    assert error.detail.startswith("Request has failed with status error 404")

# tests/test_web_base.py
def test_upstream_404_reads_in_spanish_with_a_sanitized_detail() -> None:
    # A panel route whose service raises UpstreamError("Pterodactyl", 404, raw="… https://node/x?token=abc …")
    # answers a notice: "Pterodactyl no encontró lo pedido."; never "Request has failed".
    assert "Request has failed" not in visible_text
    assert "token=abc" not in body and "https://node/x" in body   # the query is stripped
    assert "Ver detalle" in body and request_id in body

def test_sanitize_detail_cuts_queries_and_length() -> None:
    assert sanitize_detail("GET https://a.b/c?token=s&x=1 failed") == "GET https://a.b/c failed"
    assert len(sanitize_detail("x" * 900)) == 500
```

- [ ] **Paso 2:** correr `hatch run tests -k "upstream or sanitize"`. Tienen que FALLAR.

- [ ] **Paso 3: implementar**
  - `handle_errors` toma el nombre de `getattr(args[0], "SERVICE", "un servicio externo")` y lanza
    `UpstreamError` para los errores de estado, conexión (502) y timeout (504). El 429 sigue igual.
  - Valores de `SERVICE`: `ModrinthAPI` = "Modrinth", `PterodactylAPI` = "Pterodactyl" y
    `LoaderMetaAPI` = "el servicio de loaders".
  - `upstream_message` por estado:
    - 404 → "{service} no encontró lo pedido.";
    - 401 o 403 → "{service} rechazó la credencial del panel.";
    - 502 o 504 → "{service} no respondió; intenta de nuevo.";
    - el resto → "{service} respondió con un error ({status}).".
  - `WebRoute` atrapa `UpstreamError` **antes** que `HTTPException`. Para estados fuera de
    `NOTICE_STATUSES` (un 400 o un 500 de arriba) también responde un aviso, con el estado `502`.
  - El detalle es `f"{service} · HTTP {status} · {sanitize_detail(raw)}"`.
  - `request_id` sale de la cabecera `X-Request-ID` de la respuesta, o del contexto de log que ya
    usa `src/library/logging.py`.
  - `sanitize_detail` quita `?…` de cada URL (regex `https?://[^\s?]+\?[^\s]*` → la parte antes de
    `?`) y recorta a 500 caracteres.

- [ ] **Paso 4:** los tests pasan. `hatch run tests -k "http_client or web_base"`.
- [ ] **Paso 5: commit.** `fix: show upstream errors in Spanish with a sanitized detail`

### Tarea 2: Funciones puras: deducción, digest e ícono del catálogo

**Archivos:**
- Crear: `src/library/deduce.py`, `tests/test_deduce.py`
- Modificar: `src/library/resolution.py` (`request_digest`), `tests/test_resolution.py`
- Modificar: `src/model/modrinth/project.py` (`icon_url`), `tests/test_modrinth_catalog.py`

**Interfaces:**
- Produce:
  - `Deduction` (dataclass congelada) con:
    - `loader: Optional[str]` y `game_version: Optional[str]`;
    - `identified: int`;
    - `loader_votes: dict[str, int]` y `version_votes: dict[str, int]`.
  - `deduce_target(identified: list[tuple[list[str], list[str]]]) -> Deduction`. Cada tupla es
    (loaders, game_versions) de la versión de un jar.
  - `request_digest(request: ResolveRequest) -> str`: sha256 en hexadecimal.
  - `ModrinthProject.icon_url: Optional[str] = None`.

- [ ] **Paso 1: tests que fallan**

```python
def test_majority_loader_and_version() -> None:
    d = deduce_target([(["fabric"], ["1.21.1"])] * 8 + [(["quilt"], ["1.21.1"])])
    assert (d.loader, d.game_version, d.identified) == ("fabric", "1.21.1", 9)
    assert d.loader_votes == {"fabric": 8, "quilt": 1}

def test_a_tie_decides_nothing() -> None:
    d = deduce_target([(["fabric"], ["1.21.1"]), (["quilt"], ["1.21"])])
    assert d.loader is None and d.game_version is None

def test_only_minecraft_loaders_vote() -> None:
    d = deduce_target([(["iris", "fabric"], ["1.21.1"]), (["datapack"], ["1.21.1"])])
    assert d.loader == "fabric" and "iris" not in d.loader_votes and "datapack" not in d.loader_votes

def test_nothing_identified() -> None:
    assert deduce_target([]) == Deduction(None, None, 0, {}, {})

def test_digest_ignores_order_and_follows_inputs() -> None:
    a = request(projects=["b", "a"], pins={"a": "1", "b": "2"})
    b = request(projects=["a", "b"], pins={"b": "2", "a": "1"})
    assert request_digest(a) == request_digest(b)
    for changed in (request(projects=["a"]), request(pins={"a": "9"}), request(server_decisions={"a": True}),
                    request(channels={"beta"}), request(replacements={"a": None})):
        assert request_digest(changed) != request_digest(a)

# tests/test_modrinth_catalog.py (Mongo)
async def test_icon_url_is_stored_and_absent_reads_as_none() -> None: ...
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - Solo votan los valores de `MinecraftLoaders` (`src/model/utils.py`). Cada jar da un voto a cada
    loader de su lista que sea de Minecraft.
  - El ganador necesita estrictamente más votos que el segundo; si no, `None`. Lo mismo vale para la
    versión.
  - `request_digest`:
    - `projects` va ordenado;
    - `json.dumps(request.model_dump(mode="json") con projects ordenado, sort_keys=True, separators=(",", ":"))`;
    - los `frozenset` (canales) se serializan ordenados.
- [ ] **Paso 4:** los tests pasan; `./scripts/test-mongo.sh` para el de Mongo.
- [ ] **Paso 5: commit.** `feat: deduce a server's loader and version, and digest a resolve request`

### Tarea 3: Avisos donde se actúa, éxito que se cierra, botones ocupados y pasos traducidos

**Archivos:**
- Modificar: `src/web/static/app.js`, `src/web/templates/base.html`, `src/web/templates/_macros.html`
- Modificar: `src/web/assets/app.css`
- Modificar: `src/web/templates/activity/_job_row.html` y `_job_tr.html` (usan `JOB_STEP`)
- Tests: `tests/test_web_base.py`, `tests/test_web_activity.py`

**Interfaces:**
- Consume: `data-notice-tone` (Tarea 1).
- Produce:
  - `ui.JOB_STEP`: un mapa de paso interno a etiqueta. Un paso desconocido se muestra tal cual.
  - El macro `button` agrega `hx-disabled-elt="this"` cuando `attrs` tiene `hx-post`, `hx-put` o
    `hx-delete`.
  - El macro `success(message)` es un aviso de éxito, que se cierra solo.

- [ ] **Paso 1: tests que fallan**

```python
def test_htmx_buttons_disable_themselves_while_busy() -> None:
    # Render ui.button("X", attrs={"hx-post": "/x"}) and ui.button("Y", attrs={"href": "/y"}).
    assert 'hx-disabled-elt="this"' in rendered_x and "hx-disabled-elt" not in rendered_y

def test_job_steps_read_in_spanish() -> None:
    # A job row with step "drift" shows "Comparando", never the raw word in the visible text.

def test_notices_container_is_fixed() -> None:
    # GET / : the #notices element has the fixed classes (fixed, z-50) and is outside <main>.
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - **`JOB_STEP`** cubre los pasos de `src/services/jobs.py`, `update.py` y `update_check.py`. El
    implementador los lista con `grep -n 'step("' src/services/*.py`. Ejemplos: `preflight` →
    "Revisando", `drift` → "Comparando", `log` → "Registrando", `scan` → "Escaneando".
  - **`#notices`:** `fixed inset-x-4 top-3 z-50 space-y-2 sm:left-auto sm:right-6 sm:w-96`, fuera
    de `<main>`.
  - **`app.js`, en `htmx:beforeSwap` para un aviso HTML:**
    - si `evt.detail.requestConfig.elt` está dentro de un `<form>` (o es uno), el destino es el hueco
      `[data-form-notice]` inmediatamente después del form;
    - si el hueco no existe, se crea un `<div data-form-notice>`;
    - si no, el destino es `#notices`.
  - **Cierre:** un `MutationObserver` sobre `document.body` cierra a los 5000 ms todo
    `[data-notice-tone="success"]` nuevo. Nada se evalúa como código.
  - **CSS:** `button.htmx-request { opacity: .6; cursor: progress; }`, con `@layer components` en
    `src/web/assets/app.css`.
- [ ] **Paso 4:** los tests pasan y `npm run build` compila.
- [ ] **Paso 5: commit.** `feat: show notices where the action happened and busy buttons`

### Tarea 4: La última resolución se guarda; un build bloqueado dice qué falta

**Archivos:**
- Modificar: `src/model/modpack/project.py` (`ModpackResolution`), `src/model/modpack/__init__.py`,
  `src/model/__init__.py` (registrar)
- Modificar: `src/database/modpack.py` (`resolution_save` y `resolution_get`)
- Modificar: `src/services/modpack.py` (`resolve`, `build_create`, `resolution`)
- Modificar: `src/exceptions.py` (`BuildBlocked`)
- Modificar: `docs/decisions.md` (texto de la fila de `ModpackService.resolve`)
- Tests: `tests/test_database.py`, `tests/test_builds.py`, `tests/test_resolution_service.py`

**Interfaces:**
- Consume: `request_digest` (Tarea 2).
- Produce:
  - `ModpackResolution(Document)` con:
    - `bundle_id: Annotated[PydanticObjectId, Indexed(unique=True)]`;
    - `resolved_at: datetime`;
    - `request_digest: str`;
    - `lock: Lock`.
  - `ModpackDatabase.resolution_save(resolution) -> None`: upsert por `bundle_id`.
  - `ModpackDatabase.resolution_get(bundle_id) -> Optional[ModpackResolution]`.
  - `ModpackService.resolution(bundle_id) -> Optional[tuple[ModpackResolution, bool]]`, donde el
    `bool` es `stale`, comparando el digest con el de `_request(bundle)` actual.
  - `class BuildBlocked(HTTP_409_CONFLICT)` con `lock: Lock` y `only_undecided: bool`. El `detail` es
    el texto de hoy.

- [ ] **Paso 1: tests que fallan**

```python
async def test_resolve_stores_the_latest_resolution_with_its_digest() -> None:
    lock = await service.resolve(bundle_id)
    saved, stale = await service.resolution(bundle_id)
    assert saved.lock == lock and stale is False

async def test_a_change_to_the_pack_makes_the_resolution_stale() -> None:
    await service.resolve(bundle_id); await service.pin_set(bundle_id, "P1", "v1", EDITOR)
    assert (await service.resolution(bundle_id))[1] is True

async def test_build_create_resolves_again_and_never_reads_the_saved_lock() -> None:
    # Save a resolution whose lock has an entry the catalog no longer gives; build_create's lock
    # is the fresh one, and the saved resolution is replaced by it.

async def test_undecided_build_raises_build_blocked_still_a_409() -> None:
    with pytest.raises(BuildBlocked) as caught: await service.build_create(bundle_id, EDITOR)
    assert caught.value.status_code == 409 and caught.value.only_undecided
    assert [e.project_id for e in caught.value.lock.undecided] == ["P2"]
```

- [ ] **Paso 2:** verlos fallar (los de Mongo, con `./scripts/test-mongo.sh`).
- [ ] **Paso 3: implementar**
  - `resolve` y `build_create` llaman `resolution_save` con el lock que calcularon, después de
    resolver. En `build_create` se guarda también cuando queda bloqueado.
  - `only_undecided = lock.complete and not lock.problems and bool(lock.undecided)`.
  - La fila de `ModpackService.resolve` en la tabla de roles queda así: "Calcula el lock del bundle y
    guarda la última resolución para mostrarla (nunca es entrada de un build); guarda en el catálogo
    lo que trajo de Modrinth. No toca ningún servidor."
- [ ] **Paso 4:** los tests pasan, con `./scripts/test-mongo.sh`.
- [ ] **Paso 5: commit.** `feat: keep each modpack's latest resolution and say why a build is blocked`

### Tarea 5: La lista de mods del pack y las versiones para fijar (servicio)

**Archivos:**
- Modificar: `src/services/modpack.py` (`mod_list`, `project_versions`, dataclasses `ModRow` y
  `VersionOption`)
- Modificar: `src/services/modrinth.py` (`fill_catalog` y `ModrinthService.projects_filled`)
- Modificar: `docs/decisions.md` (filas editor de `ModpackService.mod_list`,
  `ModpackService.project_versions` y `ModrinthService.projects_filled`: escriben el catálogo)
- Tests: `tests/test_mod_list.py` (nuevo; dobles en memoria para la base y la API, como
  `tests/test_resolution_service.py`)

**Interfaces:**
- Consume: `resolution` (Tarea 4) e `icon_url` (Tarea 2).
- Produce:
  - `ModRow` (dataclass congelada), con:
    - `project_id`, `title`, `icon_url: Optional[str]`, `description`, `downloads: int`,
      `categories: list[str]`;
    - `loaders: list[str]` y `game_versions: list[str]`;
    - `supports_loader: Optional[bool]` y `supports_version: Optional[bool]`;
    - `origin: Optional[str]` ("admin", "pack", o None si es dependencia);
    - `required_by: list[str]` (títulos);
    - `side: Optional[str]`, el `server` de la entrada ("include", "exclude", "admin"), o None sin
      resolución;
    - `build_version: Optional[str]` (version_number en el último build);
    - `newer_version: Optional[str]` (de la revisión, si no está fijado);
    - `pinned: bool`;
    - `source_url` e `issues_url` (opcionales).
  - `ModList` (dataclass) con `rows: list[ModRow]`, `resolution: Optional[ModpackResolution]`,
    `stale: bool` e `icons_missing: int`.
  - `ModpackService.mod_list(bundle_id: str, check: Optional[UpdateCheck]) -> ModList`.
  - `fill_catalog(modrinthDB, modrinthAPI, project_ids: list[str], deadline: float) -> dict[str, ModrinthProject]`
    es una función de módulo en `src/services/modrinth.py`. Devuelve los proyectos del catálogo, y
    antes trae en **una** llamada por lotes los que faltan o no tienen `icon_url`. Es best effort:
    un error o el tope se registran y se devuelve lo que había. La usan `mod_list` y
    `ModrinthService.projects_filled(project_ids) -> dict[str, ModrinthProject]` (nuevo, editor,
    con su fila), que la Tarea 9 usa en la página del servidor.
  - `VersionOption` (dataclass) con `version_id`, `version_number`, `channel` y `published: datetime`.
  - `ModpackService.project_versions(bundle_id: str, project_id: str) -> list[VersionOption]`.

- [ ] **Paso 1: tests que fallan**

```python
async def test_rows_join_pack_projects_and_resolved_dependencies() -> None:
    # Pack: P1 (admin). Saved resolution: P1 include, P9 dependency requested_by ["P1"].
    rows = (await service.mod_list(BUNDLE, check=None)).rows
    assert [(r.project_id, r.origin) for r in rows] == [("P1", "admin"), ("P9", None)]
    assert rows[1].required_by == ["Sodium"]               # titles, never ids

async def test_build_version_and_newer_version_from_the_check() -> None:
    # latest build has P1 at "0.8.13"; check says P1 → "0.8.14" (not pinned)
    assert row("P1").build_version == "0.8.13" and row("P1").newer_version == "0.8.14"

async def test_pinned_rows_never_show_a_newer_version() -> None: ...

async def test_missing_icons_are_filled_in_one_batch_call() -> None:
    # catalog has P1 without icon_url and P2 not at all: one project_get_many(["P1","P2"]) call.
    assert api.calls == [("project_get_many", ["P1", "P2"])]

async def test_modrinth_down_still_lists_without_icons() -> None:
    # project_get_many raises (or sleeps past the deadline): rows render, icons None, no exception,
    # and the call returns within the deadline (service built with icon_deadline=0.05).

async def test_support_flags_follow_the_pack() -> None:
    # pack fabric 1.21.1; project loaders ["quilt"], game_versions ["1.21.1"]
    assert (r.supports_loader, r.supports_version) == (False, True)

async def test_versions_for_the_pack_newest_first() -> None:
    opts = await service.project_versions(BUNDLE, "P1")
    assert [o.version_number for o in opts] == ["0.8.14", "0.8.13"]  # only fabric + 1.21.1, by date desc
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - `ModpackService.__init__` suma `icon_deadline: float = 5.0`.
  - `fill_catalog` usa `modrinthAPI.project_get_many` y `modrinthDB.project_upsert_many`, dentro
    de `asyncio.wait_for(…, deadline)`. Cualquier excepción se registra con `logger.warning` y se
    sigue. `mod_list` la llama con `self.icon_deadline`.
  - El orden de filas es: proyectos del pack en su orden y, después, dependencias por título.
  - `supports_loader` acepta lo que acepta la resolución (`Target.loaders` / `LOADER_ACCEPTS`) para
    el tipo del proyecto.
  - `project_versions` usa `modrinthAPI.project_versions(project_id, loaders, [version])`. Con los
    loaders de la resolución, guarda en el catálogo (`version_upsert_many`) y ordena por fecha
    descendente. Incluye **todos** los canales permitidos del pack, que salen de `bundle.channels`.
- [ ] **Paso 4:** los tests pasan.
- [ ] **Paso 5: commit.** `feat: list a modpack's mods with their card data and versions to pin`

### Tarea 6: La página del pack: lista con ficha, resolución y disposición

**Archivos:**
- Crear: `src/web/templates/modpacks/_mods.html`, `src/web/templates/modpacks/_resolution.html`,
  `src/web/templates/modpacks/_versions.html`
- Modificar: `src/web/templates/modpacks/detail.html`, `_search.html`, `_builds.html`
- Borrar: `src/web/templates/modpacks/_projects.html`, `_lock.html` y `_pins.html` (si nada más los
  usa)
- Modificar: `src/routers/web/modpacks.py`
- Tests: `tests/test_web_modpacks.py`

**Interfaces:**
- Consume: `mod_list`, `project_versions` y `resolution` (Tareas 4 y 5); `UpdateCheckService.latest`;
  `PterodactylService.server_find` (servidores vinculados).
- Produce rutas:
  - `GET /modpacks/{id}/projects/{project_id}/versions`, que devuelve el fragmento `<select>`;
  - `POST /modpacks/{id}/resolve` y las rutas de proyectos, decisiones y pins, que ahora responden
    `_mods.html` con la resolución arriba (`#mods`).

- [ ] **Paso 1: tests que fallan**

```python
def test_mods_list_shows_card_data_not_ids() -> None:
    # Row of P1: icon <img src="https://cdn.modrinth.com/…">, "Sodium", "agregado", "Solo cliente",
    # "0.8.13 → 0.8.14"; a dependency row says "dependencia de Sodium". No bare "AANobbMI" visible.

def test_icon_off_the_cdn_is_a_placeholder() -> None:
    # icon_url "http://cdn.modrinth.com/x.png" and "https://evil.example/x.png": no <img> for them.

def test_card_has_support_links_and_lazy_version_picker() -> None:
    # <details> contains "Loaders", "fabric ✓", "no soporta 1.21.1" when false, links to modrinth,
    # and an element with hx-get=".../projects/P1/versions" hx-trigger="toggle once".

def test_versions_fragment_posts_a_pin() -> None:
    # GET …/projects/P1/versions → <select name="version_id"> with "0.8.14 (release, 2026-10-01)";
    # its form posts to /modpacks/{id}/pins with project_id=P1.

def test_saved_resolution_shows_with_time_and_stale_notice() -> None:
    # resolution present and stale → "el pack cambió desde la última resolución".

def test_suggestions_have_titles_and_an_add_button() -> None: ...

def test_add_from_search_marks_the_hit_out_of_band() -> None:
    # POST /projects (HTMX) body contains id="hit-P1" hx-swap-oob="true" and "Ya está".

def test_detail_layout_has_linked_servers_and_renamed_updates_link() -> None:
    # "Servidores vinculados" lists the server linked to the bundle with a link to /servers/{id}#actualizar;
    # header link text is "Buscar actualizaciones de mods".

def test_empty_sections_fold_to_one_line() -> None:
    # no local files → "Archivos locales: ninguno" and no table header "SHA512".
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - **Plantillas** según el §1 y el §5 de la spec: fila, ficha (`<details>`), "Decidir varios"
    arriba con títulos, sugerencias con "Agregar" (un form `POST …/projects` con `ref`), resolución
    con "Resuelto hace …" (`ui.when`) y aviso de vieja.
  - **Disposición:** `xl:grid-cols-3`. Mods a la izquierda (`xl:col-span-2`); Builds, Servidores
    vinculados, Canales y Archivos locales a la derecha.
  - **Elipsis** del número de versión: `truncate max-w-[10rem]` y `title`.
  - **Rutas:** la ruta de detalle pide `updateCheckService.latest` y se lo pasa a `mod_list`. Las
    rutas que hoy devuelven `_projects`, `_lock` o `_pins` devuelven `_mods.html`. El
    `hx-swap-oob` de la búsqueda va en la respuesta de `project_add`.
  - **Formulario de pin:** el selector reemplaza al de id escrito a mano. La ruta `POST …/pins`
    sigue igual.
  - **Avisos de éxito** (`ui.success`, Tarea 3): "Decisiones guardadas" al guardar decisiones y
    "Agregado: *Título*" al agregar un mod.
- [ ] **Paso 4:** los tests pasan. Correr también `tests/test_routes_roles.py` y
  `tests/test_routes_auth.py`.
- [ ] **Paso 5: commit.** `feat: show a modpack's mods as one list with cards`

### Tarea 7: Crear un build decidiendo en el momento, y el siguiente paso

**Archivos:**
- Crear: `src/web/templates/modpacks/_decide_dialog.html`
- Modificar: `src/routers/web/modpacks.py` (`build_create`), `_builds.html`, `_macros.html`
  (`next_step`)
- Tests: `tests/test_web_modpacks.py`

**Interfaces:**
- Consume: `BuildBlocked` (Tarea 4), `decisions_set`, `build_create` y `ui.success` (Tarea 3).
- Produce:
  - `POST /modpacks/{id}/builds`, que acepta campos opcionales `decision.<id>` = `si` | `no`;
  - `ui.next_step(label, href)`.

- [ ] **Paso 1: tests que fallan**

```python
def test_blocked_by_undecided_answers_a_dialog_with_titles() -> None:
    # build_create raises BuildBlocked(only_undecided=True, lock with undecided P2 "FerriteCore"):
    # 200 with <dialog open> listing "FerriteCore" and a submit "Decidir y crear build".

def test_dialog_submit_saves_decisions_then_builds() -> None:
    # POST /builds with decision.P2=si: calls decisions_set({"P2": True}) then build_create, in order.

def test_blocked_by_problems_is_a_notice_not_a_dialog() -> None: ...

def test_a_new_build_offers_simulating_on_linked_servers() -> None:
    # after success: "Build 3 creado" success notice and next_step "Simular en Survival" → /servers/{id}#actualizar.
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - La ruta atrapa `BuildBlocked`:
    - con `only_undecided`, renderiza el diálogo (abierto con el atributo `open`) en
      `#build-dialog`, con `HX-Retarget: #build-dialog` y `HX-Reswap: innerHTML`;
    - si no, relanza y sale como aviso.
  - Si el envío del diálogo vuelve a bloquear, el diálogo se vuelve a mostrar con el motivo dentro.
  - Las decisiones se validan como en `modpack_decide_bulk`, con la misma lógica extraída a una
    función (sin duplicar).
  - El botón dice "Decidir y crear build…" cuando la resolución vigente tiene pendientes.
- [ ] **Paso 4:** los tests pasan.
- [ ] **Paso 5: commit.** `feat: decide pending mods while creating a build`

### Tarea 8: Nuevo modpack para un servidor (deducido, editor crea y operador vincula)

**Archivos:**
- Modificar: `src/services/pterodactyl.py` (`deduction` y `bundle_for_server`)
- Modificar: `src/model/pterodactyl/` (`BundleForServer`)
- Modificar: `src/routers/web/servers.py` (`POST /servers/{id}/modpack`)
- Crear: `src/web/templates/servers/_new_modpack.html`, `servers/_new_modpack_done.html`
- Modificar: `docs/decisions.md` (fila `PterodactylService.bundle_for_server | operador`)
- Tests: `tests/test_bundle_for_server.py` (servicio; Mongo donde haga falta),
  `tests/test_web_servers.py`

**Interfaces:**
- Consume: `deduce_target` (Tarea 2), `ModpackService.bundle_create`, `server_link_bundle` e
  `import_server`.
- Produce:
  - `PterodactylService.deduction(server_id) -> Optional[Deduction]`: None sin escaneo. Lee las
    versiones de los jars identificados del catálogo (`modrinthDB.version_find_by_version_id`, o un
    `find_many`).
  - `@operator_only PterodactylService.bundle_for_server(server_id, name, version, loader, import_mods: bool, actor) -> BundleForServer`.
  - `BundleForServer` (BaseModel) con `bundle: ModpackBundle`, `imported: Optional[ImportResult]` y
    `import_error: Optional[str]`.

- [ ] **Paso 1: tests que fallan**

```python
async def test_operator_creates_links_and_imports_with_audit_in_order() -> None:
    result = await service.bundle_for_server(SERVER, "Creativo", "1.21.1", MinecraftLoaders.neoforge, True, OPERATOR)
    assert [e[0] for e in audit.events] == ["bundle.create", "server.bundle", "server.import"]
    assert result.imported is not None

async def test_editor_is_refused() -> None:
    with pytest.raises(HTTPException) as caught: await service.bundle_for_server(..., EDITOR)
    assert caught.value.status_code == 403

async def test_a_linked_server_is_a_409() -> None: ...

async def test_import_failure_leaves_the_pack_linked_and_says_so() -> None:
    # import_server raises: result.bundle exists, server linked, import_error is Spanish text; no re-raise.

def test_form_prefills_the_deduction_and_explains_it() -> None:
    # server without bundle, deduction fabric/1.21.1 from 8 of 9: inputs prefilled and "8 de 9".

def test_without_scan_offers_scanning_to_deduce() -> None: ...

def test_editor_post_only_creates_the_pack_even_if_link_is_forged() -> None:
    # EDITOR posts link=1&import=1: modpackService.bundle_create called; ptero.bundle_for_server NOT called;
    # body says "Pide a un operador que lo vincule".

def test_operator_post_shows_the_result_and_next_step() -> None:
    # "Mods agregados: 3 (JEI, AppleSkin, FerriteCore)", local file labelled, next_step "Resolver y crear build" → /modpacks/{id}.
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - El servicio crea el bundle a través de `self.modpackDB.bundle_insert` y audita `bundle.create`
    igual que `ModpackService.bundle_create`. Después llama a `self.server_link_bundle(...)` y a
    `self.import_server(...)` (los dos ya auditan).
  - La importación va en `try/except Exception`, con log y `import_error`.
  - La ruta elige con `actor.is_operator`: el operador usa `bundle_for_server` y el editor,
    `modpackService.bundle_create(ModpackBundle(...), actor)`.
  - Nombre: hasta 120 caracteres; versión: hasta 40; loader: `MinecraftLoaders`.
- [ ] **Paso 4:** los tests pasan, incluido el barrido `tests/test_routes_roles.py`.
- [ ] **Paso 5: commit.** `feat: create a modpack from a server's page`

### Tarea 9: La página del servidor: estado vivo, resumen, nombres e importar siempre visible

**Archivos:**
- Modificar: `src/routers/web/activity.py` (`job_row` agrega `HX-Trigger`)
- Modificar: `src/routers/web/servers.py` (`server_detail`)
- Modificar: `src/web/templates/servers/{detail,_compare,_import,_dry_run}.html`,
  `modpacks/updates.html`
- Modificar: `src/services/pterodactyl.py`:
  - `scan` guarda el drift de un servidor vinculado con build (F37);
  - `compare_names(comparison)` → títulos y números de versión;
- Modificar: `src/services/update.py` (el mensaje de F26 en sus dos casos)
- Tests: `tests/test_web_servers.py`, `tests/test_web_activity.py`, `tests/test_server_scan.py`,
  `tests/test_update.py`, `tests/test_web_updates.py`

**Interfaces:**
- Consume: `JOB_STEP`, `next_step` y `upstream_message` (Tareas 1, 3 y 7).
- Produce:
  - `HX-Trigger: job-finished` en `GET /jobs/{id}/row` cuando el trabajo no está activo;
  - `#server-main`, con `hx-trigger="job-finished from:body"`.

- [ ] **Paso 1: tests que fallan**

```python
def test_finished_row_triggers_a_refresh_and_an_active_one_does_not() -> None:
    assert finished.headers["HX-Trigger"] == "job-finished" and "HX-Trigger" not in active.headers

def test_server_main_reloads_itself_and_excludes_the_polling_row() -> None:
    # #server-main has hx-get="/servers/{id}" hx-select="#server-main" hx-trigger="job-finished from:body";
    # the #server-job element is not inside it.

def test_summary_line_says_installed_recommended_and_differences() -> None: ...

def test_comparison_uses_titles_and_version_numbers() -> None:
    # unknown-to-catalog project filled; "el build espera 19.51.0.418", never "EO4u1E2G".

def test_client_only_extra_says_it_does_not_go_on_the_server() -> None: ...

def test_import_is_always_shown_and_says_when_nothing_to_import() -> None:
    # comparison without unknown/foreign extras → "No hay nada que importar" and the button disabled;
    # no scan → "Escanea el servidor para saber qué importar".

def test_drift_pre_is_gone_and_dry_run_jars_fold() -> None: ...

async def test_scan_of_a_linked_server_stores_its_drift() -> None: ...

async def test_unexplained_jar_message_points_to_a_new_build_when_the_pack_has_it() -> None:
    # local file sha in bundle.local_files but not in build.local_files → "crea un build nuevo: el modpack ya lo incluye".
```

- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3: implementar**
  - **Resumen:**
    - el build instalado sale de `JobService.installed_build(server_id) -> Optional[int]` (nuevo,
      de lectura, sin fila de roles). Es el `payload["build"]` de `jobDB.last_real_update(f"server:{id}")`
      cuando su `status == "done"`; si no, None ("sin actualizar desde el panel");
    - las diferencias son `different_version + missing + extra + unknown`.
  - **Comparación:**
    - los nombres se completan con `ModrinthService.projects_filled` (Tarea 5): la ruta lo usa en
      lugar de `projects_by_id` en `_titles` de `servers.py`;
    - las versiones salen de `ModrinthVersion` (catálogo) y, si no están, del nombre del jar;
    - "Sobra" con `server == "exclude"` en el build: "solo cliente: no va en el servidor".
  - **Disposición** (§5 de la spec): el resumen arriba; la comparación a la izquierda
    (`lg:col-span-2`); a la derecha, en orden, Actualizar, Modpack (vincular o el formulario de la
    Tarea 8), Escaneo e Importar.
  - **Página de actualizaciones del pack:** la misma recarga (`#updates-main`).
  - **Después de importar:** `next_step` a la página del pack.
- [ ] **Paso 4:** los tests pasan, con `./scripts/test-mongo.sh`.
- [ ] **Paso 5: commit.** `feat: refresh the server page when a job ends and show names, not ids`

### Tarea 10: Barrido visual, documentos y registro

**Archivos:**
- Modificar: `scripts/visual_sweep.py`: siembra una resolución, una ficha abierta, un servidor sin
  modpack con escaneo, un aviso de formulario y uno de éxito.
- Modificar: `docs/decisions.md` (decisiones nuevas con su "qué lo hace cumplir"),
  `docs/roadmap.md` (la ronda 2), `docs/references.md` (nada nuevo de Modrinth, salvo lo que
  encuentre la tarea), `AGENTS.md` (solo si una invariante cambia: no se espera)
- Modificar: `.claude/logs/agent-changelog.md` (entrada de la sesión, arriba, con un id de
  `bundle.py id s`)

- [ ] **Paso 1: barrido.** Suma a `pages`:
  - `modpack` con `?abrir=P1` o un `<details open>` sembrado;
  - `server-new`, un servidor sin modpack con escaneo;
  - una interacción que envía el form "por slug" con un ref inexistente y comprueba que el aviso
    queda dentro de `[data-form-notice]`;
  - un aviso de éxito que desaparece antes de 6 s.
- [ ] **Paso 2:** correr `PLAYWRIGHT_CHROMIUM=<headless shell> ./scripts/visual-sweep.sh`, que
  termina con "0 con problemas".
- [ ] **Paso 3: documentos.**
  - `docs/decisions.md`: una fila por decisión del dueño de la spec (la ficha, editor crea y
    operador vincula, decidir en el momento, avisos en dos lugares con detalle saneado, resolución
    guardada solo para mostrar), cada una con su test.
  - `docs/roadmap.md`: la ronda 2 de la spec.
- [ ] **Paso 4: gate completo** con `./scripts/test-mongo.sh` y `hatch run bundle`.
- [ ] **Paso 5: commit.** `docs: record the usability round-1 decisions and extend the visual sweep`

---

## Después del plan (lo hace el controlador, no un subagente)

**Ronda 2 de pruebas reales**, en la instancia local descartable del scratchpad (`explore/up.sh`):
1. Repetir los escenarios s01–s10 de la ronda 1.
2. Medir de nuevo la tarea de F36: acciones, cambios de página y callejones sin salida.
3. Agregar a `docs/research/2026-10-04-usabilidad-panel.md` una sección **"Ronda 2"** con:
   - lo que se resolvió (por F…);
   - lo nuevo encontrado;
   - lo aprendido;
   - lo que no hay que repetir.
4. Detener la instancia.
