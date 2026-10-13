# Panel 0.3.1, tanda A: plan

> **Para quien ejecuta:** cada tarea, test primero y visto fallar; el gate con MongoDB
> (`./scripts/test-mongo.sh`, escrito tal cual) al cerrar cada una. Pasos con casillas (`- [ ]`).

**Objetivo:** el estado de un mod se cambia en su fila (selects), la lista de mods se filtra, las
sugerencias son una pestaña, y auditoría y trabajos son tarjetas expandibles con filtros por nombre
y con conteo.

**Arquitectura:** Jinja2 + htmx sobre las rutas que ya existen; una macro de tarjeta común; los
nombres del objetivo se guardan en el evento al escribirlo; los conteos de auditoría salen de una
consulta `$facet` y los de mods se cuentan en memoria.

**Stack:** FastAPI, Jinja2, htmx 2.0.11, Tailwind 4 (`npm run build:css`), Beanie/MongoDB.

**Spec:** `docs/superpowers/specs/2026-10-13-panel-tanda-a-design.md` (A1–A14; filas
d-7985ee-b21228 … d-7985ee-e8c5fb en `docs/decisions.md`). Se lee junto a este plan.

**Estado (2026-10-13):** las 9 tareas hechas, en `feat/panel-tanda-a`; revisión final hecha, sus 2 importantes arreglados (`16e0c70`) y 8 menores en el roadmap. El registro de ejecución está al final.

## Restricciones globales

- Estructura y layout con clases de Tailwind, sin CSS propio (d-7985ee-e8c5fb).
- Sin `<script>` ni `style=` inline (CSP, d-7985ee-491c83); el JS va en `src/web/static/app.js`.
- Los routers solo inyectan servicios (*audit* `layering`); un método de servicio nuevo que escribe
  suma su fila a `## Acciones y roles` (no se espera ninguno).
- Ningún test abre red fuera de localhost; los de base, `@pytest.mark.network` con `mongo`.
- Textos visibles en español, exactamente los de la spec (§6 para acciones y claves).
- Versiones en el select de fijación: **15** más nuevas más la fijada (A3); auditoría: **50** por
  página, como hoy.
- Commit solo encadenado: `./scripts/check.sh && git commit …`; sin atribución.

## Lo que el agente decide en este plan (reversible e invisible)

- **El nombre del objetivo** (A10) lo pone `AuditService.record` en un solo lugar: si el
  `AuditTarget` ya trae `name`, se guarda tal cual; si no, lo busca por `kind` (pack, servidor,
  reemplazo → título del mod). La spec decía «cada llamada pasa el nombre»; el resultado guardado es
  el mismo y evita tocar ~30 llamadas. Solo las que escriben *después* de borrar el objetivo pasan el
  nombre ellas mismas (`bundle.delete` en `pterodactyl.py`).
- `/jobs/{id}/tr` pasa a devolver el `<summary>` de la tarjeta (se llama `/jobs/{id}/summary`; `tr`
  se borra, no tiene otros usos).

## Review Focus

1. **Un select que falla o un diálogo de baja que se cancela** deja el select en su valor guardado
   (`data-saved`), no en lo elegido. → tests de la tarea 7 y barrido.
2. **Un trabajo activo con su tarjeta abierta** no se cierra cada 2 s al sondear. → test de la tarea 1.
3. **Un objetivo borrado** se sigue leyendo por su nombre de entonces. → test de la tarea 2.
4. **Filtros con un valor desconocido en la URL** (`?lado=xyz`, `?action=foo`) se ignoran sin 422. →
   tests de las tareas 4 y 8.
5. **Un `version_id` o `project_id` que el catálogo no tiene** en el detalle de auditoría se muestra
   crudo, sin error. → test de la tarea 3.

## Tareas, en este orden

| # | Qué | Decisión | Dónde |
|---|---|---|---|
| 1 | Macro de tarjeta expandible; trabajos como tarjetas que cargan su fragmento al abrirse | A8, A13 | `_macros.html`, `activity/_job_*`, `routers/web/activity.py` |
| 2 | `AuditTarget.name`, puesto al escribir | A10 | `model/audit.py`, `services/audit.py`, `dependences.py`, `services/pterodactyl.py` |
| 3 | Auditoría como tarjetas, con acciones y claves en español | A9 | `src/web/audit_labels.py`, `activity/audit.html` |
| 4 | Filtros de auditoría con conteo (`$facet`) e índices | A11, A12 | `database/audit.py`, `services/audit.py`, `routers/web/activity.py`, `audit.html` |
| 5 | Pestaña Sugerencias | A7 | `_mods.html`, `_resolution.html`, `routers/web/modpacks.py` |
| 6 | Selects de lado y canal en la fila; se quita «Excepciones por mod» | A1, A2, A4, A5 | `_mods.html`, `_channel_exceptions.html`, `modpacks.py`, `app.js` |
| 7 | Select de fijación con versiones del catálogo, y aviso con Deshacer | A2, A3, A14 | `ModpackService.mod_list`, `_mods.html`, `modpacks.py`, `app.js` |
| 8 | Barra de filtros de la lista de mods, con conteo | A6, A12 | `src/library/mod_filters.py`, `_mods.html`, `modpacks.py` |
| 9 | Barrido visual, «Se cumple en» de las filas, roadmap y registro | — | `scripts/visual_sweep.py`, `docs/` |

---

### Tarea 1: la tarjeta expandible y los trabajos

**Archivos:** modificar `src/web/templates/_macros.html`, `activity/_jobs_table.html`,
`activity/_job_tr.html` (se renombra `_job_card.html`), `src/routers/web/activity.py:174-187`;
tests en `tests/test_web_activity.py`.

**Interfaces:**
- Produce: `{% call ui.card_list(empty, caption) %}…{% endcall %}` (`<ul>` con `divide-y`, el texto
  `empty` si el bloque no rinde nada) y `{% call(part) ui.card(id, attrs={}) %}` donde
  `part == "summary"` rinde la línea y `part == "body"` el cuerpo. La macro recibe `cols`, las
  columnas de la página como clase de Tailwind escrita entera en la plantilla que llama (p. ej.
  `sm:grid-cols-[8rem_10rem_1fr_minmax(0,1fr)]`; Tailwind solo ve clases literales), y el
  `<summary>` es `grid gap-x-3 {{ cols }}`, apilado bajo `sm`.
- Produce: `GET /jobs/{id}/summary` → solo el `<summary>` (para el sondeo).

- [ ] **Paso 1: tests que fallan**
  - `test_jobs_are_cards_whose_summary_is_the_old_row`: `/jobs` tiene `<details data-job="{id}">`,
    sin `<table>`; el `<summary>` contiene tipo, servidor, estado, paso, pedido por.
  - `test_opening_a_job_card_loads_its_fragment_once`: el `<details>` lleva
    `hx-get="/jobs/{id}/row"` y `hx-trigger="toggle once"`, y el cuerpo, «Abrir página» con
    `href="/jobs/{id}"` y el id.
  - `test_an_active_job_polls_only_its_summary`: el `<summary>` activo lleva
    `hx-get="/jobs/{id}/summary"`, `hx-trigger="every 2s"`, `hx-swap="outerHTML"`; uno terminado no.
  - `test_job_summary_route_answers_the_summary_alone`: `GET /jobs/{id}/summary` empieza con
    `<summary` y no contiene `<details`.
  - Se adaptan `test_active_job_row_keeps_polling`, `test_finished_job_row_stops_polling`,
    `test_finished_row_triggers_a_refresh_and_an_active_one_does_not` a `/summary`.
- [ ] **Paso 2:** `hatch run tests -k "job and (card or summary or polls)"` → FAIL.
- [ ] **Paso 3:** las macros y `_job_card.html`; la ruta `/jobs/{id}/summary` reemplaza a `/tr` con
  el mismo cuerpo de función. `home.html` usa la misma lista.
- [ ] **Paso 4:** los tests pasan; `./scripts/test-mongo.sh` verde.
- [ ] **Paso 5:** `./scripts/check.sh && git commit -m "feat(panel): jobs as expandable cards"`.

### Tarea 2: el nombre del objetivo en el evento

**Archivos:** `src/model/audit.py`, `src/services/audit.py`, `src/dependences.py:71`,
`src/services/pterodactyl.py:593`; tests en `tests/test_audit.py`, `tests/test_startup.py`.

**Interfaces:**
- `AuditTarget.name: Optional[str] = None`.
- `class TargetNames` en `src/services/audit.py`, `__init__(self, modpackDB, pterodactylDB, modrinthDB)`,
  `async def name(self, target: AuditTarget) -> Optional[str]`: `bundle` → `bundle.name`; `server` →
  `server.name` (por id interno); `replacement` → título del proyecto en el catálogo; otro tipo, un
  id que no existe o un error de base → `None` (nunca impide registrar).
- `AuditService.__init__(self, auditDB, names: Optional[TargetNames] = None, clock=utcnow)`;
  `record` completa `target.name` si viene `None` y hay `names`.

- [ ] **Paso 1: tests que fallan** (`mongo`):
  - `test_an_event_keeps_the_target_name_it_had`: pack «Survival» → evento con `target.name ==
    "Survival"`; se renombra el pack → el evento sigue diciendo «Survival».
  - `test_a_name_given_by_the_caller_is_kept`: `AuditTarget(kind="bundle", id=x, name="Test")` con
    el pack ya borrado → `name == "Test"`.
  - `test_an_unknown_target_records_without_a_name`: id inexistente y `kind="job"` → `name is None`,
    el evento se escribe.
  - `test_startup.py`: el cableado nuevo coincide con el constructor (ya lo comprueba al construir).
- [ ] **Paso 2:** `./scripts/test-mongo.sh` → FAIL en esos tres.
- [ ] **Paso 3:** implementar; `dependences.py`: `auditService = providers.Singleton(AuditService,
  auditDatabase, providers.Singleton(TargetNames, modpackDatabase, pterodactylDatabase,
  modrinthDatabase))`; `pterodactyl.py:593` pasa `name=bundle.name`.
- [ ] **Paso 4:** verde con MongoDB.
- [ ] **Paso 5:** commit `feat(audit): events keep their target's name`.

### Tarea 3: la auditoría como tarjetas, en español

**Archivos:** crear `src/web/audit_labels.py`; modificar `src/web/__init__.py` (exponer a Jinja),
`activity/audit.html`, `src/routers/web/activity.py` (`audit_log`), `src/services/audit.py`; tests en
`tests/test_web_activity.py` y `tests/test_audit_labels.py`.

**Interfaces:**
- `ACTIONS: dict[str, tuple[str, str]]` (acción → (texto, área: «Packs», «Builds», «Servidores»,
  «Reemplazos»)), con los 30 textos de la spec §6, literal.
- `DETAILS: dict[str, str]` (clave → etiqueta), las de §6 más las que encuentre el `grep` de cada
  `audit.record` (p. ej. `reason` → «Motivo», `replacement` → «Reemplazo»).
- `def detail_text(key: str, value: Any, names: Mapping[str, str]) -> str`: `project_id`/`version_id`
  → «Título (id)» si `names` lo tiene, si no el id; `server` bool → «sí»/«no»; `downgrade` →
  «19.57 → 19.51, confirmada»; dict o lista → sus valores unidos por «, »; otro → `str(value)`.
- `AuditService.names_for(events) -> dict[str, str]`: una consulta al catálogo por los
  `project_id` y una por los `version_id` de la página (título y `version_number`).

- [ ] **Paso 1: tests que fallan**
  - `test_every_recorded_action_has_a_spanish_text`: recorre `src/` con la regex
    `audit\.record\([^,]+,\s*"([a-z_.]+)"` y exige cada acción en `ACTIONS`.
  - `test_an_audit_card_shows_when_who_what_and_the_target_by_name`: el `<summary>` contiene «Fijó
    una versión» y «Survival», y no `bundle.pin`.
  - `test_an_open_audit_card_translates_the_details_and_ends_with_the_ids`: «Mod» «JEI (J)», «Baja»
    «19.57 → 19.51, confirmada»; al final el id del pack, el `sub` y el `request_id`.
  - `test_an_unknown_key_or_id_is_shown_raw`: `{"foo": 1, "project_id": "ZZZ"}` → «foo» «1»,
    «ZZZ», sin error (Review Focus 5).
  - `test_an_event_without_a_target_name_shows_its_kind_and_id`.
- [ ] **Paso 2:** `hatch run tests -k "audit"` → FAIL.
- [ ] **Paso 3:** implementar con `ui.card_list`/`ui.card` de la tarea 1.
- [ ] **Paso 4:** verde; `./scripts/test-mongo.sh`.
- [ ] **Paso 5:** commit `feat(panel): the audit log as cards, in Spanish`.

### Tarea 4: filtros de auditoría con conteo

**Archivos:** `src/database/audit.py`, `src/services/audit.py`, `src/model/audit.py` (índices en
`Settings`), `src/routers/web/activity.py:189-220`, `activity/audit.html`; tests en
`tests/test_audit_log.py` (base) y `tests/test_web_activity.py`.

**Interfaces:**
- `AuditDatabase.page(*, actor_sub, action, target: Optional[tuple[str, str]], before_id, limit,
  since, until) -> AuditPage` con `AuditPage(events: list[AuditEvent], actors: list[Facet],
  actions: list[Facet], targets: list[Facet])`, `Facet(value: str, label: str, count: int)`:
  - una agregación: `$match` de la ventana de fechas, luego `$facet` con `events` (los tres filtros,
    `before`, orden `at` desc e id desc, `limit`) y un facet por filtro que aplica los *otros dos*
    filtros y agrupa por el suyo (`actor.sub` con su último `actor.name`; `action`; `kind:id` con su
    último `target.name` y `kind`).
  - `targets` se ordena por conteo y se recorta a 200; `actors` y `actions` completos.
- `AuditService.page(...)` lo envuelve (reemplaza a `find`, que se borra si no tiene otros usos).
- La ruta: `target` llega como `kind:id`; un valor que no tiene `:` filtra por `target.name` exacto;
  una `action` que no está en `ACTIONS` se ignora (Review Focus 4).
- Índices de `AuditEvent.Settings.indexes`: `[("at", -1)]`, `[("actor.sub", 1), ("at", -1)]`,
  `[("action", 1), ("at", -1)]`, `[("target.kind", 1), ("target.id", 1), ("at", -1)]`.

- [ ] **Paso 1: tests que fallan**
  - base: `test_each_facet_counts_with_the_other_filters_but_not_its_own`: 3 eventos de Olga (2
    `bundle.pin`, 1 `bundle.create`) y 1 de Pedro (`bundle.pin`); con `actor=olga` → `actions ==
    [pin 2, create 1]`, `actors == [olga 3, pedro 1]`.
  - base: `test_the_page_and_its_facets_come_from_one_query` (un solo `aggregate`, contando
    llamadas sobre la colección).
  - base: `test_paging_with_before_keeps_the_filters`.
  - web: `test_person_and_action_are_selects_with_counts` («Olga (3)», `<optgroup label="Packs">`,
    una opción en 0 con clase de gris y sin `disabled`).
  - web: `test_target_is_an_input_with_a_datalist_of_names_kinds_and_counts` («Survival · pack (31)»
    con `value="bundle:<id>"`).
  - web: `test_an_unknown_action_or_a_plain_target_name_does_not_422`.
  - web: el enlace «Ver anteriores» lleva los filtros.
- [ ] **Paso 2:** FAIL.
- [ ] **Paso 3:** implementar; el formulario lleva `hx-get="/audit" hx-trigger="change"
  hx-select="#audit-list" hx-target="#audit-list" hx-swap="outerHTML" hx-push-url="true"`, y
  `#audit-list` envuelve los filtros, la lista y «Ver anteriores» (los conteos cambian con cada
  filtro); sin JS el botón «Filtrar» sigue.
- [ ] **Paso 4:** verde con MongoDB; `explain` de la consulta de una página con un filtro usa un
  índice (anotar el resultado en el ledger).
- [ ] **Paso 5:** commit `feat(panel): audit filters by name, with counts`.

### Tarea 5: la pestaña Sugerencias

**Archivos:** `modpacks/_mods.html` (TABS), `modpacks/_resolution.html` (sale el recuadro, el
resumen suma «· N sugerencias»), crear `modpacks/_suggestions.html`, `src/routers/web/modpacks.py`
(aceptar `vista=sugerencias`); tests en `tests/test_web_modpacks.py`.

- [ ] **Paso 1: tests que fallan**
  - `test_suggestions_are_a_fourth_tab_with_their_count` (`data-tab="sugerencias"`, el conteo).
  - `test_the_suggestions_tab_lists_each_one_with_who_asks_and_add` («opcional para», «necesario
    para» con shaders, el formulario `hx-post=".../projects"` con `ref`).
  - `test_the_blue_box_is_gone_and_the_summary_links_the_suggestions`.
  - `test_without_a_current_resolution_the_tab_asks_to_resolve`.
  - Se adapta `test_tabs_link_to_each_view_with_their_counts_and_colors`.
- [ ] **Paso 2:** FAIL. **Paso 3:** implementar. **Paso 4:** verde. **Paso 5:** commit
  `feat(panel): suggestions as a tab`.

### Tarea 6: los selects de lado y canal en la fila

**Archivos:** `modpacks/_mods.html`, borrar `modpacks/_channel_exceptions.html` (y su include en
`detail.html`), `src/routers/web/modpacks.py:659-690` (el DELETE de una excepción responde `#mods`
como el POST, sin `HX-Redirect`), `src/web/static/app.js`; tests en `tests/test_web_modpacks.py` y
`tests/test_web_base.py` (el JS).

**Interfaces:**
- Cada select: `<select name="…" data-saved="{valor}" hx-trigger="change" hx-target="#mods"
  hx-swap="outerHTML">`, fuera del `<summary>`, en un `<div class="flex flex-wrap gap-2 px-3 pb-2">` alineado con el título (sangría igual al ícono).
- Lado: `hx-post="/modpacks/{id}/decisions"` con `project_id` oculto y `server` = `yes`/`no`; la
  opción «Por decidir» (`value=""`, `disabled`) solo si `row.side == "admin"`.
- Canal: valores `""` (canal del pack), `beta`, `alpha`. Un select solo puede pedir un método, así que
  el select apunta a `POST …/channel-exceptions` y la ruta
  acepta `channel=""` como quitarla (llama a `channel_exception_unset`). El DELETE se queda para
  `/v1` y lo demás, respondiendo `#mods`.
- Texto de estado bajo el select de canal: el de `_channel_exceptions.html` hoy («en uso: no hay
  una estable más nueva que la instalada» / «sin efecto: ya hay versión en los canales del pack»).
- `app.js`: en `htmx:responseError` y `htmx:sendError`, si `evt.detail.elt` es un `select[data-saved]`,
  `elt.value = elt.dataset.saved`.

- [ ] **Paso 1: tests que fallan**
  - `test_each_row_has_a_side_select_with_its_value_selected` (incluye «Por decidir» solo en
    `admin`).
  - `test_choosing_a_side_decides_and_answers_mods`.
  - `test_each_row_has_a_channel_select_grey_without_an_exception` (clase de gris cuando `""`).
  - `test_choosing_beta_sets_the_exception_and_the_pack_channel_removes_it` (el servicio falso
    registra `channel_exception_set`/`unset`; respuesta con `id="mods"`).
  - `test_the_exception_state_is_written_under_its_select`.
  - `test_the_channels_section_has_no_exception_list`.
  - `test_the_side_buttons_left_the_card` («¿Va en el servidor?» ya no está).
  - `test_web_base.py`: `app.js` restaura `data-saved` en `htmx:responseError` (busca el texto).
- [ ] **Paso 2:** FAIL. **Paso 3:** implementar. **Paso 4:** verde. **Paso 5:** commit
  `feat(panel): side and channel are selects on each mod's row`.

### Tarea 7: el select de fijación y Deshacer

**Archivos:** `src/services/modpack.py` (`ModRow`, `mod_list`), `modpacks/_mods.html`,
`src/routers/web/modpacks.py:580-620`, `modpacks/_pin_confirm.html`, `src/web/static/app.js`; tests en
`tests/test_mod_list.py` (base), `tests/test_web_modpacks.py`.

**Interfaces:**
- `ModRow.pin_options: Optional[list[VersionOption]] = None` (None: el catálogo no tiene la lista) y
  `ModRow.pinned_version: Optional[str] = None` (el `version_id` fijado).
- `PIN_OPTIONS = 15`. En `mod_list`: una llamada a `version_lists_find(ids, bundle.version)` para
  todas las filas y una a `version_find_by_version_ids` para la unión de sus ids; por fila, el filtro
  de `project_versions` (versión de MC, loaders de su tipo, canales del pack **más** su excepción,
  `listed`), orden por `date_published` desc, las 15 primeras más la fijada si no está. Ninguna
  llamada a Modrinth.
- Extraer de `project_versions` la función pura `pin_choices(versions, target, kind, channels) ->
  list[VersionOption]` y usarla en los dos lugares.
- Select: `hx-post="/modpacks/{id}/pins"` con `version_id` y `from="fila"`; la opción «sin fijar»
  (`value=""`) va a la misma ruta, que pasa a aceptar `version_id` vacío (hoy `min_length=1`) y
  entonces llama a `ModpackService.pin_unset`, sin `hx-confirm`. Solo con `from="fila"` la respuesta
  lleva el aviso con Deshacer; los demás llamadores siguen como hoy. Sin `pin_options`: el
  estado actual y una opción `disabled` «ver versiones en la ficha».
- La respuesta de fijar o quitar desde la fila suma, fuera de banda en `#notices`, un aviso «{Mod}
  fijado en {número}» / «{Mod} sin fijar» con un botón «Deshacer» que hace `hx-post` a la misma ruta
  con la versión anterior (`previous` en el formulario: `data-saved`), `hx-target="#mods"`.
  Deshacer hacia una versión más vieja pasa por la confirmación de baja como cualquier otra.
- `app.js`: al cerrarse `#pin-dialog` sin confirmar, el select de ese mod vuelve a `data-saved`.

- [ ] **Paso 1: tests que fallan**
  - base: `test_the_row_offers_the_15_newest_versions_and_the_pinned_one_from_the_catalog` (20
    versiones, fijada la 18.ª más nueva → 16 opciones; `api.many == []` y ninguna llamada a
    `version_list`).
  - base: `test_a_mod_with_a_beta_exception_also_offers_beta_versions`.
  - base: `test_without_the_catalog_s_list_the_row_has_no_options` (`pin_options is None`).
  - web: `test_the_pin_select_marks_the_pinned_version_and_greys_without_a_pin`.
  - web: `test_choosing_a_version_pins_it_and_offers_undo` (el aviso con «Deshacer» y
    `previous`).
  - web: `test_choosing_sin_fijar_unpins_without_a_confirm`.
  - web: `test_a_downgrade_from_the_row_opens_the_pin_dialog` (409 → `HX-Retarget: #pin-dialog`).
  - web: `test_without_options_the_select_points_to_the_card`.
  - web (`test_web_base.py`): `app.js` restaura el select al cerrar `#pin-dialog`.
- [ ] **Paso 2:** FAIL. **Paso 3:** implementar. **Paso 4:** verde con MongoDB. **Paso 5:** commit
  `feat(panel): pin a mod from its row, with undo`.

### Tarea 8: los filtros de la lista de mods

**Archivos:** crear `src/library/mod_filters.py`; modificar `src/routers/web/modpacks.py` (`_mods` y
`GET /modpacks/{id}`), `modpacks/_mods.html`; tests en `tests/test_mod_filters.py` y
`tests/test_web_modpacks.py`.

**Interfaces:**
- `@dataclass(frozen=True) class ModFilters: nombre: str = ""; lado: str = ""; canal: str = "";
  fijacion: str = ""; origen: str = ""` con `@classmethod parse(cls, query: Mapping[str, str])`
  que descarta valores que no son de estos conjuntos (Review Focus 4):
  lado `{"decidir", "servidor", "desactivado"}`; canal `{"pack", "excepcion", "beta", "alpha"}`;
  fijacion `{"fijados", "sin", "baja"}`; origen `{"agregados", "mrpack", "dependencias"}`.
- `def apply(rows: list[ModRow], filters: ModFilters, exceptions: Mapping[str, str]) -> list[ModRow]`.
- `def counts(rows, filters, exceptions) -> dict[str, dict[str, int]]`: por filtro, por valor (con
  `""` = todos), aplicando los demás filtros y no el suyo.
- `nombre`: contiene, comparando `casefold()` sin acentos (`unicodedata.normalize("NFKD")` sin
  marcas combinantes).
- La barra, como las pestañas (`ui.mods_view_link`): `<form action="/modpacks/{id}"
  hx-get="/modpacks/{id}" hx-trigger="change, input changed delay:300ms from:[name=nombre]"
  hx-select="#mods" hx-target="#mods" hx-swap="outerHTML" hx-push-url="true">` con `vista=todos`
  oculto; `modpack_detail` y `_mods` reciben los filtros como `Query` y pasan `ModFilters.parse`.
  Sin ruta nueva. Solo en la vista «todos»; los conteos de las pestañas no cambian.

- [ ] **Paso 1: tests que fallan**
  - `test_each_filter_keeps_its_rows` (uno por filtro, tabla de casos).
  - `test_filters_combine` (fijados y desactivados).
  - `test_counts_respect_the_other_filters_not_their_own`.
  - `test_name_matches_without_case_or_accents` («sodium» encuentra «Sódium»).
  - `test_unknown_values_are_dropped`.
  - web: `test_the_filter_bar_shows_each_option_with_its_count` («con excepción (2)»).
  - web: `test_filtering_answers_mods_and_keeps_the_tab_counts`.
- [ ] **Paso 2:** FAIL. **Paso 3:** implementar. **Paso 4:** verde. **Paso 5:** commit
  `feat(panel): filter the mod list, with counts`.

### Tarea 9: el barrido y los documentos

- [ ] `npm run build:css`, luego `./scripts/visual-sweep.sh` (tal cual). En `scripts/visual_sweep.py`
  sumar: fila con los tres selects (con y sin estado), la barra de filtros con un filtro activo, la
  pestaña Sugerencias, el aviso con Deshacer, auditoría y trabajos cerrados y una tarjeta abierta, a
  360, 768 y 1280 px, claro y oscuro. Ningún desborde horizontal; revisar las capturas.
- [ ] `docs/decisions.md`: cambiar «por construir» de d-7985ee-b21228 … d-7985ee-7d375f por los tests
  que las cumplen; d-7985ee-2f28ce (tres pestañas) anota la enmienda; d-7985ee-e8c5fb, «la revisión
  del diff».
- [ ] `docs/roadmap.md` (i-7985ee-44060c: tanda A hecha en código; F40 cerrada para auditoría y
  trabajos), entrada del registro con `bundle.py new entry`, y el estado de este plan.
- [ ] `./scripts/test-mongo.sh` verde, luego `./scripts/check.sh && git commit -m "docs: close batch A"`.

## Lo que no cubre

El gate no ve cómo se ve (barrido manual) ni el selector nativo de un móvil real: el barrido usa
Chromium a 360 px, no un teléfono. El comportamiento de `<datalist>` varía por navegador (en Safari
de iOS se muestra distinto): sin probar en un dispositivo real.

## Registro de ejecución

Copiado del ledger de la ejecución (en inglés, como se escribió): cada tarea, sus commits y pruebas, y cada decisión tomada con su costo.

```text

Spec: docs/superpowers/specs/2026-10-13-panel-tanda-a-design.md. Branch feat/panel-tanda-a from f598533.
Execution: native (owner, 2026-10-13), with an agent in parallel for the tasks that share no files.

Pre-flight:
- Task 1 → Task 3, 4: `ui.card_list`/`ui.card(cols)` produced by 1, consumed by 3 (audit cards) — same chain, same agent.
- Task 2 → Task 3, 4: `AuditTarget.name` produced by 2, consumed by 3 (summary) and 4 (target facet label) — same chain.
- Task 3 → Task 4: `ACTIONS` (text, area) produced by 3, consumed by 4 (optgroups, unknown action ignored) — same chain.
- Task 5 → 6 → 7 → 8: all edit `modpacks/_mods.html`; 6 and 7 both edit `app.js` and `test_web_base.py` — serial, by the controller.
- Chains 1–4 and 5–8 share no file except possibly `_macros.html` (1 adds macros; 5–8 should not need new ones). Merge by the controller.

Ruling: tasks 1–4 run in a parallel agent in a git worktree outside the repo (scratchpad/wt-audit, branch feat/panel-tanda-a-audit), with a gate script equal to `hatch run gate` using the main env's python — the owner asked to speed up with agents; two chains share no files — cost if wrong: a merge conflict resolved by hand.
Ruling: the agent does not run `scripts/test-mongo.sh` (one disposable MongoDB at a time); the controller runs it after merging the audit branch, and the MongoDB tests of tasks 2 and 4 are seen failing there first by checking out their test commit — cost if wrong: a DB test seen failing later than its code.
Task 5: complete (commits f598533..b230fd5, tests: hatch run tests tests/test_web_modpacks.py → 213 passed; gate 865 passed). Ruling: task-start/task-done expect 'Task N' headings and the plan says 'Tarea N' — the brief is read from the plan directly and ledger lines are written by hand — cost if wrong: none.
Task 6: complete (commits b230fd5..b647fca, tests: hatch run tests tests/test_web_modpacks.py tests/test_web_base.py → 262 passed; gate green). Ruling: the selects sit after the mod's <details> (under the title while closed, under the card body while open), not inside the <summary> — a form control in a <summary> toggles the card and is nested interactive content — cost if wrong: moving one include. Ruling: an empty form value reaches FastAPI as missing, so POST channel-exceptions takes channel as optional and a missing one removes the exception (beta/alpha still validated, anything else 422) — cost if wrong: a malformed request removes an exception, recorded in the audit. Ruling: from a row (no contexto) setting or removing an exception resolves again, like the side select, so its 'en uso' line is current — cost if wrong: one resolve per change.
Task 7: complete (commits b647fca..ddfb90b, tests: scripts/test-mongo.sh → 1296 passed; RED seen: 2 base tests + 5 web + 1 app.js). Ruling: 'sin fijar' arrives as a missing version_id (FastAPI), so POST /pins takes it optional and unpins only with from=fila; without from it stays a 422 — cost if wrong: none for other callers. Ruling: the undo notice is built in _mods.html (not ui.notice: it has no slot for a form, and _macros.html is the parallel agent's) and stays until closed or used; app.js removes it after a successful Deshacer — cost if wrong: a notice left open. Ruling: the 'fijado' and 'baja (piso X)' badges left the summary for the pin select line — cost if wrong: one template move. Note: test_without_the_catalog_s_list_the_row_has_no_options passed before the code (None is the field's default): it guards the behaviour, it did not drive it.
Task 8: complete (commits ddfb90b..7194dc5, tests: hatch run tests tests/test_mod_filters.py tests/test_web_modpacks.py → 244 passed; gate green). Ruling: the name filter keeps at most 100 characters — cost if wrong: none. Ruling: 'Filtrar' only without JS (<noscript>), 'Limpiar' only with a filter on — cost if wrong: one template line. Ruling: actions other than GET keep the filters from HX-Current-URL, as the tab does — cost if wrong: a filtered list shows unfiltered after an action.
Task 1: complete (agent, commit d3f5ade; tests seen failing: 4 in test_web_activity.py; merged in e974bd7, scripts/test-mongo.sh → 1360 passed).
Task 2: complete (agent, commits 554993f tests+stub, 3b8bf12; RED seen by the controller at 554993f with MongoDB: test_an_event_keeps_the_target_name_it_had, test_servers_and_replacements_are_named_from_the_catalog, test_a_lost_race_deletes_the_new_pack_and_keeps_the_other_link; green after merge).
Task 3: complete (agent, commit fc72a3c; RED seen by the agent: test_audit_labels.py import error, 5 web tests).
Task 4: complete (agent, commits 57a7bed tests+stub, 44824f6; RED seen by the controller at 57a7bed with MongoDB: 5 tests in test_audit_log.py; green after merge).
Task 1-4: Ruling (agent): test-only commits carry interface stubs so they pass the gate — cost if wrong: a little non-test code in two commits.
Task 1: Ruling (agent): the /row tests stay (/row still serves the card body and the job page); the /tr tests moved to /summary, which, like /tr, sends no HX-Trigger job-finished — cost if wrong: nothing listens for it on /jobs or home.
Task 2: Ruling (agent): its tests live in tests/test_audit_log.py and tests/test_audit_names.py (tests/test_audit.py tests scripts/audit.py); TargetNames is its own provider in dependences.py; AuditService's clock moved to third position — cost if wrong: none.
Task 2: note (agent): test_a_failing_lookup_is_no_name was written after the code, never seen failing.
Task 3: Ruling (agent): a map in a detail reads as 'name: value' pairs, a bool 'sí'/'no', None '—'; the closed card shows the absolute UTC time (ui.when), not 'hace 5 min' (no days unit in ago) — cost if wrong: a template change.
Task 4: Ruling (agent): the first index is (at, _id) to back the sort; old indexes stay in production as orphans (Beanie does not drop them); a facet's label is the newest event's name; actions not in ACTIONS are not offered — cost if wrong: an index to drop by hand.
Task 4: Ruling (controller): the explain step was not run. MongoDB documents (ASSUMPTION, from memory) that a $facet sub-pipeline cannot use indexes: only the first $match on 'at' can, so the three filter indexes may serve nothing — sent to the final review — cost if wrong: unused indexes cost a little on each write.
Ruling (controller): the merge commit of the audit branch was made before the gate ran; scripts/test-mongo.sh ran right after on that commit, 1360 passed — cost if wrong: none, it is green.
Task 9: complete (commit 666fbc3; visual sweep 194 shots, 0 problems, twice; the second after the 360 px indent fix of the row selects). Ruling: below sm the row selects lose the 4.75rem indent (two per line instead of one) — seen in the sweep — cost if wrong: one class.
Final review: opus reviewer, fresh context, package review-f598533..666fbc3.diff: 0 critical, 2 important, 8 minor.
Final: fixed the name filter swapped with #mods (typed text lost while a request was in flight) — test_filtering_never_replaces_the_name_being_typed RED→GREEN, suite 1362/1362 (scripts/test-mongo.sh).
Final: fixed mod_list read every list's whole version documents — test_the_row_loads_only_its_target_s_versions_and_only_what_the_select_needs RED→GREEN, suite 1362/1362.
Final: minor (deferred): the audit $facet reads the whole date window per page; (actor.sub, at), (action, at), (target.kind, target.id, at) serve nothing (MongoDB docs: $facet does not use indexes) — drop them, page events with a find(), or a default window.
Final: minor (deferred): a write that succeeds before resolve() fails leaves the select restored to the old value although the change is saved.
Final: minor (deferred): 'Deshacer' that opens the downgrade dialog removes the undo notice; cancelling loses the undo.
Final: minor (deferred): the job-started/job-finished refresh of #pack-main drops the mod filters.
Final: minor (deferred): duplicate id job-<id> (card and loaded row); an open active card polls twice, and the inner row keeps polling after closing.
Final: minor (deferred): pages open across the deploy poll /jobs/{id}/tr, now 404, every 2 s until reloaded.
Final: minor (deferred): the Objetivo field shows kind:id once a suggestion is chosen; a name with ':' cannot be filtered by name.
Final: minor (deferred): the row's versions show the number without the channel (spec §1: número y canal); the row adds the exception's channels and the card's picker does not; DELETE channel-exceptions has no panel caller.
Final: Ruling: 'a side back to automatic' (remove a decision) and 'the name filter matches the slug' were set aside by the reviewer — the spec offers only yes/no and 'contiene' on the name; a person gets what they decided — cost if wrong: a new option later.
```
