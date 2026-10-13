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

**Estado (2026-10-13):** escrito; ninguna tarea empezada.

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
